"""FastAPI-independent application adapter for receivable previews."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

import pandas as pd

from receivable_account_validation_service import (
    validate_receivable_settlement_accounts,
)
from receivable_options_service import (
    build_receivable_difference_summary,
    build_safe_receivable_difference_options,
    load_safe_receivable_difference_options,
)
from receivable_persistence_service import (
    DEFAULT_LOCK_POLL_INTERVAL_SECONDS,
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    read_receivable_preview_snapshot,
)
from receivable_preview_service import (
    DIFFERENCE_ACCOUNT_MODE,
    EXACT_MATCH_PATTERN,
    OVERPAYMENT_PATTERN,
    PARTIAL_SETTLEMENT_MODE,
    PARTIAL_SETTLEMENT_PATTERN,
    SHORTAGE_DIFFERENCE_PATTERN,
    build_receivable_fifo_candidates,
    build_receivable_preview_from_fifo,
    parse_receivable_payment_amount,
)
from receivable_query_service import extract_active_receivables


WIRE_PARTIAL_MODE = "partial"
WIRE_DIFFERENCE_ACCOUNT_MODE = "difference_account"

WIRE_EXACT_MATCH_PATTERN = "exact_match"
WIRE_PARTIAL_SETTLEMENT_PATTERN = "partial_settlement"
WIRE_SHORTAGE_DIFFERENCE_PATTERN = "shortage_difference"
WIRE_OVERPAYMENT_PATTERN = "overpayment"

WIRE_MODES = frozenset({WIRE_PARTIAL_MODE, WIRE_DIFFERENCE_ACCOUNT_MODE})
WIRE_PATTERNS = {
    EXACT_MATCH_PATTERN: WIRE_EXACT_MATCH_PATTERN,
    PARTIAL_SETTLEMENT_PATTERN: WIRE_PARTIAL_SETTLEMENT_PATTERN,
    SHORTAGE_DIFFERENCE_PATTERN: WIRE_SHORTAGE_DIFFERENCE_PATTERN,
    OVERPAYMENT_PATTERN: WIRE_OVERPAYMENT_PATTERN,
}

CANDIDATE_FIELDS = {
    "コード": "code",
    "未収ID": "receivable_id",
    "請求日": "billing_date",
    "請求額": "billed_amount",
    "残高": "balance",
    "消込予定": "scheduled_amount",
    "未収科目": "receivable_account",
    "未収補助": "receivable_sub_account",
    "部門": "department",
    "取引先": "customer_name",
    "摘要": "summary",
}

JOURNAL_ROW_FIELDS = {
    "借方科目": "debit_account",
    "貸方科目": "credit_account",
    "貸方補助": "credit_sub_account",
    "部門": "department",
    "金額": "amount",
    "摘要": "summary",
}

_CANDIDATE_AMOUNT_FIELDS = frozenset({"請求額", "残高", "消込予定"})


class ReceivablePreviewApplicationError(Exception):
    """Base error for Preview application validation and lookup failures."""


class ReceivablePreviewValidationError(
    ReceivablePreviewApplicationError,
    ValueError,
):
    """Raised when Preview intent is invalid."""


class ReceivablePreviewCustomerNotFoundError(
    ReceivablePreviewApplicationError,
    LookupError,
):
    """Raised when the customer has no active receivables."""


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _integer(value: Any) -> int:
    converted = pd.to_numeric(
        str(value).replace(",", ""),
        errors="coerce",
    )
    if pd.isna(converted):
        return 0
    return int(converted)


def _settlement_date_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()[:10].replace("/", "-")
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ReceivablePreviewValidationError(
            "settlement_date must be a valid date"
        ) from exc


def _internal_mode(mode: str | None) -> str | None:
    if mode is None:
        return None
    if mode == WIRE_PARTIAL_MODE:
        return PARTIAL_SETTLEMENT_MODE
    if mode == WIRE_DIFFERENCE_ACCOUNT_MODE:
        return DIFFERENCE_ACCOUNT_MODE
    raise ReceivablePreviewValidationError("mode is invalid")


def _wire_mode(mode: Any) -> str | None:
    if mode is None:
        return None
    if mode == PARTIAL_SETTLEMENT_MODE:
        return WIRE_PARTIAL_MODE
    if mode == DIFFERENCE_ACCOUNT_MODE:
        return WIRE_DIFFERENCE_ACCOUNT_MODE
    raise ReceivablePreviewValidationError("internal preview mode is invalid")


def _candidate_dto(candidate: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for source, target in CANDIDATE_FIELDS.items():
        value = candidate.get(source)
        result[target] = (
            _integer(value)
            if source in _CANDIDATE_AMOUNT_FIELDS
            else _text(value)
        )
    return result


def _journal_row_dto(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        target: _integer(row.get(source)) if source == "金額" else _text(
            row.get(source)
        )
        for source, target in JOURNAL_ROW_FIELDS.items()
    }


def _recommendations(
    account_master_snapshot: Any,
    transactions_snapshot: Sequence[Mapping[str, Any]] | None,
    customer_name: str,
    candidates: Sequence[Mapping[str, Any]],
    original_difference: int,
) -> list[dict[str, str]]:
    if original_difference == 0:
        return []
    side = "debit" if original_difference < 0 else "credit"
    default_account = "支払手数料" if side == "debit" else "仮受金"
    if transactions_snapshot is None:
        result = load_safe_receivable_difference_options(
            account_master_snapshot,
            customer_name,
            candidates,
            side,
            default_account,
        )
    else:
        result = build_safe_receivable_difference_options(
            account_master_snapshot,
            copy.deepcopy(list(transactions_snapshot)),
            customer_name,
            copy.deepcopy(list(candidates)),
            side,
            default_account,
        )
    return copy.deepcopy(result["recommended_difference_accounts"])


def _available_modes(original_difference: int) -> list[str]:
    if original_difference == 0:
        return []
    if original_difference < 0:
        return [WIRE_PARTIAL_MODE, WIRE_DIFFERENCE_ACCOUNT_MODE]
    return [WIRE_DIFFERENCE_ACCOUNT_MODE]


def build_receivable_preview_application_result(
    receivables_directory: str,
    *,
    ledger_revision: str,
    customer_name: str,
    settlement_date: Any,
    payment_amount: Any,
    receipt_account: str,
    mode: str | None = None,
    difference_account: str | None = None,
    difference_summary: str | None = None,
    account_master_snapshot: Any,
    transactions_snapshot: Sequence[Mapping[str, Any]] | None = None,
    lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    lock_poll_interval_seconds: float = DEFAULT_LOCK_POLL_INTERVAL_SECONDS,
) -> dict[str, Any]:
    """Build a revision-safe, read-only, JSON-safe receivable Preview."""

    try:
        snapshot = read_receivable_preview_snapshot(
            receivables_directory,
            expected_revision=ledger_revision,
            lock_timeout_seconds=lock_timeout_seconds,
            lock_poll_interval_seconds=lock_poll_interval_seconds,
        )
    except ValueError as exc:
        raise ReceivablePreviewValidationError(str(exc)) from exc

    normalized_customer = str(customer_name or "").strip()
    active = extract_active_receivables(snapshot.current_df)
    if (
        not normalized_customer
        or not active["得意先名"].eq(normalized_customer).any()
    ):
        raise ReceivablePreviewCustomerNotFoundError(normalized_customer)

    parsed_amount = parse_receivable_payment_amount(payment_amount)
    if parsed_amount is None:
        raise ReceivablePreviewValidationError(
            "payment_amount must be greater than zero"
        )
    normalized_date = _settlement_date_text(settlement_date)
    internal_mode = _internal_mode(mode)

    explicit_difference_mode = mode == WIRE_DIFFERENCE_ACCOUNT_MODE
    validate_receivable_settlement_accounts(
        account_master_snapshot,
        receipt_account=receipt_account,
        difference_account=difference_account,
        difference_required=explicit_difference_mode,
    )

    fifo_result = build_receivable_fifo_candidates(
        snapshot.current_df,
        normalized_customer,
        parsed_amount,
    )
    original_difference = int(fifo_result["difference"])
    target_candidates = copy.deepcopy(fifo_result["target_candidates"])
    recommendations = _recommendations(
        account_master_snapshot,
        transactions_snapshot,
        normalized_customer,
        target_candidates,
        original_difference,
    )

    if original_difference > 0 and mode is None and not str(
        difference_account or ""
    ).strip():
        return {
            "ledger_revision": snapshot.ledger_revision,
            "customer_name": normalized_customer,
            "settlement_date": normalized_date,
            "payment_amount": parsed_amount,
            "receipt_account": str(receipt_account).strip(),
            "total_receivable_balance": int(
                fifo_result["total_receivable_balance"]
            ),
            "original_difference": original_difference,
            "target_total": int(fifo_result["total_receivable_balance"]),
            "difference": original_difference,
            "pattern": WIRE_OVERPAYMENT_PATTERN,
            "mode": None,
            "available_modes": _available_modes(original_difference),
            "difference_account_required": True,
            "preview_complete": False,
            "source_candidates": [
                _candidate_dto(candidate) for candidate in target_candidates
            ],
            "rows": [],
            "recommended_difference_accounts": recommendations,
        }

    difference_required = original_difference > 0 or (
        original_difference < 0
        and internal_mode == DIFFERENCE_ACCOUNT_MODE
    )
    if difference_required and not explicit_difference_mode:
        validate_receivable_settlement_accounts(
            account_master_snapshot,
            receipt_account=receipt_account,
            difference_account=difference_account,
            difference_required=True,
        )

    effective_summary = difference_summary
    if difference_required and difference_summary is None:
        side = "debit" if original_difference < 0 else "credit"
        effective_summary = build_receivable_difference_summary(
            normalized_customer,
            side,
        )

    try:
        preview = build_receivable_preview_from_fifo(
            fifo_result,
            normalized_customer,
            parsed_amount,
            normalized_date,
            receipt_account,
            internal_mode,
            difference_account,
            effective_summary,
        )
    except ValueError as exc:
        raise ReceivablePreviewValidationError(str(exc)) from exc

    pattern = WIRE_PATTERNS[preview["pattern"]]
    return {
        "ledger_revision": snapshot.ledger_revision,
        "customer_name": normalized_customer,
        "settlement_date": normalized_date,
        "payment_amount": parsed_amount,
        "receipt_account": str(receipt_account).strip(),
        "total_receivable_balance": int(
            preview["total_receivable_balance"]
        ),
        "original_difference": original_difference,
        "target_total": int(preview["target_total"]),
        "difference": int(preview["difference"]),
        "pattern": pattern,
        "mode": _wire_mode(preview["mode"]),
        "available_modes": _available_modes(original_difference),
        "difference_account_required": (
            pattern in {
                WIRE_SHORTAGE_DIFFERENCE_PATTERN,
                WIRE_OVERPAYMENT_PATTERN,
            }
        ),
        "preview_complete": True,
        "source_candidates": [
            _candidate_dto(candidate)
            for candidate in preview["source_candidates"]
        ],
        "rows": [_journal_row_dto(row) for row in preview["rows"]],
        "recommended_difference_accounts": recommendations,
    }

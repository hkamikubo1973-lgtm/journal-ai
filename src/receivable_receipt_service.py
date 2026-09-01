"""Secure, read-only access to durable receivable settlement receipts."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

from receivable_persistence_service import (
    DEFAULT_LOCK_POLL_INTERVAL_SECONDS,
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    SETTLEMENT_RECEIPT_SCHEMA_VERSION,
    calculate_idempotency_key_hash,
    read_settlement_receipt_when_ready,
)


RECEIPT_REF_PATTERN = re.compile(r"[0-9a-f]{64}")
SETTLEMENT_REQUIRED_FIELDS = frozenset(
    {
        "settlement_id",
        "settlement_date",
        "customer_name",
        "payment_amount",
        "target_total",
        "difference",
        "source_candidates",
        "rows",
        "created_at",
    }
)
SOURCE_CANDIDATE_REQUIRED_FIELDS = frozenset(
    {
        "コード",
        "未収ID",
        "請求日",
        "請求額",
        "残高",
        "消込予定",
        "未収科目",
        "未収補助",
        "部門",
        "取引先",
        "摘要",
    }
)
ROW_REQUIRED_FIELDS = frozenset(
    {"借方科目", "貸方科目", "貸方補助", "部門", "金額", "摘要"}
)


class ReceivableReceiptServiceError(Exception):
    """Base error for secure receipt domain reads."""


class ReceivableReceiptValidationError(ReceivableReceiptServiceError):
    """Raised when a durable receipt's settlement DTO is malformed."""


class ReceivableReceiptReferenceError(
    ReceivableReceiptValidationError, ValueError
):
    """Raised when an opaque receipt reference is malformed."""


class ReceivableReceiptSettlementConflictError(ReceivableReceiptValidationError):
    """Raised when a requested settlement ID does not match the receipt."""


@dataclass(frozen=True)
class ValidatedReceivableSettlementReceipt:
    """Validated receipt data copied away from persistence internals."""

    receipt_ref: str
    schema_version: int
    settlement_id: str
    transaction_id: str
    request_hash: str
    current_after_hash: str
    history_after_hash: str
    committed_at: str
    receipt_hash: str
    settlement: dict[str, Any]


def build_receipt_ref(idempotency_key: str) -> str:
    """Build the durable opaque reference using the shared hash helper."""

    return calculate_idempotency_key_hash(idempotency_key)


def _validation_error(message: str) -> ReceivableReceiptValidationError:
    return ReceivableReceiptValidationError(message)


def _require_fields(
    value: Mapping[str, Any], required: frozenset[str], label: str
) -> None:
    missing = sorted(required.difference(value))
    if missing:
        raise _validation_error(
            f"{label} is missing fields: " + ", ".join(missing)
        )


def _require_string(
    value: Any, label: str, *, allow_empty: bool = False
) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise _validation_error(f"{label} must be a non-empty string")
    return value


def _require_integer(
    value: Any, label: str, *, positive: bool = False
) -> int:
    if type(value) is not int or (positive and value <= 0):
        suffix = "a positive integer" if positive else "an integer"
        raise _validation_error(f"{label} must be {suffix}")
    return value


def _validate_json_safe(value: Any, label: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise _validation_error(f"{label} must be JSON-safe") from exc


def _validate_source_candidates(value: Any, customer_name: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise _validation_error("settlement.source_candidates must be a list")

    for index, candidate in enumerate(value):
        label = f"settlement.source_candidates[{index}]"
        if not isinstance(candidate, dict):
            raise _validation_error(f"{label} must be an object")
        _require_fields(candidate, SOURCE_CANDIDATE_REQUIRED_FIELDS, label)
        for field in ("コード", "未収ID", "請求日", "未収科目", "取引先"):
            _require_string(candidate[field], f"{label}.{field}")
        for field in ("未収補助", "部門", "摘要"):
            _require_string(
                candidate[field], f"{label}.{field}", allow_empty=True
            )
        _require_integer(candidate["請求額"], f"{label}.請求額", positive=True)
        _require_integer(candidate["残高"], f"{label}.残高", positive=True)
        _require_integer(
            candidate["消込予定"], f"{label}.消込予定", positive=True
        )
        if candidate["取引先"] != customer_name:
            raise _validation_error(
                f"{label}.取引先 does not match settlement.customer_name"
            )
    return value


def _validate_rows(value: Any) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise _validation_error("settlement.rows must be a list")

    for index, row in enumerate(value):
        label = f"settlement.rows[{index}]"
        if not isinstance(row, dict):
            raise _validation_error(f"{label} must be an object")
        _require_fields(row, ROW_REQUIRED_FIELDS, label)
        for field in ("借方科目", "貸方科目"):
            _require_string(row[field], f"{label}.{field}")
        for field in ("貸方補助", "部門", "摘要"):
            _require_string(row[field], f"{label}.{field}", allow_empty=True)
        _require_integer(row["金額"], f"{label}.金額", positive=True)
    return value


def _validate_settlement(
    settlement: Any, top_level_settlement_id: str
) -> dict[str, Any]:
    if not isinstance(settlement, dict):
        raise _validation_error("receipt.settlement must be an object")
    _validate_json_safe(settlement, "receipt.settlement")
    _require_fields(settlement, SETTLEMENT_REQUIRED_FIELDS, "settlement")

    nested_settlement_id = _require_string(
        settlement["settlement_id"], "settlement.settlement_id"
    )
    if nested_settlement_id != top_level_settlement_id:
        raise ReceivableReceiptSettlementConflictError(
            "receipt settlement_id does not match settlement.settlement_id"
        )

    settlement_date = _require_string(
        settlement["settlement_date"], "settlement.settlement_date"
    )
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", settlement_date) is None:
        raise _validation_error("settlement.settlement_date must be an ISO date")
    try:
        date.fromisoformat(settlement_date)
    except ValueError as exc:
        raise _validation_error(
            "settlement.settlement_date must be an ISO date"
        ) from exc

    customer_name = _require_string(
        settlement["customer_name"], "settlement.customer_name"
    )
    payment_amount = _require_integer(
        settlement["payment_amount"],
        "settlement.payment_amount",
        positive=True,
    )
    target_total = _require_integer(
        settlement["target_total"], "settlement.target_total", positive=True
    )
    difference = _require_integer(
        settlement["difference"], "settlement.difference"
    )
    candidates = _validate_source_candidates(
        settlement["source_candidates"], customer_name
    )
    _validate_rows(settlement["rows"])

    if target_total != sum(candidate["消込予定"] for candidate in candidates):
        raise _validation_error(
            "settlement.target_total does not match source candidates"
        )
    if difference != payment_amount - target_total:
        raise _validation_error(
            "settlement.difference does not match payment and target total"
        )

    created_at = _require_string(
        settlement["created_at"], "settlement.created_at"
    )
    try:
        datetime.fromisoformat(created_at)
    except ValueError as exc:
        raise _validation_error("settlement.created_at must be ISO datetime") from exc

    return copy.deepcopy(settlement)


def read_receivable_settlement_receipt(
    receivables_directory: str | Path,
    receipt_ref: str,
    expected_settlement_id: str | None = None,
    *,
    lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    lock_poll_interval_seconds: float = DEFAULT_LOCK_POLL_INTERVAL_SECONDS,
) -> ValidatedReceivableSettlementReceipt:
    """Securely load and deeply validate one immutable durable receipt."""

    if not isinstance(receipt_ref, str) or RECEIPT_REF_PATTERN.fullmatch(
        receipt_ref
    ) is None:
        raise ReceivableReceiptReferenceError(
            "receipt_ref must be lowercase SHA-256 hex"
        )
    if expected_settlement_id is not None and (
        not isinstance(expected_settlement_id, str) or not expected_settlement_id
    ):
        raise ReceivableReceiptReferenceError(
            "expected_settlement_id must be a non-empty string"
        )

    loaded = read_settlement_receipt_when_ready(
        receivables_directory,
        receipt_ref,
        lock_timeout_seconds=lock_timeout_seconds,
        lock_poll_interval_seconds=lock_poll_interval_seconds,
    )
    receipt = loaded.receipt
    if receipt["schema_version"] != SETTLEMENT_RECEIPT_SCHEMA_VERSION:
        raise ReceivableReceiptValidationError(
            "unsupported settlement receipt schema version"
        )

    top_level_settlement_id = _require_string(
        receipt["settlement_id"], "receipt.settlement_id"
    )
    if (
        expected_settlement_id is not None
        and expected_settlement_id != top_level_settlement_id
    ):
        raise ReceivableReceiptSettlementConflictError(
            "expected settlement_id does not match receipt"
        )
    settlement = _validate_settlement(
        receipt["settlement"], top_level_settlement_id
    )

    return ValidatedReceivableSettlementReceipt(
        receipt_ref=receipt_ref,
        schema_version=receipt["schema_version"],
        settlement_id=top_level_settlement_id,
        transaction_id=receipt["transaction_id"],
        request_hash=receipt["request_hash"],
        current_after_hash=receipt["current_after_hash"],
        history_after_hash=receipt["history_after_hash"],
        committed_at=receipt["committed_at"],
        receipt_hash=loaded.receipt_hash,
        settlement=settlement,
    )

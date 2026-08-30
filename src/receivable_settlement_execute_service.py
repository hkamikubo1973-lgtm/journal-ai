"""Single-lock orchestration for durable receivable settlement execution."""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from receivable_persistence_service import (
    DEFAULT_LOCK_POLL_INTERVAL_SECONDS,
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    ReceivableIdempotentTransactionResult,
    ReceivableLedgerConflictError,
    _commit_receivable_ledger_transaction_with_receipt_locked,
    _idempotent_result_from_loaded_receipt,
    _lookup_idempotency_receipt_locked,
    _read_receivable_ledger_snapshot_locked,
    _recover_receivable_ledger_transactions_locked,
    atomic_write_bytes,
    build_empty_receivable_history_bytes,
    calculate_idempotency_key_hash,
    calculate_request_hash,
    load_receivable_history_read_only,
    receivable_ledger_lock,
    resolve_receivable_ledger_paths,
)
from receivable_preview_service import parse_receivable_payment_amount
from receivable_settlement_service import (
    ReceivableSettlementValidationError,
    build_receivable_settlement_plan,
)


def _normalized_date_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()[:10].replace("/", "-")
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ReceivableSettlementValidationError(
            "settlement_date must be a valid date"
        ) from exc


def build_receivable_execute_request_payload(
    *,
    preview_revision: str,
    customer_name: str,
    settlement_date: Any,
    payment_amount: Any,
    receipt_account: str,
    mode: str | None,
    difference_account: str | None,
    difference_summary: str | None,
) -> dict[str, Any]:
    """Build the canonical JSON-safe intent hashed by B3-B idempotency."""

    if not isinstance(preview_revision, str) or re.fullmatch(
        r"[0-9a-f]{64}", preview_revision
    ) is None:
        raise ReceivableSettlementValidationError(
            "preview_revision must be lowercase SHA-256 hex"
        )
    parsed_amount = parse_receivable_payment_amount(payment_amount)
    if parsed_amount is None:
        raise ReceivableSettlementValidationError(
            "payment_amount must be greater than zero"
        )
    return {
        "customer_name": customer_name,
        "settlement_date": _normalized_date_text(settlement_date),
        "payment_amount": parsed_amount,
        "receipt_account": receipt_account,
        "mode": mode,
        "difference_account": difference_account,
        "difference_summary": difference_summary,
        "preview_revision": preview_revision,
    }


def _new_settlement_id() -> str:
    return uuid.uuid4().hex


def _new_created_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _initialize_missing_history_locked(
    receivables_directory: str | Path,
) -> bytes:
    """Create formal empty history bytes; caller must hold the ledger lock."""

    paths = resolve_receivable_ledger_paths(receivables_directory)
    if paths.history_path.exists():
        raise ReceivableLedgerConflictError(
            "history target appeared after the settlement snapshot"
        )
    expected = build_empty_receivable_history_bytes()
    atomic_write_bytes(paths.history_path, expected)
    loaded = load_receivable_history_read_only(paths.history_path)
    if loaded.raw_bytes != expected:
        raise ReceivableLedgerConflictError(
            "initialized history bytes do not match the formal empty ledger"
        )
    return loaded.raw_bytes


def execute_receivable_settlement(
    receivables_directory: str | Path,
    *,
    idempotency_key: str,
    preview_revision: str,
    customer_name: str,
    settlement_date: Any,
    payment_amount: Any,
    receipt_account: str,
    mode: str | None = None,
    difference_account: str | None = None,
    difference_summary: str | None = None,
    lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    lock_poll_interval_seconds: float = DEFAULT_LOCK_POLL_INTERVAL_SECONDS,
) -> ReceivableIdempotentTransactionResult:
    """Execute one settlement under exactly one receivable ledger lock."""

    request_payload = build_receivable_execute_request_payload(
        preview_revision=preview_revision,
        customer_name=customer_name,
        settlement_date=settlement_date,
        payment_amount=payment_amount,
        receipt_account=receipt_account,
        mode=mode,
        difference_account=difference_account,
        difference_summary=difference_summary,
    )
    idempotency_key_hash = calculate_idempotency_key_hash(idempotency_key)
    request_hash = calculate_request_hash(request_payload)

    with receivable_ledger_lock(
        receivables_directory,
        timeout_seconds=lock_timeout_seconds,
        poll_interval_seconds=lock_poll_interval_seconds,
    ):
        _recover_receivable_ledger_transactions_locked(receivables_directory)
        receipt_path, existing = _lookup_idempotency_receipt_locked(
            receivables_directory,
            idempotency_key_hash,
            request_hash,
        )
        if existing is not None:
            return _idempotent_result_from_loaded_receipt(
                existing,
                receipt_path,
                replayed=True,
                workspace_cleaned=True,
                recovered=False,
            )

        snapshot = _read_receivable_ledger_snapshot_locked(
            receivables_directory,
            history_missing_as_empty=True,
        )
        if snapshot.ledger_revision != preview_revision:
            raise ReceivableLedgerConflictError(
                "current.csv revision no longer matches the preview"
            )

        settlement_id = _new_settlement_id()
        created_at = _new_created_at()
        plan = build_receivable_settlement_plan(
            snapshot.current_df,
            snapshot.history_df,
            customer_name=customer_name,
            settlement_date=settlement_date,
            payment_amount=payment_amount,
            receipt_account=receipt_account,
            mode=mode,
            difference_account=difference_account,
            difference_summary=difference_summary,
            settlement_id=settlement_id,
            created_at=created_at,
        )

        history_before_bytes = snapshot.history_raw_bytes
        if history_before_bytes is None:
            history_before_bytes = _initialize_missing_history_locked(
                receivables_directory
            )

        return _commit_receivable_ledger_transaction_with_receipt_locked(
            receivables_directory,
            settlement_id,
            settlement_id=settlement_id,
            idempotency_key_hash=idempotency_key_hash,
            request_hash=request_hash,
            settlement_response=plan.settlement,
            current_before_bytes=snapshot.current_raw_bytes,
            current_after_bytes=plan.current_after_bytes,
            history_before_bytes=history_before_bytes,
            history_after_bytes=plan.history_after_bytes,
        )

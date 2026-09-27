"""Minimal maintenance facts from the existing read-only Web services."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from ai_context_envelope import ContextEnvelope, make_context_envelope
from events_engine import EVENTS_PATH, InvalidEventsCsv
from journal_master_service import load_journal_masters
from receivable_cleanup_service import summarize_receivable_cleanup_rows
from receivable_persistence_service import (
    DEFAULT_RECEIVABLES_DIRECTORY,
    ReceivableLedgerError,
    ReceivableLedgerLockTimeout,
    ReceivableLedgerMissingError,
    ReceivableLedgerRecoveryRequired,
    read_receivable_current_snapshot_when_ready,
)
from receivable_query_service import build_receivable_summary
from schedule_ai_context_provider import build_schedule_ai_context
from system_settings import load_system_settings


def _master_context() -> dict[str, Any]:
    try:
        snapshot = load_journal_masters()
    except (OSError, ValueError, KeyError, TypeError):
        return {"status": "unavailable"}

    diagnostics = snapshot["diagnostics"]
    return {
        "status": "ready",
        "account_count": diagnostics["account_count"],
        "selectable_account_count": diagnostics["selectable_account_count"],
        "unselectable_account_count": diagnostics["unselectable_account_count"],
        "sub_account_count": diagnostics["sub_account_count"],
        "department_count": diagnostics["department_count"],
        "sub_account_relation_count": diagnostics["sub_account_relation_count"],
        "duplicate_account_name_count": len(diagnostics["duplicate_account_names"]),
        "duplicate_sub_code_count": len(diagnostics["duplicate_sub_codes"]),
        "duplicate_sub_account_relation_key_count": len(
            diagnostics["duplicate_sub_account_relation_keys"]
        ),
        "invalid_sub_account_relation_row_count": len(
            diagnostics["invalid_sub_account_relation_rows"]
        ),
        "warnings": list(diagnostics["warnings"]),
        "fiscal_year": dict(snapshot["system"]),
    }


def _receivable_context(directory: Path) -> dict[str, Any]:
    try:
        snapshot = read_receivable_current_snapshot_when_ready(directory)
    except ReceivableLedgerMissingError:
        return {"status": "missing"}
    except ReceivableLedgerLockTimeout:
        return {"status": "busy"}
    except ReceivableLedgerRecoveryRequired:
        return {"status": "recovery_required"}
    except ReceivableLedgerError:
        return {"status": "invalid"}

    summary = build_receivable_summary(snapshot.current_df)
    cleanup = summarize_receivable_cleanup_rows(snapshot.current_df)
    return {
        "status": "ready",
        "settlement_available": snapshot.settlement_available,
        "customer_count": summary["customer_count"],
        "outstanding_count": summary["outstanding_count"],
        "outstanding_balance": summary["outstanding_balance"],
        "current_count": cleanup["current_count"],
        "cleanup_target_count": cleanup["cleanup_target_count"],
        "remaining_count": cleanup["remaining_count"],
    }


def _schedule_context(path: Path, now: datetime) -> dict[str, Any]:
    try:
        data = build_schedule_ai_context(path, now=now)["data"]
    except (InvalidEventsCsv, OSError):
        return {"status": "unavailable"}
    return {
        "status": "ready",
        "total_count": data["total_count"],
        "active_count": data["active_count"],
        "stopped_count": data["stopped_count"],
        "notification_count": data["notification_count"],
    }


def build_maintenance_ai_context(
    *,
    receivables_directory: Path = DEFAULT_RECEIVABLES_DIRECTORY,
    events_path: Path = EVENTS_PATH,
    now: datetime | None = None,
) -> ContextEnvelope:
    """Return counts and safe diagnostics without exposing source rows or paths.

    The receivable snapshot uses the same ledger lock as the existing Web summary.
    """
    now = now if now is not None else datetime.now().astimezone()
    settings = load_system_settings()
    return make_context_envelope("maintenance", {
        "masters": _master_context(),
        "receivables": _receivable_context(Path(receivables_directory)),
        "schedule": _schedule_context(Path(events_path), now),
        "output_folder_configured": bool(settings["csv_export_dir"].strip()),
    }, now=now)

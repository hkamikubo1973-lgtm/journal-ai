"""Legacy-compatible cleanup of completed rows in current.csv only."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from receivable_persistence_service import (
    DEFAULT_LOCK_POLL_INTERVAL_SECONDS,
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    LEDGER_HEALTH_READY,
    ReceivableLedgerRecoveryRequired,
    _inspect_receivable_ledger_health_locked,
    atomic_write_bytes,
    load_current_receivables_read_only,
    read_receivable_current_snapshot_when_ready,
    receivable_ledger_lock,
    resolve_receivable_ledger_paths,
)
from receivable_settlement_service import serialize_receivable_dataframe


def _cleanup_mask(current: pd.DataFrame) -> pd.Series:
    """Use the exact status and balance predicates of the Streamlit UI."""

    balances = pd.to_numeric(
        current["残高"].astype(str).str.replace(",", ""),
        errors="coerce",
    )
    return (
        current["ステータス"].astype(str).str.strip().eq("完了")
        | balances.le(0)
    )


def _counts(current: pd.DataFrame) -> dict[str, int]:
    target_count = int(_cleanup_mask(current).sum())
    return {
        "current_count": int(len(current)),
        "cleanup_target_count": target_count,
        "remaining_count": int(len(current) - target_count),
    }


def summarize_receivable_cleanup(receivables_directory: Path) -> dict[str, int]:
    """Inspect a ready ledger without changing current.csv."""

    snapshot = read_receivable_current_snapshot_when_ready(receivables_directory)
    return _counts(snapshot.current_df)


def execute_receivable_cleanup(
    receivables_directory: Path,
    *,
    lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    lock_poll_interval_seconds: float = DEFAULT_LOCK_POLL_INTERVAL_SECONDS,
) -> dict[str, int]:
    """Re-evaluate current under the settlement lock, then replace it atomically."""

    with receivable_ledger_lock(
        receivables_directory,
        timeout_seconds=lock_timeout_seconds,
        poll_interval_seconds=lock_poll_interval_seconds,
    ):
        health = _inspect_receivable_ledger_health_locked(receivables_directory)
        if health.status != LEDGER_HEALTH_READY:
            raise ReceivableLedgerRecoveryRequired(
                f"Receivable ledger health is {health.status}"
            )
        current_path = resolve_receivable_ledger_paths(receivables_directory).current_path
        current = load_current_receivables_read_only(current_path).dataframe
        mask = _cleanup_mask(current)
        counts = _counts(current)
        if counts["cleanup_target_count"]:
            remaining = current.loc[~mask].copy()
            atomic_write_bytes(
                current_path,
                serialize_receivable_dataframe(remaining),
            )
        return counts

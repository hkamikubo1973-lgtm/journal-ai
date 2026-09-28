"""Legacy-compatible cleanup of completed rows in current.csv only."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pandas as pd

from receivable_persistence_service import (
    CURRENT_ARCHIVE_PAIR,
    DEFAULT_LOCK_POLL_INTERVAL_SECONDS,
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    LEDGER_HEALTH_READY,
    ReceivableLedgerRecoveryRequired,
    _inspect_receivable_ledger_health_locked,
    commit_receivable_ledger_transaction,
    load_current_receivables_read_only,
    read_receivable_current_snapshot_when_ready,
    receivable_ledger_lock,
    resolve_receivable_ledger_paths,
)
from receivable_import_archive_service import (
    append_archive_rows,
    load_receivable_import_archive,
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


def summarize_receivable_cleanup_rows(current: pd.DataFrame) -> dict[str, int]:
    """Apply the existing cleanup predicate to an already-read ledger snapshot."""
    return _counts(current)


def partition_receivable_cleanup_rows(
    current: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Separate retained and cleanup rows using the existing cleanup mask."""
    mask = _cleanup_mask(current)
    return current.loc[~mask].copy(), current.loc[mask].copy()


def summarize_receivable_cleanup(receivables_directory: Path) -> dict[str, int]:
    """Inspect a ready ledger without changing current.csv."""

    snapshot = read_receivable_current_snapshot_when_ready(receivables_directory)
    return summarize_receivable_cleanup_rows(snapshot.current_df)


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
        loaded_current = load_current_receivables_read_only(current_path)
        current = loaded_current.dataframe
        mask = _cleanup_mask(current)
        counts = _counts(current)
        if not counts["cleanup_target_count"]:
            return counts
        archive, archive_bytes = load_receivable_import_archive(
            Path(receivables_directory)
        )
        archive_after = append_archive_rows(archive, current.loc[mask])
        remaining = current.loc[~mask].copy()

    commit_receivable_ledger_transaction(
        receivables_directory,
        f"import-archive-{uuid4().hex}",
        current_before_bytes=loaded_current.raw_bytes,
        current_after_bytes=serialize_receivable_dataframe(remaining),
        history_before_bytes=archive_bytes if archive_bytes is not None else b"",
        history_after_bytes=serialize_receivable_dataframe(archive_after),
        target_pair=CURRENT_ARCHIVE_PAIR,
        history_before_missing=archive_bytes is None,
        lock_timeout_seconds=lock_timeout_seconds,
        lock_poll_interval_seconds=lock_poll_interval_seconds,
    )
    return counts

"""Persistence and recovery primitives for the receivable ledger.

This module does not execute settlements or automatically start recovery.
"""

from __future__ import annotations

import errno
import hashlib
import importlib
import io
import json
import os
import re
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Mapping

import pandas as pd

from receivable_engine import CURRENT_RECEIVABLE_COLUMNS, HISTORY_COLUMNS


DEFAULT_RECEIVABLES_DIRECTORY = Path("data/receivables")
CURRENT_FILENAME = "current.csv"
HISTORY_FILENAME = "receivable_history.csv"
LEDGER_LOCK_FILENAME = ".receivable_ledger.lock"
DEFAULT_LOCK_TIMEOUT_SECONDS = 5.0
DEFAULT_LOCK_POLL_INTERVAL_SECONDS = 0.05
TRANSACTIONS_DIRECTORY_NAME = ".transactions"
SETTLEMENTS_DIRECTORY_NAME = ".settlements"
TRANSACTION_MARKER_FILENAME = "marker.json"
SETTLEMENT_RECEIPT_SCHEMA_VERSION = 1
_REQUEST_PAYLOAD_UNSET = object()

TRANSACTION_STATES = frozenset(
    {
        "PREPARING",
        "READY_TO_COMMIT",
        "CURRENT_REPLACED",
        "HISTORY_REPLACED",
        "COMMITTED",
        "RECOVERY_REQUIRED",
    }
)
TRANSACTION_DECISIONS = frozenset({None, "COMMIT", "ROLLBACK"})

RECOVERY_BOTH_BEFORE = "BOTH_BEFORE"
RECOVERY_CURRENT_AFTER_HISTORY_BEFORE = "CURRENT_AFTER_HISTORY_BEFORE"
RECOVERY_CURRENT_BEFORE_HISTORY_AFTER = "CURRENT_BEFORE_HISTORY_AFTER"
RECOVERY_BOTH_AFTER = "BOTH_AFTER"
RECOVERY_UNKNOWN = "UNKNOWN"


class ReceivableLedgerError(Exception):
    """Base error for receivable ledger persistence operations."""


class ReceivableLedgerMissingError(ReceivableLedgerError):
    """Raised when a required ledger file does not exist."""


class ReceivableLedgerMalformedError(ReceivableLedgerError):
    """Raised when ledger bytes cannot be decoded or parsed as CSV."""


class ReceivableLedgerSchemaError(ReceivableLedgerError):
    """Raised when a ledger CSV is missing one or more required columns."""


class ReceivableLedgerLockTimeout(ReceivableLedgerError):
    """Raised when the exclusive ledger lock cannot be acquired in time."""


class ReceivableLedgerWriteError(ReceivableLedgerError):
    """Raised when durable bytes cannot be written or replaced."""


class ReceivableLedgerVerificationError(ReceivableLedgerWriteError):
    """Raised when persisted bytes do not match their expected bytes."""


class ReceivableLedgerRecoveryError(ReceivableLedgerError):
    """Raised when transaction recovery metadata is invalid."""


class ReceivableLedgerRecoveryRequired(ReceivableLedgerRecoveryError):
    """Raised when recovery cannot safely choose before or after bytes."""


class ReceivableLedgerConflictError(ReceivableLedgerError):
    """Raised when supplied before bytes no longer match ledger targets."""


class ReceivableLedgerDuplicateTransactionError(ReceivableLedgerError):
    """Raised when a transaction workspace ID is already in use."""


class ReceivableSettlementReceiptError(ReceivableLedgerRecoveryRequired):
    """Raised when a durable settlement receipt is invalid or inconsistent."""


class ReceivableIdempotencyConflictError(ReceivableLedgerConflictError):
    """Raised when one idempotency key is reused for another request."""


@dataclass(frozen=True)
class ReceivableLedgerPaths:
    receivables_directory: Path
    current_path: Path
    history_path: Path
    lock_path: Path


@dataclass(frozen=True)
class ReceivableTransactionPaths:
    receivables_directory: Path
    transactions_directory: Path
    workspace_directory: Path
    marker_path: Path
    current_before_artifact: Path
    current_after_artifact: Path
    history_before_artifact: Path
    history_after_artifact: Path


@dataclass(frozen=True)
class LoadedReceivableCsv:
    dataframe: pd.DataFrame
    raw_bytes: bytes


@dataclass(frozen=True)
class ReceivableLedgerSnapshot:
    current_df: pd.DataFrame
    history_df: pd.DataFrame
    current_raw_bytes: bytes
    history_raw_bytes: bytes | None
    ledger_revision: str


@dataclass(frozen=True)
class ReceivableLedgerTransactionResult:
    transaction_id: str
    state: str
    current_after_hash: str
    history_after_hash: str
    workspace_cleaned: bool
    recovered: bool


@dataclass(frozen=True)
class LoadedSettlementReceipt:
    receipt: dict[str, Any]
    raw_bytes: bytes
    receipt_hash: str


@dataclass(frozen=True)
class ReceivableIdempotentTransactionResult:
    replayed: bool
    settlement: dict[str, Any]
    settlement_id: str
    transaction_id: str
    current_after_hash: str
    history_after_hash: str
    receipt_path: Path
    receipt_hash: str
    workspace_cleaned: bool
    recovered: bool


def resolve_receivable_ledger_paths(
    receivables_directory: str | os.PathLike[str] = (
        DEFAULT_RECEIVABLES_DIRECTORY
    ),
) -> ReceivableLedgerPaths:
    """Resolve ledger paths without creating or reading anything."""

    directory = Path(receivables_directory)
    return ReceivableLedgerPaths(
        receivables_directory=directory,
        current_path=directory / CURRENT_FILENAME,
        history_path=directory / HISTORY_FILENAME,
        lock_path=directory / LEDGER_LOCK_FILENAME,
    )


def calculate_current_revision(current_raw_bytes: bytes) -> str:
    """Return the revision of the exact bytes used to parse current.csv."""

    return calculate_bytes_sha256(current_raw_bytes)


def calculate_bytes_sha256(content: bytes) -> str:
    """Return the shared, non-canonicalized SHA-256 for exact bytes."""

    return hashlib.sha256(content).hexdigest()


def calculate_idempotency_key_hash(idempotency_key: str) -> str:
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise ValueError("idempotency_key must be a non-empty string")
    return calculate_bytes_sha256(idempotency_key.encode("utf-8"))


def canonical_request_bytes(request_payload: Any) -> bytes:
    try:
        text = json.dumps(
            request_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("request_payload must be JSON-safe") from exc
    return text.encode("utf-8")


def calculate_request_hash(request_payload: Any) -> str:
    return calculate_bytes_sha256(canonical_request_bytes(request_payload))


def resolve_request_hash(
    *,
    request_payload: Any = _REQUEST_PAYLOAD_UNSET,
    request_hash: str | None = None,
) -> str:
    """Resolve exactly one canonical request payload or precomputed hash."""

    has_payload = request_payload is not _REQUEST_PAYLOAD_UNSET
    has_hash = request_hash is not None
    if has_payload == has_hash:
        raise ValueError(
            "provide exactly one of request_payload or request_hash"
        )
    if has_payload:
        return calculate_request_hash(request_payload)
    if not isinstance(request_hash, str) or re.fullmatch(
        r"[0-9a-f]{64}", request_hash
    ) is None:
        raise ValueError("request_hash must be lowercase SHA-256 hex")
    return request_hash


def resolve_settlement_receipt_path(
    receivables_directory: str | os.PathLike[str],
    idempotency_key_hash: str,
) -> Path:
    if re.fullmatch(r"[0-9a-f]{64}", idempotency_key_hash) is None:
        raise ValueError("idempotency_key_hash must be lowercase SHA-256 hex")
    return (
        Path(receivables_directory)
        / SETTLEMENTS_DIRECTORY_NAME
        / f"{idempotency_key_hash}.json"
    )


def resolve_receivable_transaction_paths(
    receivables_directory: str | os.PathLike[str],
    transaction_id: str,
) -> ReceivableTransactionPaths:
    """Resolve a fixed transaction workspace without creating it."""

    if (
        not transaction_id
        or ".." in transaction_id
        or Path(transaction_id).name != transaction_id
        or Path(transaction_id).drive
        or (
            re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]*", transaction_id
            )
            is None
        )
    ):
        raise ValueError(
            "transaction_id must contain only safe ASCII path-name characters"
        )

    directory = Path(receivables_directory)
    transactions_directory = directory / TRANSACTIONS_DIRECTORY_NAME
    workspace_directory = transactions_directory / transaction_id
    return ReceivableTransactionPaths(
        receivables_directory=directory,
        transactions_directory=transactions_directory,
        workspace_directory=workspace_directory,
        marker_path=workspace_directory / TRANSACTION_MARKER_FILENAME,
        current_before_artifact=workspace_directory / "current.before.csv",
        current_after_artifact=workspace_directory / "current.after.csv",
        history_before_artifact=workspace_directory / "history.before.csv",
        history_after_artifact=workspace_directory / "history.after.csv",
    )


def _write_all_and_fsync(file_handle: BinaryIO, content: bytes) -> None:
    file_handle.write(content)
    file_handle.flush()
    os.fsync(file_handle.fileno())


def _verify_file_bytes(
    path: Path,
    expected_size: int,
    expected_hash: str,
) -> None:
    try:
        persisted = path.read_bytes()
    except OSError as exc:
        raise ReceivableLedgerVerificationError(
            f"Could not read back persisted bytes: {path}"
        ) from exc

    actual_hash = calculate_bytes_sha256(persisted)
    if len(persisted) != expected_size or actual_hash != expected_hash:
        raise ReceivableLedgerVerificationError(
            f"Persisted bytes failed size/SHA-256 verification: {path}"
        )


def _fsync_directory_best_effort(directory: Path) -> None:
    """Fsync a Unix directory when supported; never treat failure as corruption."""

    if os.name == "nt":
        return
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = None
    try:
        descriptor = os.open(directory, flags)
        os.fsync(descriptor)
    except OSError:
        return
    finally:
        if descriptor is not None:
            os.close(descriptor)


def atomic_write_bytes(
    target_path: str | os.PathLike[str],
    content: bytes,
) -> str:
    """Atomically persist exact bytes through a verified same-directory temp."""

    if not isinstance(content, bytes):
        raise TypeError("content must be bytes")

    target = Path(target_path)
    expected_hash = calculate_bytes_sha256(content)
    temp_path: Path | None = None
    descriptor: int | None = None

    try:
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
        temp_path = Path(temp_name)
        with os.fdopen(descriptor, "wb") as file_handle:
            descriptor = None
            _write_all_and_fsync(file_handle, content)

        _verify_file_bytes(temp_path, len(content), expected_hash)
        os.replace(temp_path, target)
        temp_path = None
        _fsync_directory_best_effort(target.parent)
        _verify_file_bytes(target, len(content), expected_hash)
        return expected_hash
    except ReceivableLedgerVerificationError:
        raise
    except OSError as exc:
        raise ReceivableLedgerWriteError(
            f"Could not atomically write bytes: {target}"
        ) from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _read_required_bytes(path: Path, ledger_label: str) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise ReceivableLedgerMissingError(
            f"{ledger_label} ledger file is missing: {path}"
        ) from exc
    except OSError as exc:
        raise ReceivableLedgerError(
            f"Could not read {ledger_label} ledger file: {path}"
        ) from exc


def _parse_csv_bytes(
    raw_bytes: bytes,
    path: Path,
    ledger_label: str,
) -> pd.DataFrame:
    try:
        decoded = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ReceivableLedgerMalformedError(
            f"{ledger_label} ledger is not valid UTF-8: {path}"
        ) from exc

    try:
        return pd.read_csv(
            io.StringIO(decoded),
            dtype=str,
            keep_default_na=False,
        )
    except (pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        raise ReceivableLedgerMalformedError(
            f"{ledger_label} ledger is malformed CSV: {path}"
        ) from exc
    except (UnicodeError, ValueError) as exc:
        raise ReceivableLedgerMalformedError(
            f"{ledger_label} ledger could not be parsed: {path}"
        ) from exc


def _validate_schema(
    dataframe: pd.DataFrame,
    required_columns: list[str],
    path: Path,
    ledger_label: str,
) -> None:
    missing_columns = [
        column for column in required_columns if column not in dataframe.columns
    ]
    if missing_columns:
        missing_text = ", ".join(missing_columns)
        raise ReceivableLedgerSchemaError(
            f"{ledger_label} ledger is missing required columns "
            f"({missing_text}): {path}"
        )


def load_current_receivables_read_only(
    path: str | os.PathLike[str],
) -> LoadedReceivableCsv:
    """Read current.csv once, validate it, and retain its exact bytes."""

    current_path = Path(path)
    raw_bytes = _read_required_bytes(current_path, "current")
    dataframe = _parse_csv_bytes(raw_bytes, current_path, "current")
    _validate_schema(
        dataframe,
        CURRENT_RECEIVABLE_COLUMNS,
        current_path,
        "current",
    )
    return LoadedReceivableCsv(dataframe=dataframe, raw_bytes=raw_bytes)


def load_receivable_history_read_only(
    path: str | os.PathLike[str],
) -> LoadedReceivableCsv:
    """Strictly read and validate an existing receivable history CSV."""

    history_path = Path(path)
    raw_bytes = _read_required_bytes(history_path, "history")
    dataframe = _parse_csv_bytes(raw_bytes, history_path, "history")
    _validate_schema(dataframe, HISTORY_COLUMNS, history_path, "history")
    return LoadedReceivableCsv(dataframe=dataframe, raw_bytes=raw_bytes)


def load_receivable_history_or_empty_read_only(
    path: str | os.PathLike[str],
) -> tuple[LoadedReceivableCsv, bool]:
    """Read history, explicitly mapping only a missing file to empty history."""

    try:
        return load_receivable_history_read_only(path), False
    except ReceivableLedgerMissingError:
        return (
            LoadedReceivableCsv(
                dataframe=pd.DataFrame(columns=HISTORY_COLUMNS),
                raw_bytes=b"",
            ),
            True,
        )


_PROCESS_LOCKS: dict[str, threading.Lock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


def _process_lock_for(lock_path: Path) -> threading.Lock:
    key = str(lock_path.resolve())
    if os.name == "nt":
        key = key.casefold()
    with _PROCESS_LOCKS_GUARD:
        return _PROCESS_LOCKS.setdefault(key, threading.Lock())


def _ensure_windows_lock_byte(file_handle: BinaryIO) -> None:
    file_handle.seek(0, os.SEEK_END)
    if file_handle.tell() == 0:
        file_handle.write(b"\0")
        file_handle.flush()
    file_handle.seek(0)


def _try_acquire_windows_lock(
    file_handle: BinaryIO,
    msvcrt_module=None,
) -> bool:
    if msvcrt_module is None:
        msvcrt_module = importlib.import_module("msvcrt")
    _ensure_windows_lock_byte(file_handle)
    try:
        msvcrt_module.locking(file_handle.fileno(), msvcrt_module.LK_NBLCK, 1)
        return True
    except OSError:
        return False


def _release_windows_lock(file_handle: BinaryIO, msvcrt_module=None) -> None:
    if msvcrt_module is None:
        msvcrt_module = importlib.import_module("msvcrt")
    file_handle.seek(0)
    msvcrt_module.locking(file_handle.fileno(), msvcrt_module.LK_UNLCK, 1)


def _try_acquire_unix_lock(file_handle: BinaryIO, fcntl_module=None) -> bool:
    if fcntl_module is None:
        fcntl_module = importlib.import_module("fcntl")
    try:
        fcntl_module.flock(
            file_handle.fileno(),
            fcntl_module.LOCK_EX | fcntl_module.LOCK_NB,
        )
        return True
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            return False
        raise


def _release_unix_lock(file_handle: BinaryIO, fcntl_module=None) -> None:
    if fcntl_module is None:
        fcntl_module = importlib.import_module("fcntl")
    fcntl_module.flock(file_handle.fileno(), fcntl_module.LOCK_UN)


def _try_acquire_os_lock(file_handle: BinaryIO) -> bool:
    if os.name == "nt":
        return _try_acquire_windows_lock(file_handle)
    return _try_acquire_unix_lock(file_handle)


def _release_os_lock(file_handle: BinaryIO) -> None:
    if os.name == "nt":
        _release_windows_lock(file_handle)
    else:
        _release_unix_lock(file_handle)


def _validate_lock_timing(
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> None:
    if timeout_seconds < 0:
        raise ValueError("timeout_seconds must be zero or greater")
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be greater than zero")


@contextmanager
def receivable_ledger_lock(
    receivables_directory: str | os.PathLike[str] = (
        DEFAULT_RECEIVABLES_DIRECTORY
    ),
    *,
    timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    poll_interval_seconds: float = DEFAULT_LOCK_POLL_INTERVAL_SECONDS,
) -> Iterator[None]:
    """Hold the process-local and cross-process exclusive ledger lock."""

    _validate_lock_timing(timeout_seconds, poll_interval_seconds)
    paths = resolve_receivable_ledger_paths(receivables_directory)
    process_lock = _process_lock_for(paths.lock_path)
    deadline = time.monotonic() + timeout_seconds

    if not process_lock.acquire(timeout=timeout_seconds):
        raise ReceivableLedgerLockTimeout(
            f"Timed out acquiring receivable ledger lock: {paths.lock_path}"
        )

    file_handle: BinaryIO | None = None
    os_lock_acquired = False
    try:
        try:
            file_handle = paths.lock_path.open("a+b")
        except OSError as exc:
            raise ReceivableLedgerError(
                f"Could not open receivable ledger lock: {paths.lock_path}"
            ) from exc

        while True:
            if _try_acquire_os_lock(file_handle):
                os_lock_acquired = True
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReceivableLedgerLockTimeout(
                    "Timed out acquiring receivable ledger lock: "
                    f"{paths.lock_path}"
                )
            time.sleep(min(poll_interval_seconds, remaining))

        yield
    finally:
        try:
            if os_lock_acquired and file_handle is not None:
                _release_os_lock(file_handle)
        finally:
            if file_handle is not None:
                file_handle.close()
            process_lock.release()


def read_receivable_ledger_snapshot(
    receivables_directory: str | os.PathLike[str] = (
        DEFAULT_RECEIVABLES_DIRECTORY
    ),
    *,
    history_missing_as_empty: bool = True,
    lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    lock_poll_interval_seconds: float = DEFAULT_LOCK_POLL_INTERVAL_SECONDS,
) -> ReceivableLedgerSnapshot:
    """Read a consistent read-only ledger snapshot under the ledger lock."""

    paths = resolve_receivable_ledger_paths(receivables_directory)
    with receivable_ledger_lock(
        receivables_directory,
        timeout_seconds=lock_timeout_seconds,
        poll_interval_seconds=lock_poll_interval_seconds,
    ):
        current = load_current_receivables_read_only(paths.current_path)
        history_missing = False
        if history_missing_as_empty:
            history, history_missing = (
                load_receivable_history_or_empty_read_only(paths.history_path)
            )
        else:
            history = load_receivable_history_read_only(paths.history_path)

        return ReceivableLedgerSnapshot(
            current_df=current.dataframe,
            history_df=history.dataframe,
            current_raw_bytes=current.raw_bytes,
            history_raw_bytes=None if history_missing else history.raw_bytes,
            ledger_revision=calculate_current_revision(current.raw_bytes),
        )


_RECEIPT_REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        "idempotency_key_hash",
        "request_hash",
        "transaction_id",
        "settlement_id",
        "settlement",
        "current_after_hash",
        "history_after_hash",
        "committed_at",
    }
)


def _validate_settlement_receipt(receipt: Mapping[str, Any]) -> None:
    missing = sorted(_RECEIPT_REQUIRED_FIELDS.difference(receipt))
    if missing:
        raise ReceivableSettlementReceiptError(
            "Settlement receipt is missing fields: " + ", ".join(missing)
        )
    if receipt["schema_version"] != SETTLEMENT_RECEIPT_SCHEMA_VERSION:
        raise ReceivableSettlementReceiptError(
            "Unsupported settlement receipt schema_version"
        )
    for field in (
        "idempotency_key_hash",
        "request_hash",
        "current_after_hash",
        "history_after_hash",
    ):
        if not isinstance(receipt[field], str) or re.fullmatch(
            r"[0-9a-f]{64}", receipt[field]
        ) is None:
            raise ReceivableSettlementReceiptError(
                f"Settlement receipt has invalid {field}"
            )
    for field in ("transaction_id", "settlement_id", "committed_at"):
        if not isinstance(receipt[field], str) or not receipt[field]:
            raise ReceivableSettlementReceiptError(
                f"Settlement receipt has invalid {field}"
            )
    if not isinstance(receipt["settlement"], dict):
        raise ReceivableSettlementReceiptError(
            "Settlement receipt settlement must be a JSON object"
        )
    try:
        datetime.fromisoformat(receipt["committed_at"])
    except ValueError as exc:
        raise ReceivableSettlementReceiptError(
            "Settlement receipt has invalid committed_at"
        ) from exc


def _settlement_receipt_json_bytes(receipt: Mapping[str, Any]) -> bytes:
    _validate_settlement_receipt(receipt)
    try:
        text = json.dumps(
            dict(receipt),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ReceivableSettlementReceiptError(
            "Settlement receipt is not JSON-safe"
        ) from exc
    return (text + "\n").encode("utf-8")


def read_settlement_receipt(
    receipt_path: str | os.PathLike[str],
) -> LoadedSettlementReceipt:
    path = Path(receipt_path)
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise ReceivableSettlementReceiptError(
            f"Could not read settlement receipt: {path}"
        ) from exc
    try:
        receipt = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReceivableSettlementReceiptError(
            f"Settlement receipt is malformed: {path}"
        ) from exc
    if not isinstance(receipt, dict):
        raise ReceivableSettlementReceiptError(
            f"Settlement receipt must be a JSON object: {path}"
        )
    _validate_settlement_receipt(receipt)
    return LoadedSettlementReceipt(
        receipt=receipt,
        raw_bytes=raw_bytes,
        receipt_hash=calculate_bytes_sha256(raw_bytes),
    )


def save_settlement_receipt(
    receipt_path: str | os.PathLike[str],
    receipt: Mapping[str, Any],
) -> LoadedSettlementReceipt:
    path = Path(receipt_path)
    if path.exists():
        raise ReceivableSettlementReceiptError(
            f"Settlement receipt already exists: {path}"
        )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ReceivableLedgerWriteError(
            f"Could not create settlement receipt directory: {path.parent}"
        ) from exc
    expected = dict(receipt)
    raw_bytes = _settlement_receipt_json_bytes(expected)
    expected_hash = atomic_write_bytes(path, raw_bytes)
    loaded = read_settlement_receipt(path)
    if loaded.receipt != expected or loaded.receipt_hash != expected_hash:
        raise ReceivableSettlementReceiptError(
            f"Settlement receipt read-back verification failed: {path}"
        )
    return loaded


_MARKER_REQUIRED_FIELDS = frozenset(
    {
        "transaction_id",
        "settlement_id",
        "state",
        "decision",
        "created_at",
        "updated_at",
        "current_target",
        "history_target",
        "current_before_hash",
        "current_after_hash",
        "history_before_hash",
        "history_after_hash",
        "current_before_artifact",
        "current_after_artifact",
        "history_before_artifact",
        "history_after_artifact",
        "last_error",
    }
)
_ARTIFACT_MARKER_FIELDS = (
    "current_before_hash",
    "current_after_hash",
    "history_before_hash",
    "history_after_hash",
    "current_before_artifact",
    "current_after_artifact",
    "history_before_artifact",
    "history_after_artifact",
)
_MARKER_TRANSITIONS = {
    "PREPARING": {"PREPARING", "READY_TO_COMMIT", "RECOVERY_REQUIRED"},
    "READY_TO_COMMIT": {
        "READY_TO_COMMIT",
        "CURRENT_REPLACED",
        "HISTORY_REPLACED",
        "COMMITTED",
        "RECOVERY_REQUIRED",
    },
    "CURRENT_REPLACED": {
        "CURRENT_REPLACED",
        "HISTORY_REPLACED",
        "COMMITTED",
        "RECOVERY_REQUIRED",
    },
    "HISTORY_REPLACED": {
        "HISTORY_REPLACED",
        "CURRENT_REPLACED",
        "COMMITTED",
        "RECOVERY_REQUIRED",
    },
    "COMMITTED": {"COMMITTED"},
    "RECOVERY_REQUIRED": {
        "RECOVERY_REQUIRED",
        "CURRENT_REPLACED",
        "HISTORY_REPLACED",
        "COMMITTED",
    },
}
_UNSET = object()


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_transaction_marker(marker: Mapping[str, Any]) -> None:
    missing = sorted(_MARKER_REQUIRED_FIELDS.difference(marker))
    if missing:
        raise ReceivableLedgerRecoveryError(
            "Transaction marker is missing fields: " + ", ".join(missing)
        )
    if marker["state"] not in TRANSACTION_STATES:
        raise ReceivableLedgerRecoveryError(
            f"Unknown transaction marker state: {marker['state']}"
        )
    if marker["decision"] not in TRANSACTION_DECISIONS:
        raise ReceivableLedgerRecoveryError(
            f"Unknown transaction marker decision: {marker['decision']}"
        )

    commit_states = {
        "READY_TO_COMMIT",
        "CURRENT_REPLACED",
        "HISTORY_REPLACED",
        "COMMITTED",
    }
    if marker["state"] in commit_states:
        if marker["decision"] != "COMMIT":
            raise ReceivableLedgerRecoveryError(
                f"{marker['state']} requires decision=COMMIT"
            )
        empty_fields = [
            field for field in _ARTIFACT_MARKER_FIELDS if not marker[field]
        ]
        if empty_fields:
            raise ReceivableLedgerRecoveryError(
                "Commit-ready marker is missing artifact metadata: "
                + ", ".join(empty_fields)
            )
    if marker["state"] == "PREPARING" and marker["decision"] == "COMMIT":
        raise ReceivableLedgerRecoveryError(
            "PREPARING marker cannot contain a COMMIT decision"
        )
    if marker.get("receipt_required") and marker["state"] in commit_states:
        required_text_fields = (
            "idempotency_key_hash",
            "request_hash",
            "receipt_path",
            "committed_at",
            "settlement_id",
        )
        empty_receipt_fields = [
            field for field in required_text_fields if not marker.get(field)
        ]
        if marker.get("settlement_response") is None:
            empty_receipt_fields.append("settlement_response")
        if empty_receipt_fields:
            raise ReceivableLedgerRecoveryError(
                "Receipt-aware marker is missing metadata: "
                + ", ".join(empty_receipt_fields)
            )
        if not isinstance(marker["settlement_response"], dict):
            raise ReceivableLedgerRecoveryError(
                "settlement_response must be a JSON object"
            )
        if marker["state"] == "COMMITTED" and not marker.get("receipt_hash"):
            raise ReceivableLedgerRecoveryError(
                "Receipt-aware COMMITTED marker requires receipt_hash"
            )


def _marker_json_bytes(marker: Mapping[str, Any]) -> bytes:
    try:
        text = json.dumps(
            dict(marker),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ReceivableLedgerRecoveryError(
            "Transaction marker is not JSON serializable"
        ) from exc
    return (text + "\n").encode("utf-8")


def _verify_ready_marker_artifacts(
    marker_directory: Path,
    marker: Mapping[str, Any],
) -> None:
    artifact_prefixes = (
        "current_before",
        "current_after",
        "history_before",
        "history_after",
    )
    for prefix in artifact_prefixes:
        artifact_name = marker[f"{prefix}_artifact"]
        if Path(artifact_name).name != artifact_name:
            raise ReceivableLedgerRecoveryError(
                f"Invalid transaction artifact name: {artifact_name}"
            )
        artifact_path = marker_directory / artifact_name
        try:
            artifact_bytes = artifact_path.read_bytes()
        except OSError as exc:
            raise ReceivableLedgerRecoveryError(
                f"Transaction artifact is missing: {artifact_path}"
            ) from exc
        if (
            calculate_bytes_sha256(artifact_bytes)
            != marker[f"{prefix}_hash"]
        ):
            raise ReceivableLedgerRecoveryError(
                f"Transaction artifact hash mismatch: {artifact_path}"
            )


def write_transaction_marker(
    marker_path: str | os.PathLike[str],
    marker: Mapping[str, Any],
) -> dict[str, Any]:
    """Atomically write a validated UTF-8 transaction marker."""

    updated_marker = dict(marker)
    updated_marker["updated_at"] = _utc_now_text()
    _validate_transaction_marker(updated_marker)
    path = Path(marker_path)
    if updated_marker["state"] == "READY_TO_COMMIT":
        _verify_ready_marker_artifacts(path.parent, updated_marker)
    atomic_write_bytes(path, _marker_json_bytes(updated_marker))
    return updated_marker


def read_transaction_marker(
    marker_path: str | os.PathLike[str],
) -> dict[str, Any]:
    """Strictly read and validate a transaction marker."""

    path = Path(marker_path)
    try:
        raw_bytes = path.read_bytes()
    except FileNotFoundError as exc:
        raise ReceivableLedgerRecoveryError(
            f"Transaction marker is missing: {path}"
        ) from exc
    except OSError as exc:
        raise ReceivableLedgerRecoveryError(
            f"Could not read transaction marker: {path}"
        ) from exc

    try:
        marker = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReceivableLedgerRecoveryError(
            f"Transaction marker is malformed: {path}"
        ) from exc
    if not isinstance(marker, dict):
        raise ReceivableLedgerRecoveryError(
            f"Transaction marker must be a JSON object: {path}"
        )
    _validate_transaction_marker(marker)
    return marker


def _initial_transaction_marker(
    paths: ReceivableTransactionPaths,
    settlement_id: str | None,
) -> dict[str, Any]:
    ledger_paths = resolve_receivable_ledger_paths(
        paths.receivables_directory
    )
    now = _utc_now_text()
    return {
        "transaction_id": paths.workspace_directory.name,
        "settlement_id": settlement_id,
        "state": "PREPARING",
        "decision": None,
        "created_at": now,
        "updated_at": now,
        "current_target": str(ledger_paths.current_path.resolve()),
        "history_target": str(ledger_paths.history_path.resolve()),
        "current_before_hash": None,
        "current_after_hash": None,
        "history_before_hash": None,
        "history_after_hash": None,
        "current_before_size": None,
        "current_after_size": None,
        "history_before_size": None,
        "history_after_size": None,
        "current_before_artifact": paths.current_before_artifact.name,
        "current_after_artifact": paths.current_after_artifact.name,
        "history_before_artifact": paths.history_before_artifact.name,
        "history_after_artifact": paths.history_after_artifact.name,
        "last_error": None,
        "receipt_required": False,
        "idempotency_key_hash": None,
        "request_hash": None,
        "receipt_path": None,
        "receipt_hash": None,
        "settlement_response": None,
        "committed_at": None,
    }


def create_transaction_workspace(
    receivables_directory: str | os.PathLike[str],
    transaction_id: str,
    *,
    settlement_id: str | None = None,
) -> tuple[ReceivableTransactionPaths, dict[str, Any]]:
    """Explicitly create a transaction workspace and PREPARING marker."""

    paths = resolve_receivable_transaction_paths(
        receivables_directory, transaction_id
    )
    try:
        paths.transactions_directory.mkdir(parents=True, exist_ok=True)
        paths.workspace_directory.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise ReceivableLedgerRecoveryError(
            f"Transaction workspace already exists: {paths.workspace_directory}"
        ) from exc
    except OSError as exc:
        raise ReceivableLedgerWriteError(
            f"Could not create transaction workspace: {paths.workspace_directory}"
        ) from exc

    marker = write_transaction_marker(
        paths.marker_path,
        _initial_transaction_marker(paths, settlement_id),
    )
    return paths, marker


def prepare_transaction_artifacts(
    receivables_directory: str | os.PathLike[str],
    transaction_id: str,
    *,
    current_before_bytes: bytes,
    current_after_bytes: bytes,
    history_before_bytes: bytes,
    history_after_bytes: bytes,
    settlement_id: str | None = None,
    marker_metadata: Mapping[str, Any] | None = None,
) -> tuple[ReceivableTransactionPaths, dict[str, Any]]:
    """Durably prepare four artifacts before recording COMMIT decision."""

    paths = resolve_receivable_transaction_paths(
        receivables_directory, transaction_id
    )
    if paths.workspace_directory.exists():
        marker = read_transaction_marker(paths.marker_path)
        if marker["state"] != "PREPARING":
            raise ReceivableLedgerRecoveryError(
                "Artifacts can only be prepared from PREPARING state"
            )
        if (
            settlement_id is not None
            and marker["settlement_id"] != settlement_id
        ):
            raise ReceivableLedgerRecoveryError(
                "settlement_id does not match the existing marker"
            )
    else:
        paths, marker = create_transaction_workspace(
            receivables_directory,
            transaction_id,
            settlement_id=settlement_id,
        )

    artifacts = (
        (
            "current_before",
            paths.current_before_artifact,
            current_before_bytes,
        ),
        ("current_after", paths.current_after_artifact, current_after_bytes),
        (
            "history_before",
            paths.history_before_artifact,
            history_before_bytes,
        ),
        ("history_after", paths.history_after_artifact, history_after_bytes),
    )
    prepared_marker = dict(marker)
    for field_prefix, artifact_path, content in artifacts:
        artifact_hash = atomic_write_bytes(artifact_path, content)
        prepared_marker[f"{field_prefix}_hash"] = artifact_hash
        prepared_marker[f"{field_prefix}_size"] = len(content)
        prepared_marker[f"{field_prefix}_artifact"] = artifact_path.name

    if marker_metadata is not None:
        prepared_marker.update(dict(marker_metadata))

    prepared_marker["state"] = "READY_TO_COMMIT"
    prepared_marker["decision"] = "COMMIT"
    prepared_marker["last_error"] = None
    prepared_marker = write_transaction_marker(
        paths.marker_path, prepared_marker
    )
    return paths, prepared_marker


def transition_transaction_marker(
    marker_path: str | os.PathLike[str],
    state: str,
    *,
    decision: str | None | object = _UNSET,
    last_error: str | None | object = _UNSET,
) -> dict[str, Any]:
    """Atomically perform a small, validated marker state transition."""

    marker = read_transaction_marker(marker_path)
    if state not in TRANSACTION_STATES:
        raise ReceivableLedgerRecoveryError(
            f"Unknown transaction marker state: {state}"
        )
    if state not in _MARKER_TRANSITIONS[marker["state"]]:
        raise ReceivableLedgerRecoveryError(
            f"Invalid marker transition: {marker['state']} -> {state}"
        )
    marker["state"] = state
    if decision is not _UNSET:
        marker["decision"] = decision
    if last_error is not _UNSET:
        marker["last_error"] = last_error
    return write_transaction_marker(marker_path, marker)


def _coerce_marker(marker_or_path: Mapping[str, Any] | str | os.PathLike[str]):
    if isinstance(marker_or_path, Mapping):
        marker = dict(marker_or_path)
        _validate_transaction_marker(marker)
        return marker
    return read_transaction_marker(marker_or_path)


def _file_hash_or_none(path: str | os.PathLike[str]) -> str | None:
    try:
        return calculate_bytes_sha256(Path(path).read_bytes())
    except OSError:
        return None


def classify_recovery_state(
    current_target_path: str | os.PathLike[str],
    history_target_path: str | os.PathLike[str],
    marker_or_path: Mapping[str, Any] | str | os.PathLike[str],
) -> str:
    """Classify actual target hashes against marker before/after hashes."""

    marker = _coerce_marker(marker_or_path)
    current_hash = _file_hash_or_none(current_target_path)
    history_hash = _file_hash_or_none(history_target_path)

    current_before = current_hash == marker["current_before_hash"]
    current_after = current_hash == marker["current_after_hash"]
    history_before = history_hash == marker["history_before_hash"]
    history_after = history_hash == marker["history_after_hash"]

    if current_before and history_before:
        return RECOVERY_BOTH_BEFORE
    if current_after and history_before:
        return RECOVERY_CURRENT_AFTER_HISTORY_BEFORE
    if current_before and history_after:
        return RECOVERY_CURRENT_BEFORE_HISTORY_AFTER
    if current_after and history_after:
        return RECOVERY_BOTH_AFTER
    return RECOVERY_UNKNOWN


def plan_recovery_actions(
    marker_or_path: Mapping[str, Any] | str | os.PathLike[str],
    classification: str,
) -> tuple[str, ...]:
    """Return explicit recovery actions without changing any file."""

    marker = _coerce_marker(marker_or_path)
    valid_classifications = {
        RECOVERY_BOTH_BEFORE,
        RECOVERY_CURRENT_AFTER_HISTORY_BEFORE,
        RECOVERY_CURRENT_BEFORE_HISTORY_AFTER,
        RECOVERY_BOTH_AFTER,
        RECOVERY_UNKNOWN,
    }
    if classification not in valid_classifications:
        raise ReceivableLedgerRecoveryError(
            f"Unknown recovery classification: {classification}"
        )
    if classification == RECOVERY_UNKNOWN:
        raise ReceivableLedgerRecoveryRequired(
            "Ledger targets do not match transaction before/after hashes"
        )

    commit_decided = (
        marker["decision"] == "COMMIT"
        and marker["state"] != "PREPARING"
    )
    if not commit_decided:
        if classification == RECOVERY_BOTH_BEFORE:
            return ("ABORT",)
        raise ReceivableLedgerRecoveryRequired(
            "Targets changed before a durable COMMIT decision"
        )

    actions = {
        RECOVERY_BOTH_BEFORE: (
            "ROLL_FORWARD_CURRENT",
            "ROLL_FORWARD_HISTORY",
        ),
        RECOVERY_CURRENT_AFTER_HISTORY_BEFORE: ("ROLL_FORWARD_HISTORY",),
        RECOVERY_CURRENT_BEFORE_HISTORY_AFTER: ("ROLL_FORWARD_CURRENT",),
        RECOVERY_BOTH_AFTER: ("FINALIZE_COMMIT",),
    }
    return actions[classification]


def _restore_from_artifact(
    artifact_path: str | os.PathLike[str],
    expected_hash: str,
    target_path: str | os.PathLike[str],
) -> str:
    artifact = Path(artifact_path)
    try:
        content = artifact.read_bytes()
    except OSError as exc:
        raise ReceivableLedgerRecoveryRequired(
            f"Recovery artifact cannot be read: {artifact}"
        ) from exc
    if calculate_bytes_sha256(content) != expected_hash:
        raise ReceivableLedgerRecoveryRequired(
            f"Recovery artifact hash mismatch: {artifact}"
        )

    persisted_hash = atomic_write_bytes(target_path, content)
    if persisted_hash != expected_hash:
        raise ReceivableLedgerRecoveryRequired(
            f"Recovered target hash mismatch: {target_path}"
        )
    return persisted_hash


def roll_forward_from_artifact(
    artifact_path: str | os.PathLike[str],
    expected_hash: str,
    target_path: str | os.PathLike[str],
) -> str:
    """Restore exact after bytes while retaining the source artifact."""

    return _restore_from_artifact(artifact_path, expected_hash, target_path)


def rollback_from_artifact(
    artifact_path: str | os.PathLike[str],
    expected_hash: str,
    target_path: str | os.PathLike[str],
) -> str:
    """Restore exact before bytes while retaining the source artifact."""

    return _restore_from_artifact(artifact_path, expected_hash, target_path)


def mark_transaction_recovery_required(
    marker_path: str | os.PathLike[str],
    error: str,
) -> dict[str, Any]:
    """Persist RECOVERY_REQUIRED without attempting a target overwrite."""

    return transition_transaction_marker(
        marker_path,
        "RECOVERY_REQUIRED",
        last_error=error,
    )


def cleanup_transaction_workspace(
    workspace_directory: str | os.PathLike[str],
) -> None:
    """Remove only a terminal COMMITTED or explicitly rolled-back workspace."""

    workspace = Path(workspace_directory)
    if workspace.parent.name != TRANSACTIONS_DIRECTORY_NAME:
        raise ReceivableLedgerRecoveryError(
            f"Not a receivable transaction workspace: {workspace}"
        )
    marker = read_transaction_marker(workspace / TRANSACTION_MARKER_FILENAME)
    if marker["transaction_id"] != workspace.name:
        raise ReceivableLedgerRecoveryError(
            f"Transaction marker does not match workspace: {workspace}"
        )
    cleanup_allowed = marker["state"] == "COMMITTED" or (
        marker["state"] == "PREPARING"
        and marker["decision"] == "ROLLBACK"
    )
    if not cleanup_allowed:
        raise ReceivableLedgerRecoveryError(
            f"Transaction workspace is not safe to clean up: {workspace}"
        )
    try:
        shutil.rmtree(workspace)
    except OSError as exc:
        raise ReceivableLedgerRecoveryError(
            f"Could not clean up transaction workspace: {workspace}"
        ) from exc


def _normalized_resolved_path(path: str | os.PathLike[str]) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def _validate_transaction_recovery_paths(
    paths: ReceivableTransactionPaths,
    marker: Mapping[str, Any],
) -> None:
    ledger_paths = resolve_receivable_ledger_paths(
        paths.receivables_directory
    )
    expected_targets = {
        "current_target": ledger_paths.current_path,
        "history_target": ledger_paths.history_path,
    }
    try:
        for field, expected_path in expected_targets.items():
            if _normalized_resolved_path(marker[field]) != (
                _normalized_resolved_path(expected_path)
            ):
                raise ReceivableLedgerRecoveryRequired(
                    f"Marker {field} does not match this ledger"
                )
    except (OSError, TypeError, ValueError) as exc:
        raise ReceivableLedgerRecoveryRequired(
            "Marker contains an invalid ledger target path"
        ) from exc

    expected_artifacts = {
        "current_before_artifact": paths.current_before_artifact,
        "current_after_artifact": paths.current_after_artifact,
        "history_before_artifact": paths.history_before_artifact,
        "history_after_artifact": paths.history_after_artifact,
    }
    for field, expected_path in expected_artifacts.items():
        artifact_name = marker[field]
        if not isinstance(artifact_name, str):
            raise ReceivableLedgerRecoveryRequired(
                f"Marker {field} is not a safe artifact name"
            )
        actual_path = paths.workspace_directory / artifact_name
        if (
            artifact_name != expected_path.name
            or _normalized_resolved_path(actual_path)
            != _normalized_resolved_path(expected_path)
        ):
            raise ReceivableLedgerRecoveryRequired(
                f"Marker {field} escapes or changes the transaction workspace"
            )

    if marker["transaction_id"] != paths.workspace_directory.name:
        raise ReceivableLedgerRecoveryRequired(
            "Marker transaction_id does not match its workspace"
        )


def _recovery_required_message(
    paths: ReceivableTransactionPaths,
    detail: str,
) -> str:
    ledger_paths = resolve_receivable_ledger_paths(
        paths.receivables_directory
    )
    current_hash = _file_hash_or_none(ledger_paths.current_path)
    history_hash = _file_hash_or_none(ledger_paths.history_path)
    return (
        f"transaction_id={paths.workspace_directory.name}; "
        f"current_hash={current_hash}; history_hash={history_hash}; "
        f"workspace={paths.workspace_directory}; {detail}"
    )


def _raise_transaction_recovery_required(
    paths: ReceivableTransactionPaths,
    marker: Mapping[str, Any] | None,
    detail: str,
) -> None:
    message = _recovery_required_message(paths, detail)
    if marker is not None and marker.get("state") != "RECOVERY_REQUIRED":
        try:
            mark_transaction_recovery_required(paths.marker_path, message)
        except ReceivableLedgerError:
            pass
    raise ReceivableLedgerRecoveryRequired(message)


def _verify_transaction_artifacts_for_recovery(
    paths: ReceivableTransactionPaths,
    marker: Mapping[str, Any],
) -> None:
    artifact_fields = (
        (paths.current_before_artifact, "current_before_hash"),
        (paths.current_after_artifact, "current_after_hash"),
        (paths.history_before_artifact, "history_before_hash"),
        (paths.history_after_artifact, "history_after_hash"),
    )
    for artifact_path, hash_field in artifact_fields:
        try:
            artifact_bytes = artifact_path.read_bytes()
        except OSError:
            _raise_transaction_recovery_required(
                paths,
                marker,
                f"artifact missing: {artifact_path}",
            )
        if calculate_bytes_sha256(artifact_bytes) != marker[hash_field]:
            _raise_transaction_recovery_required(
                paths,
                marker,
                f"artifact hash mismatch: {artifact_path}",
            )


def _verify_final_after_hashes(
    paths: ReceivableTransactionPaths,
    marker: Mapping[str, Any],
) -> None:
    ledger_paths = resolve_receivable_ledger_paths(
        paths.receivables_directory
    )
    classification = classify_recovery_state(
        ledger_paths.current_path,
        ledger_paths.history_path,
        marker,
    )
    if classification != RECOVERY_BOTH_AFTER:
        _raise_transaction_recovery_required(
            paths,
            marker,
            f"final target classification is {classification}",
        )


def _cleanup_committed_workspace_best_effort(
    paths: ReceivableTransactionPaths,
) -> bool:
    try:
        cleanup_transaction_workspace(paths.workspace_directory)
        return True
    except ReceivableLedgerRecoveryError:
        return False


def _transaction_result(
    marker: Mapping[str, Any],
    *,
    state: str,
    workspace_cleaned: bool,
    recovered: bool,
) -> ReceivableLedgerTransactionResult:
    return ReceivableLedgerTransactionResult(
        transaction_id=str(marker["transaction_id"]),
        state=state,
        current_after_hash=str(marker.get("current_after_hash") or ""),
        history_after_hash=str(marker.get("history_after_hash") or ""),
        workspace_cleaned=workspace_cleaned,
        recovered=recovered,
    )


def _abort_preparing_transaction_locked(
    paths: ReceivableTransactionPaths,
    marker: Mapping[str, Any],
) -> ReceivableLedgerTransactionResult:
    _validate_transaction_recovery_paths(paths, marker)
    ledger_paths = resolve_receivable_ledger_paths(
        paths.receivables_directory
    )
    try:
        current_before = paths.current_before_artifact.read_bytes()
        history_before = paths.history_before_artifact.read_bytes()
        current_target = ledger_paths.current_path.read_bytes()
        history_target = ledger_paths.history_path.read_bytes()
    except OSError:
        _raise_transaction_recovery_required(
            paths,
            marker,
            "PREPARING transaction cannot prove both targets are before",
        )

    if current_target != current_before or history_target != history_before:
        _raise_transaction_recovery_required(
            paths,
            marker,
            "PREPARING transaction targets do not both match before artifacts",
        )

    for artifact_path, hash_field in (
        (paths.current_before_artifact, "current_before_hash"),
        (paths.current_after_artifact, "current_after_hash"),
        (paths.history_before_artifact, "history_before_hash"),
        (paths.history_after_artifact, "history_after_hash"),
    ):
        if not artifact_path.exists():
            continue
        artifact_bytes = artifact_path.read_bytes()
        expected_hash = marker.get(hash_field)
        if (
            expected_hash is not None
            and calculate_bytes_sha256(artifact_bytes) != expected_hash
        ):
            _raise_transaction_recovery_required(
                paths,
                marker,
                f"PREPARING artifact hash mismatch: {artifact_path}",
            )

    rolled_back_marker = transition_transaction_marker(
        paths.marker_path,
        "PREPARING",
        decision="ROLLBACK",
    )
    try:
        cleanup_transaction_workspace(paths.workspace_directory)
    except ReceivableLedgerRecoveryError as exc:
        raise ReceivableLedgerRecoveryError(
            f"Could not clean aborted transaction: {paths.workspace_directory}"
        ) from exc
    return _transaction_result(
        rolled_back_marker,
        state="ABORTED",
        workspace_cleaned=True,
        recovered=True,
    )


def _abort_failed_prepare_in_same_call(
    paths: ReceivableTransactionPaths,
    current_before_bytes: bytes,
    history_before_bytes: bytes,
) -> None:
    marker = read_transaction_marker(paths.marker_path)
    _validate_transaction_recovery_paths(paths, marker)
    if marker["state"] != "PREPARING" or marker["decision"] is not None:
        _raise_transaction_recovery_required(
            paths,
            marker,
            "failed prepare is no longer an undecided PREPARING transaction",
        )
    try:
        _read_and_verify_transaction_before_targets(
            paths.receivables_directory,
            current_before_bytes,
            history_before_bytes,
        )
    except ReceivableLedgerError as exc:
        _raise_transaction_recovery_required(paths, marker, str(exc))
    transition_transaction_marker(
        paths.marker_path,
        "PREPARING",
        decision="ROLLBACK",
    )
    cleanup_transaction_workspace(paths.workspace_directory)


def _expected_receipt_from_marker(
    marker: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": SETTLEMENT_RECEIPT_SCHEMA_VERSION,
        "idempotency_key_hash": marker["idempotency_key_hash"],
        "request_hash": marker["request_hash"],
        "transaction_id": marker["transaction_id"],
        "settlement_id": marker["settlement_id"],
        "settlement": marker["settlement_response"],
        "current_after_hash": marker["current_after_hash"],
        "history_after_hash": marker["history_after_hash"],
        "committed_at": marker["committed_at"],
    }


def _validate_receipt_marker_path(
    paths: ReceivableTransactionPaths,
    marker: Mapping[str, Any],
) -> Path:
    try:
        expected = resolve_settlement_receipt_path(
            paths.receivables_directory,
            marker["idempotency_key_hash"],
        )
        actual = Path(marker["receipt_path"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ReceivableSettlementReceiptError(
            "Marker contains invalid receipt path metadata"
        ) from exc
    if _normalized_resolved_path(actual) != _normalized_resolved_path(expected):
        raise ReceivableSettlementReceiptError(
            "Marker receipt_path does not match its idempotency key"
        )
    return expected


def _ensure_receipt_durable_locked(
    paths: ReceivableTransactionPaths,
    marker: Mapping[str, Any],
) -> dict[str, Any]:
    if not marker.get("receipt_required"):
        return dict(marker)
    try:
        receipt_path = _validate_receipt_marker_path(paths, marker)
        expected = _expected_receipt_from_marker(marker)
        _validate_settlement_receipt(expected)
        if receipt_path.exists():
            loaded = read_settlement_receipt(receipt_path)
        else:
            loaded = save_settlement_receipt(receipt_path, expected)
        if loaded.receipt != expected:
            raise ReceivableSettlementReceiptError(
                "Settlement receipt conflicts with transaction marker"
            )
        if marker.get("receipt_hash") not in (None, loaded.receipt_hash):
            raise ReceivableSettlementReceiptError(
                "Settlement receipt hash conflicts with transaction marker"
            )
    except ReceivableSettlementReceiptError as exc:
        _raise_transaction_recovery_required(paths, marker, str(exc))

    updated = dict(marker)
    updated["receipt_hash"] = loaded.receipt_hash
    return write_transaction_marker(paths.marker_path, updated)


def _recover_transaction_workspace_locked(
    paths: ReceivableTransactionPaths,
) -> ReceivableLedgerTransactionResult:
    try:
        marker = read_transaction_marker(paths.marker_path)
    except ReceivableLedgerRecoveryError as exc:
        _raise_transaction_recovery_required(paths, None, str(exc))

    try:
        _validate_transaction_recovery_paths(paths, marker)
    except ReceivableLedgerRecoveryRequired as exc:
        _raise_transaction_recovery_required(paths, marker, str(exc))

    if marker["state"] == "RECOVERY_REQUIRED":
        _raise_transaction_recovery_required(
            paths,
            marker,
            str(marker.get("last_error") or "manual recovery is required"),
        )

    if marker["state"] == "COMMITTED":
        marker = _ensure_receipt_durable_locked(paths, marker)
        cleaned = _cleanup_committed_workspace_best_effort(paths)
        return _transaction_result(
            marker,
            state="COMMITTED",
            workspace_cleaned=cleaned,
            recovered=True,
        )

    if marker["state"] == "PREPARING":
        if marker["decision"] not in (None, "ROLLBACK"):
            _raise_transaction_recovery_required(
                paths, marker, "PREPARING has an invalid durable decision"
            )
        return _abort_preparing_transaction_locked(paths, marker)

    _verify_transaction_artifacts_for_recovery(paths, marker)
    ledger_paths = resolve_receivable_ledger_paths(
        paths.receivables_directory
    )
    classification = classify_recovery_state(
        ledger_paths.current_path,
        ledger_paths.history_path,
        marker,
    )
    if classification == RECOVERY_UNKNOWN:
        _raise_transaction_recovery_required(
            paths, marker, "target classification is UNKNOWN"
        )

    actions = plan_recovery_actions(marker, classification)
    if "ROLL_FORWARD_CURRENT" in actions:
        roll_forward_from_artifact(
            paths.current_after_artifact,
            marker["current_after_hash"],
            ledger_paths.current_path,
        )
        marker = transition_transaction_marker(
            paths.marker_path, "CURRENT_REPLACED"
        )
    if "ROLL_FORWARD_HISTORY" in actions:
        roll_forward_from_artifact(
            paths.history_after_artifact,
            marker["history_after_hash"],
            ledger_paths.history_path,
        )
        marker = transition_transaction_marker(
            paths.marker_path, "HISTORY_REPLACED"
        )

    _verify_final_after_hashes(paths, marker)
    marker = _ensure_receipt_durable_locked(paths, marker)
    marker = transition_transaction_marker(paths.marker_path, "COMMITTED")
    cleaned = _cleanup_committed_workspace_best_effort(paths)
    return _transaction_result(
        marker,
        state="COMMITTED",
        workspace_cleaned=cleaned,
        recovered=True,
    )


def _transaction_workspace_records(
    receivables_directory: str | os.PathLike[str],
) -> list[tuple[ReceivableTransactionPaths, dict[str, Any]]]:
    directory = Path(receivables_directory) / TRANSACTIONS_DIRECTORY_NAME
    if not directory.exists():
        return []
    try:
        entries = list(directory.iterdir())
    except OSError as exc:
        raise ReceivableLedgerRecoveryError(
            f"Could not inspect transaction directory: {directory}"
        ) from exc

    records = []
    for entry in entries:
        if entry.is_symlink() or not entry.is_dir():
            raise ReceivableLedgerRecoveryRequired(
                f"Unexpected entry in transaction directory: {entry}"
            )
        try:
            paths = resolve_receivable_transaction_paths(
                receivables_directory, entry.name
            )
        except ValueError as exc:
            raise ReceivableLedgerRecoveryRequired(
                f"Unsafe transaction workspace name: {entry}"
            ) from exc
        marker = None
        try:
            marker = read_transaction_marker(paths.marker_path)
            _validate_transaction_recovery_paths(paths, marker)
        except ReceivableLedgerError as exc:
            _raise_transaction_recovery_required(paths, marker, str(exc))
        records.append((paths, marker))
    records.sort(key=lambda item: (str(item[1]["created_at"]), item[0].workspace_directory.name))
    return records


def _recover_receivable_ledger_transactions_locked(
    receivables_directory: str | os.PathLike[str],
) -> tuple[ReceivableLedgerTransactionResult, ...]:
    records = _transaction_workspace_records(receivables_directory)
    nonterminal = [
        record for record in records if record[1]["state"] != "COMMITTED"
    ]
    if len(nonterminal) > 1:
        transaction_ids = ", ".join(
            record[0].workspace_directory.name for record in nonterminal
        )
        raise ReceivableLedgerRecoveryRequired(
            "Multiple nonterminal receivable transactions require manual "
            f"inspection: {transaction_ids}"
        )

    results = []
    for paths, _ in records:
        results.append(_recover_transaction_workspace_locked(paths))
    return tuple(results)


def recover_receivable_ledger_transactions(
    receivables_directory: str | os.PathLike[str] = (
        DEFAULT_RECEIVABLES_DIRECTORY
    ),
    *,
    lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    lock_poll_interval_seconds: float = DEFAULT_LOCK_POLL_INTERVAL_SECONDS,
) -> tuple[ReceivableLedgerTransactionResult, ...]:
    """Recover pending 2-file transactions while holding the ledger lock."""

    with receivable_ledger_lock(
        receivables_directory,
        timeout_seconds=lock_timeout_seconds,
        poll_interval_seconds=lock_poll_interval_seconds,
    ):
        return _recover_receivable_ledger_transactions_locked(
            receivables_directory
        )


def _read_and_verify_transaction_before_targets(
    receivables_directory: str | os.PathLike[str],
    current_before_bytes: bytes,
    history_before_bytes: bytes,
) -> None:
    ledger_paths = resolve_receivable_ledger_paths(receivables_directory)
    try:
        actual_current = ledger_paths.current_path.read_bytes()
        actual_history = ledger_paths.history_path.read_bytes()
    except FileNotFoundError as exc:
        raise ReceivableLedgerConflictError(
            f"A required ledger target is missing: {exc.filename}"
        ) from exc
    except OSError as exc:
        raise ReceivableLedgerError(
            "Could not read ledger targets before transaction"
        ) from exc

    if actual_current != current_before_bytes:
        raise ReceivableLedgerConflictError(
            "current.csv no longer matches supplied before bytes"
        )
    if actual_history != history_before_bytes:
        raise ReceivableLedgerConflictError(
            "receivable_history.csv no longer matches supplied before bytes"
        )


def _reject_existing_transaction_id(
    receivables_directory: str | os.PathLike[str],
    transaction_id: str,
) -> None:
    paths = resolve_receivable_transaction_paths(
        receivables_directory, transaction_id
    )
    if not paths.workspace_directory.exists():
        return
    try:
        marker = read_transaction_marker(paths.marker_path)
    except ReceivableLedgerRecoveryError as exc:
        raise ReceivableLedgerDuplicateTransactionError(
            f"transaction_id already has an unreadable workspace: {transaction_id}"
        ) from exc
    if marker["state"] == "RECOVERY_REQUIRED":
        raise ReceivableLedgerRecoveryRequired(
            _recovery_required_message(
                paths,
                str(marker.get("last_error") or "existing recovery required"),
            )
        )
    raise ReceivableLedgerDuplicateTransactionError(
        f"transaction_id already exists with state={marker['state']}: "
        f"{transaction_id}"
    )


def _commit_transaction_targets_locked(
    paths: ReceivableTransactionPaths,
    marker: Mapping[str, Any],
) -> ReceivableLedgerTransactionResult:
    ledger_paths = resolve_receivable_ledger_paths(
        paths.receivables_directory
    )
    roll_forward_from_artifact(
        paths.current_after_artifact,
        marker["current_after_hash"],
        ledger_paths.current_path,
    )
    marker = transition_transaction_marker(
        paths.marker_path, "CURRENT_REPLACED"
    )

    roll_forward_from_artifact(
        paths.history_after_artifact,
        marker["history_after_hash"],
        ledger_paths.history_path,
    )
    marker = transition_transaction_marker(
        paths.marker_path, "HISTORY_REPLACED"
    )

    _verify_final_after_hashes(paths, marker)
    marker = transition_transaction_marker(paths.marker_path, "COMMITTED")
    cleaned = _cleanup_committed_workspace_best_effort(paths)
    return _transaction_result(
        marker,
        state="COMMITTED",
        workspace_cleaned=cleaned,
        recovered=False,
    )


def commit_receivable_ledger_transaction(
    receivables_directory: str | os.PathLike[str],
    transaction_id: str,
    *,
    current_before_bytes: bytes,
    current_after_bytes: bytes,
    history_before_bytes: bytes,
    history_after_bytes: bytes,
    settlement_id: str | None = None,
    lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    lock_poll_interval_seconds: float = DEFAULT_LOCK_POLL_INTERVAL_SECONDS,
) -> ReceivableLedgerTransactionResult:
    """Commit exact current/history bytes as one recoverable transaction."""

    byte_values = (
        current_before_bytes,
        current_after_bytes,
        history_before_bytes,
        history_after_bytes,
    )
    if not all(isinstance(value, bytes) for value in byte_values):
        raise TypeError("all before/after ledger values must be bytes")

    with receivable_ledger_lock(
        receivables_directory,
        timeout_seconds=lock_timeout_seconds,
        poll_interval_seconds=lock_poll_interval_seconds,
    ):
        _reject_existing_transaction_id(receivables_directory, transaction_id)
        _recover_receivable_ledger_transactions_locked(receivables_directory)
        _read_and_verify_transaction_before_targets(
            receivables_directory,
            current_before_bytes,
            history_before_bytes,
        )

        paths = resolve_receivable_transaction_paths(
            receivables_directory, transaction_id
        )
        try:
            paths, marker = prepare_transaction_artifacts(
                receivables_directory,
                transaction_id,
                current_before_bytes=current_before_bytes,
                current_after_bytes=current_after_bytes,
                history_before_bytes=history_before_bytes,
                history_after_bytes=history_after_bytes,
                settlement_id=settlement_id,
            )
        except ReceivableLedgerError:
            if paths.workspace_directory.exists():
                try:
                    _abort_failed_prepare_in_same_call(
                        paths,
                        current_before_bytes,
                        history_before_bytes,
                    )
                except ReceivableLedgerRecoveryRequired:
                    raise
                except ReceivableLedgerError:
                    pass
            raise

        try:
            return _commit_transaction_targets_locked(paths, marker)
        except ReceivableLedgerError as original_error:
            try:
                return _recover_transaction_workspace_locked(paths)
            except ReceivableLedgerRecoveryRequired:
                raise
            except ReceivableLedgerError:
                raise original_error


def _idempotent_result_from_loaded_receipt(
    loaded: LoadedSettlementReceipt,
    receipt_path: Path,
    *,
    replayed: bool,
    workspace_cleaned: bool,
    recovered: bool,
) -> ReceivableIdempotentTransactionResult:
    receipt = loaded.receipt
    return ReceivableIdempotentTransactionResult(
        replayed=replayed,
        settlement=dict(receipt["settlement"]),
        settlement_id=receipt["settlement_id"],
        transaction_id=receipt["transaction_id"],
        current_after_hash=receipt["current_after_hash"],
        history_after_hash=receipt["history_after_hash"],
        receipt_path=receipt_path,
        receipt_hash=loaded.receipt_hash,
        workspace_cleaned=workspace_cleaned,
        recovered=recovered,
    )


def _lookup_idempotency_receipt_locked(
    receivables_directory: str | os.PathLike[str],
    idempotency_key_hash: str,
    request_hash: str,
) -> tuple[Path, LoadedSettlementReceipt | None]:
    receipt_path = resolve_settlement_receipt_path(
        receivables_directory, idempotency_key_hash
    )
    if not receipt_path.exists():
        return receipt_path, None
    loaded = read_settlement_receipt(receipt_path)
    if loaded.receipt["idempotency_key_hash"] != idempotency_key_hash:
        raise ReceivableSettlementReceiptError(
            "Settlement receipt key hash does not match its path"
        )
    if loaded.receipt["request_hash"] != request_hash:
        raise ReceivableIdempotencyConflictError(
            "idempotency_key was already used for a different request"
        )
    return receipt_path, loaded


def _commit_receipt_transaction_targets_locked(
    paths: ReceivableTransactionPaths,
    marker: Mapping[str, Any],
) -> ReceivableIdempotentTransactionResult:
    ledger_paths = resolve_receivable_ledger_paths(
        paths.receivables_directory
    )
    roll_forward_from_artifact(
        paths.current_after_artifact,
        marker["current_after_hash"],
        ledger_paths.current_path,
    )
    marker = transition_transaction_marker(
        paths.marker_path, "CURRENT_REPLACED"
    )
    roll_forward_from_artifact(
        paths.history_after_artifact,
        marker["history_after_hash"],
        ledger_paths.history_path,
    )
    marker = transition_transaction_marker(
        paths.marker_path, "HISTORY_REPLACED"
    )
    _verify_final_after_hashes(paths, marker)
    marker = _ensure_receipt_durable_locked(paths, marker)
    marker = transition_transaction_marker(paths.marker_path, "COMMITTED")
    receipt_path = Path(marker["receipt_path"])
    loaded = read_settlement_receipt(receipt_path)
    cleaned = _cleanup_committed_workspace_best_effort(paths)
    return _idempotent_result_from_loaded_receipt(
        loaded,
        receipt_path,
        replayed=False,
        workspace_cleaned=cleaned,
        recovered=False,
    )


def commit_receivable_ledger_transaction_with_receipt(
    receivables_directory: str | os.PathLike[str],
    transaction_id: str,
    *,
    settlement_id: str,
    idempotency_key: str,
    request_payload: Any = _REQUEST_PAYLOAD_UNSET,
    request_hash: str | None = None,
    settlement_response: Mapping[str, Any],
    current_before_bytes: bytes,
    current_after_bytes: bytes,
    history_before_bytes: bytes,
    history_after_bytes: bytes,
    lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    lock_poll_interval_seconds: float = DEFAULT_LOCK_POLL_INTERVAL_SECONDS,
) -> ReceivableIdempotentTransactionResult:
    """Commit a 2-file transaction with a durable replay receipt."""

    if not isinstance(settlement_id, str) or not settlement_id:
        raise ValueError("settlement_id must be a non-empty string")
    if not isinstance(settlement_response, Mapping):
        raise ValueError("settlement_response must be a JSON-safe mapping")
    byte_values = (
        current_before_bytes,
        current_after_bytes,
        history_before_bytes,
        history_after_bytes,
    )
    if not all(isinstance(value, bytes) for value in byte_values):
        raise TypeError("all before/after ledger values must be bytes")

    key_hash = calculate_idempotency_key_hash(idempotency_key)
    resolved_request_hash = resolve_request_hash(
        request_payload=request_payload,
        request_hash=request_hash,
    )
    try:
        settlement_snapshot = json.loads(
            canonical_request_bytes(dict(settlement_response)).decode("utf-8")
        )
    except ValueError as exc:
        raise ValueError("settlement_response must be JSON-safe") from exc

    with receivable_ledger_lock(
        receivables_directory,
        timeout_seconds=lock_timeout_seconds,
        poll_interval_seconds=lock_poll_interval_seconds,
    ):
        _recover_receivable_ledger_transactions_locked(receivables_directory)
        receipt_path, existing = _lookup_idempotency_receipt_locked(
            receivables_directory, key_hash, resolved_request_hash
        )
        if existing is not None:
            return _idempotent_result_from_loaded_receipt(
                existing,
                receipt_path,
                replayed=True,
                workspace_cleaned=True,
                recovered=False,
            )

        _reject_existing_transaction_id(receivables_directory, transaction_id)
        _read_and_verify_transaction_before_targets(
            receivables_directory,
            current_before_bytes,
            history_before_bytes,
        )
        paths = resolve_receivable_transaction_paths(
            receivables_directory, transaction_id
        )
        metadata = {
            "receipt_required": True,
            "idempotency_key_hash": key_hash,
            "request_hash": resolved_request_hash,
            "receipt_path": str(receipt_path.resolve()),
            "receipt_hash": None,
            "settlement_response": settlement_snapshot,
            "committed_at": _utc_now_text(),
        }
        try:
            paths, marker = prepare_transaction_artifacts(
                receivables_directory,
                transaction_id,
                current_before_bytes=current_before_bytes,
                current_after_bytes=current_after_bytes,
                history_before_bytes=history_before_bytes,
                history_after_bytes=history_after_bytes,
                settlement_id=settlement_id,
                marker_metadata=metadata,
            )
        except ReceivableLedgerError:
            if paths.workspace_directory.exists():
                try:
                    _abort_failed_prepare_in_same_call(
                        paths,
                        current_before_bytes,
                        history_before_bytes,
                    )
                except ReceivableLedgerRecoveryRequired:
                    raise
                except ReceivableLedgerError:
                    pass
            raise

        try:
            return _commit_receipt_transaction_targets_locked(paths, marker)
        except ReceivableLedgerError as original_error:
            try:
                recovered = _recover_transaction_workspace_locked(paths)
                loaded = read_settlement_receipt(receipt_path)
                return _idempotent_result_from_loaded_receipt(
                    loaded,
                    receipt_path,
                    replayed=False,
                    workspace_cleaned=recovered.workspace_cleaned,
                    recovered=True,
                )
            except ReceivableLedgerRecoveryRequired:
                raise
            except ReceivableLedgerError:
                raise original_error

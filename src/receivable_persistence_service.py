"""Read-only persistence primitives for the receivable ledger.

This phase intentionally contains no ledger CSV write or recovery behavior.
"""

from __future__ import annotations

import errno
import hashlib
import importlib
import io
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator

import pandas as pd

from receivable_engine import CURRENT_RECEIVABLE_COLUMNS, HISTORY_COLUMNS


DEFAULT_RECEIVABLES_DIRECTORY = Path("data/receivables")
CURRENT_FILENAME = "current.csv"
HISTORY_FILENAME = "receivable_history.csv"
LEDGER_LOCK_FILENAME = ".receivable_ledger.lock"
DEFAULT_LOCK_TIMEOUT_SECONDS = 5.0
DEFAULT_LOCK_POLL_INTERVAL_SECONDS = 0.05


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


@dataclass(frozen=True)
class ReceivableLedgerPaths:
    receivables_directory: Path
    current_path: Path
    history_path: Path
    lock_path: Path


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

    return hashlib.sha256(current_raw_bytes).hexdigest()


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

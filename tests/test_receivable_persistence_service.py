import hashlib
import multiprocessing
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

import pandas as pd


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import receivable_persistence_service as service  # noqa: E402
from receivable_engine import (  # noqa: E402
    CURRENT_RECEIVABLE_COLUMNS,
    HISTORY_COLUMNS,
)
from receivable_persistence_service import (  # noqa: E402
    ReceivableLedgerLockTimeout,
    ReceivableLedgerMalformedError,
    ReceivableLedgerMissingError,
    ReceivableLedgerSchemaError,
    calculate_current_revision,
    load_current_receivables_read_only,
    load_receivable_history_or_empty_read_only,
    load_receivable_history_read_only,
    read_receivable_ledger_snapshot,
    receivable_ledger_lock,
    resolve_receivable_ledger_paths,
)


def _cross_process_lock_attempt(
    source_directory,
    receivables_directory,
    timeout_seconds,
    result_queue,
):
    if source_directory not in sys.path:
        sys.path.insert(0, source_directory)
    from receivable_persistence_service import (
        ReceivableLedgerLockTimeout as ChildLockTimeout,
    )
    from receivable_persistence_service import receivable_ledger_lock as child_lock

    try:
        with child_lock(
            receivables_directory,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=0.01,
        ):
            result_queue.put("acquired")
    except ChildLockTimeout:
        result_queue.put("timeout")


def _current_row(customer="A商事"):
    return {
        "コード": "C001",
        "未収ID": "R001",
        "得意先名": customer,
        "請求日": "2026-08-01",
        "入金予定日": "2026-08-31",
        "未収科目": "未収運賃",
        "未収補助": "",
        "部門": "営業部",
        "摘要": "8月分",
        "請求金額": "1000",
        "入金済額": "0",
        "残高": "1000",
        "ステータス": "未処理",
    }


def _history_row():
    return {
        "消込ID": "S001",
        "消込日": "2026-08-30",
        "得意先名": "A商事",
        "コード": "C001",
        "消込額": "1000",
        "消込前残高": "1000",
        "消込後残高": "0",
        "仕訳登録済": "0",
    }


class ReceivablePersistenceServiceTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.receivables_directory = Path(self.temporary_directory.name)
        self.paths = resolve_receivable_ledger_paths(
            self.receivables_directory
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def write_current(self, rows=None, columns=None, encoding="utf-8-sig"):
        if rows is None:
            rows = [_current_row()]
        if columns is None:
            columns = CURRENT_RECEIVABLE_COLUMNS
        pd.DataFrame(rows, columns=columns).to_csv(
            self.paths.current_path,
            index=False,
            encoding=encoding,
        )

    def write_history(self, rows=None, columns=None):
        if rows is None:
            rows = [_history_row()]
        if columns is None:
            columns = HISTORY_COLUMNS
        pd.DataFrame(rows, columns=columns).to_csv(
            self.paths.history_path,
            index=False,
            encoding="utf-8-sig",
        )

    def test_resolve_paths_has_no_filesystem_side_effect(self):
        missing_directory = self.receivables_directory / "missing"
        paths = resolve_receivable_ledger_paths(missing_directory)

        self.assertEqual(paths.current_path, missing_directory / "current.csv")
        self.assertFalse(missing_directory.exists())

    def test_current_loader_returns_exact_raw_bytes(self):
        self.write_current()
        expected = self.paths.current_path.read_bytes()

        loaded = load_current_receivables_read_only(self.paths.current_path)

        self.assertEqual(loaded.raw_bytes, expected)
        self.assertEqual(loaded.dataframe.loc[0, "得意先名"], "A商事")

    def test_current_loader_reads_utf8_bom_and_empty_value(self):
        self.write_current()
        self.assertTrue(self.paths.current_path.read_bytes().startswith(b"\xef\xbb\xbf"))

        loaded = load_current_receivables_read_only(self.paths.current_path)

        self.assertEqual(loaded.dataframe.loc[0, "未収補助"], "")

    def test_current_schema_allows_extra_columns_and_preserves_order(self):
        columns = ["追加列"] + list(reversed(CURRENT_RECEIVABLE_COLUMNS))
        row = _current_row()
        row["追加列"] = "保持"
        self.write_current([row], columns)

        loaded = load_current_receivables_read_only(self.paths.current_path)

        self.assertEqual(list(loaded.dataframe.columns), columns)
        self.assertEqual(loaded.dataframe.loc[0, "追加列"], "保持")

    def test_current_missing_required_column_raises_schema_error(self):
        columns = [
            column for column in CURRENT_RECEIVABLE_COLUMNS if column != "未収ID"
        ]
        self.write_current(columns=columns)

        with self.assertRaises(ReceivableLedgerSchemaError):
            load_current_receivables_read_only(self.paths.current_path)

    def test_current_missing_raises_missing_error(self):
        with self.assertRaises(ReceivableLedgerMissingError):
            load_current_receivables_read_only(self.paths.current_path)

    def test_malformed_csv_raises_malformed_error(self):
        self.paths.current_path.write_bytes(
            b'"unterminated header\nvalue\n'
        )

        with self.assertRaises(ReceivableLedgerMalformedError):
            load_current_receivables_read_only(self.paths.current_path)

    def test_invalid_utf8_raises_malformed_error(self):
        self.paths.current_path.write_bytes(b"\xff\xfe\x00")

        with self.assertRaises(ReceivableLedgerMalformedError):
            load_current_receivables_read_only(self.paths.current_path)

    def test_same_raw_bytes_produce_same_revision(self):
        raw_bytes = b"same bytes"
        self.assertEqual(
            calculate_current_revision(raw_bytes),
            calculate_current_revision(raw_bytes),
        )

    def test_one_byte_change_changes_revision(self):
        self.assertNotEqual(
            calculate_current_revision(b"abc"),
            calculate_current_revision(b"abd"),
        )

    def test_other_customer_change_changes_revision(self):
        first = pd.DataFrame(
            [_current_row("A商事"), _current_row("B商事")],
            columns=CURRENT_RECEIVABLE_COLUMNS,
        ).to_csv(index=False).encode("utf-8-sig")
        second = first.replace("B商事".encode(), "C商事".encode())

        self.assertNotEqual(
            calculate_current_revision(first),
            calculate_current_revision(second),
        )

    def test_revision_is_exact_sha256_of_raw_bytes(self):
        self.write_current()
        raw_bytes = self.paths.current_path.read_bytes()

        self.assertEqual(
            calculate_current_revision(raw_bytes),
            hashlib.sha256(raw_bytes).hexdigest(),
        )

    def test_history_loader_validates_eight_columns(self):
        self.write_history()

        loaded = load_receivable_history_read_only(self.paths.history_path)

        self.assertEqual(list(loaded.dataframe.columns), HISTORY_COLUMNS)
        self.assertEqual(loaded.dataframe.loc[0, "消込ID"], "S001")

    def test_history_missing_strict_loader_raises(self):
        with self.assertRaises(ReceivableLedgerMissingError):
            load_receivable_history_read_only(self.paths.history_path)

    def test_history_missing_explicit_helper_returns_empty(self):
        loaded, was_missing = load_receivable_history_or_empty_read_only(
            self.paths.history_path
        )

        self.assertTrue(was_missing)
        self.assertTrue(loaded.dataframe.empty)
        self.assertEqual(list(loaded.dataframe.columns), HISTORY_COLUMNS)
        self.assertEqual(loaded.raw_bytes, b"")

    def test_history_missing_column_raises_schema_error(self):
        columns = [column for column in HISTORY_COLUMNS if column != "消込ID"]
        self.write_history(columns=columns)

        with self.assertRaises(ReceivableLedgerSchemaError):
            load_receivable_history_read_only(self.paths.history_path)

    def test_loaders_do_not_rewrite_files(self):
        self.write_current()
        self.write_history()
        current_before = self.paths.current_path.read_bytes()
        history_before = self.paths.history_path.read_bytes()

        load_current_receivables_read_only(self.paths.current_path)
        load_receivable_history_read_only(self.paths.history_path)

        self.assertEqual(self.paths.current_path.read_bytes(), current_before)
        self.assertEqual(self.paths.history_path.read_bytes(), history_before)

    def test_loader_does_not_migrate_or_fill_empty_receivable_id(self):
        row = _current_row()
        row["未収ID"] = ""
        self.write_current([row])

        loaded = load_current_receivables_read_only(self.paths.current_path)

        self.assertEqual(loaded.dataframe.loc[0, "未収ID"], "")

    def test_snapshot_reads_current_and_missing_history_under_lock(self):
        self.write_current()
        raw_bytes = self.paths.current_path.read_bytes()

        snapshot = read_receivable_ledger_snapshot(
            self.receivables_directory,
            lock_timeout_seconds=0.5,
            lock_poll_interval_seconds=0.01,
        )

        self.assertEqual(snapshot.current_raw_bytes, raw_bytes)
        self.assertIsNone(snapshot.history_raw_bytes)
        self.assertTrue(snapshot.history_df.empty)
        self.assertEqual(
            snapshot.ledger_revision,
            hashlib.sha256(raw_bytes).hexdigest(),
        )

    def test_lock_can_be_acquired_and_leaves_lock_file(self):
        with receivable_ledger_lock(self.receivables_directory):
            self.assertTrue(self.paths.lock_path.exists())

        self.assertTrue(self.paths.lock_path.exists())

    def test_same_process_second_lock_times_out(self):
        result = []

        def attempt_lock():
            try:
                with receivable_ledger_lock(
                    self.receivables_directory,
                    timeout_seconds=0.1,
                    poll_interval_seconds=0.01,
                ):
                    result.append("acquired")
            except ReceivableLedgerLockTimeout:
                result.append("timeout")

        with receivable_ledger_lock(self.receivables_directory):
            thread = threading.Thread(target=attempt_lock)
            thread.start()
            thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result, ["timeout"])

    def test_lock_release_allows_next_acquisition(self):
        with receivable_ledger_lock(self.receivables_directory):
            pass
        with receivable_ledger_lock(
            self.receivables_directory,
            timeout_seconds=0.1,
        ):
            acquired_again = True

        self.assertTrue(acquired_again)

    def test_exception_inside_context_releases_lock(self):
        with self.assertRaisesRegex(RuntimeError, "body failed"):
            with receivable_ledger_lock(self.receivables_directory):
                raise RuntimeError("body failed")

        with receivable_ledger_lock(
            self.receivables_directory,
            timeout_seconds=0.1,
        ):
            acquired_after_error = True

        self.assertTrue(acquired_after_error)

    def test_existing_unlocked_lock_file_does_not_block(self):
        self.paths.lock_path.write_bytes(b"stale marker content")

        with receivable_ledger_lock(
            self.receivables_directory,
            timeout_seconds=0.1,
        ):
            acquired = True

        self.assertTrue(acquired)

    def test_negative_timeout_is_rejected(self):
        with self.assertRaises(ValueError):
            with receivable_ledger_lock(
                self.receivables_directory,
                timeout_seconds=-1,
            ):
                pass

    def test_windows_adapter_locks_first_byte_and_unlocks(self):
        calls = []
        fake_msvcrt = SimpleNamespace(
            LK_NBLCK=1,
            LK_UNLCK=2,
            locking=lambda descriptor, mode, count: calls.append(
                (descriptor, mode, count)
            ),
        )
        with self.paths.lock_path.open("a+b") as file_handle:
            self.assertTrue(
                service._try_acquire_windows_lock(file_handle, fake_msvcrt)
            )
            service._release_windows_lock(file_handle, fake_msvcrt)

        self.assertEqual(calls[0][1:], (fake_msvcrt.LK_NBLCK, 1))
        self.assertEqual(calls[1][1:], (fake_msvcrt.LK_UNLCK, 1))
        self.assertGreaterEqual(self.paths.lock_path.stat().st_size, 1)

    def test_unix_adapter_uses_nonblocking_exclusive_lock(self):
        calls = []
        fake_fcntl = SimpleNamespace(
            LOCK_EX=1,
            LOCK_NB=2,
            LOCK_UN=4,
            flock=lambda descriptor, operation: calls.append(
                (descriptor, operation)
            ),
        )
        with self.paths.lock_path.open("a+b") as file_handle:
            self.assertTrue(
                service._try_acquire_unix_lock(file_handle, fake_fcntl)
            )
            service._release_unix_lock(file_handle, fake_fcntl)

        self.assertEqual(calls[0][1], fake_fcntl.LOCK_EX | fake_fcntl.LOCK_NB)
        self.assertEqual(calls[1][1], fake_fcntl.LOCK_UN)

    def test_cross_process_lock_times_out_then_acquires_after_release(self):
        context = multiprocessing.get_context("spawn")
        first_queue = context.Queue()

        with receivable_ledger_lock(self.receivables_directory):
            blocked_process = context.Process(
                target=_cross_process_lock_attempt,
                args=(
                    str(SRC_DIR),
                    str(self.receivables_directory),
                    0.2,
                    first_queue,
                ),
            )
            blocked_process.start()
            blocked_process.join(timeout=5)

        self.assertFalse(blocked_process.is_alive())
        self.assertEqual(blocked_process.exitcode, 0)
        self.assertEqual(first_queue.get(timeout=1), "timeout")

        second_queue = context.Queue()
        acquiring_process = context.Process(
            target=_cross_process_lock_attempt,
            args=(
                str(SRC_DIR),
                str(self.receivables_directory),
                1.0,
                second_queue,
            ),
        )
        acquiring_process.start()
        acquiring_process.join(timeout=5)

        self.assertFalse(acquiring_process.is_alive())
        self.assertEqual(acquiring_process.exitcode, 0)
        self.assertEqual(second_queue.get(timeout=1), "acquired")


if __name__ == "__main__":
    unittest.main()

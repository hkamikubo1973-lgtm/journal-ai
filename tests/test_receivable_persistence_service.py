import hashlib
import io
import json
import multiprocessing
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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
    ReceivableLedgerRecoveryError,
    ReceivableLedgerRecoveryRequired,
    ReceivableLedgerSchemaError,
    ReceivableLedgerVerificationError,
    ReceivableLedgerWriteError,
    ReceivableSettlementReceiptError,
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

    def prepare_artifacts(self, transaction_id="tx-001"):
        before_current = b"current-before\r\n"
        after_current = b"current-after\r\n"
        before_history = b"history-before\r\n"
        after_history = b"history-after\r\n"
        paths, marker = service.prepare_transaction_artifacts(
            self.receivables_directory,
            transaction_id,
            current_before_bytes=before_current,
            current_after_bytes=after_current,
            history_before_bytes=before_history,
            history_after_bytes=after_history,
            settlement_id="settlement-001",
        )
        contents = {
            "current_before": before_current,
            "current_after": after_current,
            "history_before": before_history,
            "history_after": after_history,
        }
        return paths, marker, contents

    def coordinator_contents(self):
        return {
            "current_before": "\ufeffコード,摘要\r\n1,旧\r\n".encode("utf-8"),
            "current_after": "\ufeffコード,摘要\r\n1,新\r\n".encode("utf-8"),
            "history_before": "\ufeff消込ID,摘要\r\nS0,旧\r\n".encode("utf-8"),
            "history_after": "\ufeff消込ID,摘要\r\nS0,旧\r\nS1,新\r\n".encode("utf-8"),
        }

    def write_coordinator_before_targets(self, contents=None):
        if contents is None:
            contents = self.coordinator_contents()
        self.paths.current_path.write_bytes(contents["current_before"])
        self.paths.history_path.write_bytes(contents["history_before"])
        return contents

    def commit_coordinator(self, transaction_id="tx-coordinator", contents=None):
        if contents is None:
            contents = self.coordinator_contents()
        return service.commit_receivable_ledger_transaction(
            self.receivables_directory,
            transaction_id,
            current_before_bytes=contents["current_before"],
            current_after_bytes=contents["current_after"],
            history_before_bytes=contents["history_before"],
            history_after_bytes=contents["history_after"],
            settlement_id="settlement-coordinator",
            lock_timeout_seconds=0.5,
            lock_poll_interval_seconds=0.01,
        )

    def settlement_response(self, difference=-100):
        return {
            "settlement_id": "settlement-receipt",
            "settlement_date": "2026-08-31",
            "customer_name": "日本商事",
            "payment_amount": 900,
            "target_total": 1000,
            "difference": difference,
            "source_candidates": [
                {"未収ID": "R1", "消込予定": 600},
                {"未収ID": "R2", "消込予定": 300},
            ],
            "rows": [
                {"借方科目": "普通預金", "金額": 900},
                {"貸方科目": "未収金", "金額": 900},
            ],
            "created_at": "2026-08-31T10:00:00+09:00",
        }

    def commit_with_receipt(
        self,
        transaction_id="tx-receipt",
        key="client-operation-001",
        request_payload=None,
        contents=None,
        settlement=None,
    ):
        if contents is None:
            contents = self.coordinator_contents()
        if request_payload is None:
            request_payload = {"customer": "日本商事", "amount": 900}
        if settlement is None:
            settlement = self.settlement_response()
        return service.commit_receivable_ledger_transaction_with_receipt(
            self.receivables_directory,
            transaction_id,
            settlement_id="settlement-receipt",
            idempotency_key=key,
            request_payload=request_payload,
            settlement_response=settlement,
            current_before_bytes=contents["current_before"],
            current_after_bytes=contents["current_after"],
            history_before_bytes=contents["history_before"],
            history_after_bytes=contents["history_after"],
            lock_timeout_seconds=0.5,
            lock_poll_interval_seconds=0.01,
        )

    def prepare_receipt_artifacts(
        self,
        transaction_id="tx-receipt-crash",
        key=None,
        request_payload=None,
        settlement=None,
    ):
        if request_payload is None:
            request_payload = {"amount": 900}
        if settlement is None:
            settlement = self.settlement_response()
        contents = self.write_coordinator_before_targets()
        if key is None:
            key = f"crash-key-{transaction_id}"
        key_hash = service.calculate_idempotency_key_hash(key)
        receipt_path = service.resolve_settlement_receipt_path(
            self.receivables_directory, key_hash
        )
        metadata = {
            "receipt_required": True,
            "idempotency_key_hash": key_hash,
            "request_hash": service.calculate_request_hash(request_payload),
            "receipt_path": str(receipt_path.resolve()),
            "receipt_hash": None,
            "settlement_response": settlement,
            "committed_at": "2026-08-31T00:00:00+00:00",
        }
        paths, marker = service.prepare_transaction_artifacts(
            self.receivables_directory,
            transaction_id,
            current_before_bytes=contents["current_before"],
            current_after_bytes=contents["current_after"],
            history_before_bytes=contents["history_before"],
            history_after_bytes=contents["history_after"],
            settlement_id="settlement-receipt",
            marker_metadata=metadata,
        )
        return paths, marker, contents, receipt_path

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

    def test_locked_snapshot_matches_public_snapshot_contract(self):
        self.write_current()
        self.write_history()
        with receivable_ledger_lock(self.receivables_directory):
            snapshot = service._read_receivable_ledger_snapshot_locked(
                self.receivables_directory,
                history_missing_as_empty=False,
            )

        self.assertEqual(
            snapshot.current_raw_bytes, self.paths.current_path.read_bytes()
        )
        self.assertEqual(
            snapshot.history_raw_bytes, self.paths.history_path.read_bytes()
        )

    def test_empty_history_bytes_are_bom_header_only_and_strictly_loadable(self):
        raw_bytes = service.build_empty_receivable_history_bytes()
        dataframe = pd.read_csv(io.BytesIO(raw_bytes), dtype=str)

        self.assertTrue(raw_bytes.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(list(dataframe.columns), HISTORY_COLUMNS)
        self.assertTrue(dataframe.empty)

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

    def test_atomic_writer_creates_new_file_with_exact_hash_and_bytes(self):
        target = self.receivables_directory / "new.csv"
        content = b"header\r\nvalue\r\n"

        persisted_hash = service.atomic_write_bytes(target, content)

        self.assertEqual(target.read_bytes(), content)
        self.assertEqual(persisted_hash, hashlib.sha256(content).hexdigest())

    def test_atomic_writer_replaces_existing_file(self):
        target = self.receivables_directory / "existing.csv"
        target.write_bytes(b"old")

        service.atomic_write_bytes(target, b"new")

        self.assertEqual(target.read_bytes(), b"new")

    def test_atomic_writer_preserves_bom_japanese_and_newlines(self):
        target = self.receivables_directory / "exact.csv"
        content = "列1,列2\r\n日本語,値\r\n".encode("utf-8-sig")

        service.atomic_write_bytes(target, content)

        self.assertEqual(target.read_bytes(), content)
        self.assertTrue(target.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_atomic_writer_temp_is_in_target_directory(self):
        target = self.receivables_directory / "same-dir.csv"
        replace_paths = []
        real_replace = os.replace

        def recording_replace(source, destination):
            replace_paths.append((Path(source), Path(destination)))
            real_replace(source, destination)

        with patch.object(service.os, "replace", side_effect=recording_replace):
            service.atomic_write_bytes(target, b"content")

        self.assertEqual(replace_paths[0][0].parent, target.parent)
        self.assertEqual(replace_paths[0][1], target)

    def test_replace_failure_preserves_old_target_and_cleans_temp(self):
        target = self.receivables_directory / "replace-failure.csv"
        target.write_bytes(b"old")

        with patch.object(
            service.os,
            "replace",
            side_effect=PermissionError("blocked"),
        ):
            with self.assertRaises(ReceivableLedgerWriteError):
                service.atomic_write_bytes(target, b"new")

        self.assertEqual(target.read_bytes(), b"old")
        self.assertEqual(list(target.parent.glob(f".{target.name}.*.tmp")), [])

    def test_temp_verification_failure_preserves_old_target(self):
        target = self.receivables_directory / "verify-failure.csv"
        target.write_bytes(b"old")

        with patch.object(
            service,
            "_verify_file_bytes",
            side_effect=ReceivableLedgerVerificationError("bad temp"),
        ):
            with self.assertRaises(ReceivableLedgerVerificationError):
                service.atomic_write_bytes(target, b"new")

        self.assertEqual(target.read_bytes(), b"old")
        self.assertEqual(list(target.parent.glob(f".{target.name}.*.tmp")), [])

    def test_target_readback_mismatch_raises_verification_error(self):
        target = self.receivables_directory / "target-mismatch.csv"

        with patch.object(
            service,
            "_verify_file_bytes",
            side_effect=[
                None,
                ReceivableLedgerVerificationError("bad target"),
            ],
        ):
            with self.assertRaises(ReceivableLedgerVerificationError):
                service.atomic_write_bytes(target, b"new")

        self.assertEqual(target.read_bytes(), b"new")

    def test_failure_before_temp_write_cleans_temp(self):
        target = self.receivables_directory / "before-write.csv"

        with patch.object(
            service,
            "_write_all_and_fsync",
            side_effect=OSError("write failed"),
        ):
            with self.assertRaises(ReceivableLedgerWriteError):
                service.atomic_write_bytes(target, b"new")

        self.assertFalse(target.exists())
        self.assertEqual(list(target.parent.glob(f".{target.name}.*.tmp")), [])

    def test_partial_temp_write_failure_preserves_old_target(self):
        target = self.receivables_directory / "partial.csv"
        target.write_bytes(b"old")

        def partial_write(file_handle, content):
            file_handle.write(content[:2])
            file_handle.flush()
            raise OSError("partial write")

        with patch.object(
            service,
            "_write_all_and_fsync",
            side_effect=partial_write,
        ):
            with self.assertRaises(ReceivableLedgerWriteError):
                service.atomic_write_bytes(target, b"new")

        self.assertEqual(target.read_bytes(), b"old")
        self.assertEqual(list(target.parent.glob(f".{target.name}.*.tmp")), [])

    def test_file_fsync_failure_preserves_old_target(self):
        target = self.receivables_directory / "fsync.csv"
        target.write_bytes(b"old")

        with patch.object(service.os, "fsync", side_effect=OSError("fsync")):
            with self.assertRaises(ReceivableLedgerWriteError):
                service.atomic_write_bytes(target, b"new")

        self.assertEqual(target.read_bytes(), b"old")

    def test_transaction_path_resolution_has_no_side_effect(self):
        paths = service.resolve_receivable_transaction_paths(
            self.receivables_directory, "tx-path"
        )

        self.assertEqual(paths.marker_path.name, "marker.json")
        self.assertFalse(paths.workspace_directory.exists())

    def test_transaction_id_rejects_path_traversal(self):
        for unsafe_id in (
            "../escape",
            "tx..escape",
            "C:drive",
            "unsafe/name",
            "unsafe\\name",
            "日本語ID",
        ):
            with self.subTest(transaction_id=unsafe_id):
                with self.assertRaises(ValueError):
                    service.resolve_receivable_transaction_paths(
                        self.receivables_directory, unsafe_id
                    )

    def test_workspace_creation_writes_preparing_marker(self):
        paths, marker = service.create_transaction_workspace(
            self.receivables_directory,
            "tx-preparing",
            settlement_id="消込-001",
        )

        self.assertTrue(paths.workspace_directory.is_dir())
        self.assertEqual(marker["state"], "PREPARING")
        self.assertIsNone(marker["decision"])
        self.assertEqual(marker["settlement_id"], "消込-001")
        self.assertEqual(
            service.read_transaction_marker(paths.marker_path), marker
        )

    def test_prepare_saves_four_exact_artifacts_and_hashes(self):
        paths, marker, contents = self.prepare_artifacts()

        for prefix, content in contents.items():
            artifact = getattr(paths, f"{prefix}_artifact")
            self.assertEqual(artifact.read_bytes(), content)
            self.assertEqual(
                marker[f"{prefix}_hash"], hashlib.sha256(content).hexdigest()
            )
            self.assertEqual(marker[f"{prefix}_size"], len(content))
        self.assertEqual(marker["state"], "READY_TO_COMMIT")
        self.assertEqual(marker["decision"], "COMMIT")

    def test_marker_is_utf8_human_readable_json(self):
        paths, _ = service.create_transaction_workspace(
            self.receivables_directory,
            "tx-json",
            settlement_id="消込-日本語",
        )

        raw_bytes = paths.marker_path.read_bytes()
        decoded = raw_bytes.decode("utf-8")

        self.assertIn("消込-日本語", decoded)
        self.assertEqual(json.loads(decoded)["state"], "PREPARING")

    def test_marker_update_uses_same_directory_atomic_replace(self):
        paths, _ = service.create_transaction_workspace(
            self.receivables_directory, "tx-marker-atomic"
        )
        replacements = []
        real_replace = os.replace

        def recording_replace(source, destination):
            replacements.append((Path(source), Path(destination)))
            real_replace(source, destination)

        with patch.object(service.os, "replace", side_effect=recording_replace):
            service.transition_transaction_marker(
                paths.marker_path,
                "PREPARING",
                decision="ROLLBACK",
            )

        self.assertEqual(replacements[0][0].parent, paths.marker_path.parent)
        self.assertEqual(replacements[0][1], paths.marker_path)

    def test_malformed_marker_raises_recovery_error(self):
        marker_path = self.receivables_directory / "marker.json"
        marker_path.write_bytes(b"{not-json")

        with self.assertRaises(ReceivableLedgerRecoveryError):
            service.read_transaction_marker(marker_path)

    def test_missing_marker_raises_recovery_error(self):
        with self.assertRaises(ReceivableLedgerRecoveryError):
            service.read_transaction_marker(
                self.receivables_directory / "missing-marker.json"
            )

    def test_unknown_marker_state_is_rejected(self):
        paths, marker = service.create_transaction_workspace(
            self.receivables_directory, "tx-unknown-state"
        )
        marker["state"] = "UNKNOWN_STATE"

        with self.assertRaises(ReceivableLedgerRecoveryError):
            service.write_transaction_marker(paths.marker_path, marker)

    def test_ready_marker_without_all_artifacts_is_rejected(self):
        paths, marker = service.create_transaction_workspace(
            self.receivables_directory, "tx-not-ready"
        )
        marker["state"] = "READY_TO_COMMIT"
        marker["decision"] = "COMMIT"

        with self.assertRaises(ReceivableLedgerRecoveryError):
            service.write_transaction_marker(paths.marker_path, marker)

    def test_marker_temp_write_failure_does_not_create_marker(self):
        paths = service.resolve_receivable_transaction_paths(
            self.receivables_directory, "tx-marker-write-failure"
        )

        with patch.object(
            service,
            "_write_all_and_fsync",
            side_effect=OSError("marker write"),
        ):
            with self.assertRaises(ReceivableLedgerWriteError):
                service.create_transaction_workspace(
                    self.receivables_directory,
                    "tx-marker-write-failure",
                )

        self.assertFalse(paths.marker_path.exists())
        self.assertEqual(list(paths.workspace_directory.glob("*.tmp")), [])

    def test_marker_replace_failure_does_not_create_marker(self):
        paths = service.resolve_receivable_transaction_paths(
            self.receivables_directory, "tx-marker-replace-failure"
        )

        with patch.object(
            service.os,
            "replace",
            side_effect=PermissionError("marker replace"),
        ):
            with self.assertRaises(ReceivableLedgerWriteError):
                service.create_transaction_workspace(
                    self.receivables_directory,
                    "tx-marker-replace-failure",
                )

        self.assertFalse(paths.marker_path.exists())

    def test_marker_update_replace_failure_preserves_old_marker(self):
        paths, original = service.create_transaction_workspace(
            self.receivables_directory, "tx-marker-update-failure"
        )
        original_bytes = paths.marker_path.read_bytes()

        with patch.object(
            service.os,
            "replace",
            side_effect=PermissionError("marker update replace"),
        ):
            with self.assertRaises(ReceivableLedgerWriteError):
                service.transition_transaction_marker(
                    paths.marker_path,
                    "PREPARING",
                    decision="ROLLBACK",
                )

        self.assertEqual(paths.marker_path.read_bytes(), original_bytes)
        self.assertEqual(
            service.read_transaction_marker(paths.marker_path), original
        )

    def test_partial_artifact_failure_keeps_marker_preparing(self):
        real_atomic_write = service.atomic_write_bytes
        artifact_writes = 0

        def fail_during_artifacts(target_path, content):
            nonlocal artifact_writes
            if Path(target_path).suffix == ".csv":
                artifact_writes += 1
                if artifact_writes == 3:
                    raise ReceivableLedgerWriteError("artifact failure")
            return real_atomic_write(target_path, content)

        with patch.object(
            service,
            "atomic_write_bytes",
            side_effect=fail_during_artifacts,
        ):
            with self.assertRaises(ReceivableLedgerWriteError):
                service.prepare_transaction_artifacts(
                    self.receivables_directory,
                    "tx-partial-artifacts",
                    current_before_bytes=b"cb",
                    current_after_bytes=b"ca",
                    history_before_bytes=b"hb",
                    history_after_bytes=b"ha",
                )

        paths = service.resolve_receivable_transaction_paths(
            self.receivables_directory, "tx-partial-artifacts"
        )
        marker = service.read_transaction_marker(paths.marker_path)
        self.assertEqual(marker["state"], "PREPARING")
        self.assertIsNone(marker["decision"])

    def test_ready_marker_rejects_fake_artifact_metadata(self):
        paths, marker = service.create_transaction_workspace(
            self.receivables_directory, "tx-fake-artifacts"
        )
        marker.update(
            {
                "state": "READY_TO_COMMIT",
                "decision": "COMMIT",
                "current_before_hash": "a" * 64,
                "current_after_hash": "b" * 64,
                "history_before_hash": "c" * 64,
                "history_after_hash": "d" * 64,
            }
        )

        with self.assertRaises(ReceivableLedgerRecoveryError):
            service.write_transaction_marker(paths.marker_path, marker)

    def assert_recovery_classification(
        self,
        current_content,
        history_content,
        expected,
        transaction_id,
    ):
        _, marker, _ = self.prepare_artifacts(transaction_id)
        self.paths.current_path.write_bytes(current_content)
        self.paths.history_path.write_bytes(history_content)

        classification = service.classify_recovery_state(
            self.paths.current_path,
            self.paths.history_path,
            marker,
        )

        self.assertEqual(classification, expected)

    def test_recovery_classifies_both_before(self):
        self.assert_recovery_classification(
            b"current-before\r\n",
            b"history-before\r\n",
            service.RECOVERY_BOTH_BEFORE,
            "tx-both-before",
        )

    def test_recovery_classifies_current_after_history_before(self):
        self.assert_recovery_classification(
            b"current-after\r\n",
            b"history-before\r\n",
            service.RECOVERY_CURRENT_AFTER_HISTORY_BEFORE,
            "tx-current-after",
        )

    def test_recovery_classifies_current_before_history_after(self):
        self.assert_recovery_classification(
            b"current-before\r\n",
            b"history-after\r\n",
            service.RECOVERY_CURRENT_BEFORE_HISTORY_AFTER,
            "tx-history-after",
        )

    def test_recovery_classifies_both_after(self):
        self.assert_recovery_classification(
            b"current-after\r\n",
            b"history-after\r\n",
            service.RECOVERY_BOTH_AFTER,
            "tx-both-after",
        )

    def test_recovery_classifies_unknown_current(self):
        self.assert_recovery_classification(
            b"unknown-current",
            b"history-before\r\n",
            service.RECOVERY_UNKNOWN,
            "tx-unknown-current",
        )

    def test_recovery_classifies_unknown_history(self):
        self.assert_recovery_classification(
            b"current-before\r\n",
            b"unknown-history",
            service.RECOVERY_UNKNOWN,
            "tx-unknown-history",
        )

    def test_commit_recovery_plans_roll_forward_and_finalize(self):
        _, marker, _ = self.prepare_artifacts("tx-plan")

        self.assertEqual(
            service.plan_recovery_actions(
                marker, service.RECOVERY_BOTH_BEFORE
            ),
            ("ROLL_FORWARD_CURRENT", "ROLL_FORWARD_HISTORY"),
        )
        self.assertEqual(
            service.plan_recovery_actions(
                marker,
                service.RECOVERY_CURRENT_AFTER_HISTORY_BEFORE,
            ),
            ("ROLL_FORWARD_HISTORY",),
        )
        self.assertEqual(
            service.plan_recovery_actions(marker, service.RECOVERY_BOTH_AFTER),
            ("FINALIZE_COMMIT",),
        )

    def test_precommit_both_before_plans_abort(self):
        _, marker = service.create_transaction_workspace(
            self.receivables_directory, "tx-abort-plan"
        )

        self.assertEqual(
            service.plan_recovery_actions(
                marker, service.RECOVERY_BOTH_BEFORE
            ),
            ("ABORT",),
        )

    def test_unknown_recovery_requires_manual_intervention(self):
        paths, marker, _ = self.prepare_artifacts("tx-manual")
        self.paths.current_path.write_bytes(b"do-not-overwrite")

        with self.assertRaises(ReceivableLedgerRecoveryRequired):
            service.plan_recovery_actions(marker, service.RECOVERY_UNKNOWN)

        updated = service.mark_transaction_recovery_required(
            paths.marker_path, "unknown target hash"
        )
        self.assertEqual(updated["state"], "RECOVERY_REQUIRED")
        self.assertEqual(self.paths.current_path.read_bytes(), b"do-not-overwrite")

    def test_roll_forward_current_and_history_from_after_artifacts(self):
        paths, marker, contents = self.prepare_artifacts("tx-roll-forward")
        self.paths.current_path.write_bytes(contents["current_before"])
        self.paths.history_path.write_bytes(contents["history_before"])

        service.roll_forward_from_artifact(
            paths.current_after_artifact,
            marker["current_after_hash"],
            self.paths.current_path,
        )
        service.roll_forward_from_artifact(
            paths.history_after_artifact,
            marker["history_after_hash"],
            self.paths.history_path,
        )

        self.assertEqual(
            self.paths.current_path.read_bytes(), contents["current_after"]
        )
        self.assertEqual(
            self.paths.history_path.read_bytes(), contents["history_after"]
        )
        self.assertTrue(paths.current_after_artifact.exists())
        self.assertTrue(paths.history_after_artifact.exists())

    def test_rollback_current_and_history_from_before_artifacts(self):
        paths, marker, contents = self.prepare_artifacts("tx-rollback")
        self.paths.current_path.write_bytes(contents["current_after"])
        self.paths.history_path.write_bytes(contents["history_after"])

        service.rollback_from_artifact(
            paths.current_before_artifact,
            marker["current_before_hash"],
            self.paths.current_path,
        )
        service.rollback_from_artifact(
            paths.history_before_artifact,
            marker["history_before_hash"],
            self.paths.history_path,
        )

        self.assertEqual(
            self.paths.current_path.read_bytes(), contents["current_before"]
        )
        self.assertEqual(
            self.paths.history_path.read_bytes(), contents["history_before"]
        )

    def test_artifact_hash_mismatch_requires_recovery_and_keeps_target(self):
        paths, marker, _ = self.prepare_artifacts("tx-corrupt-artifact")
        self.paths.current_path.write_bytes(b"original-target")
        paths.current_after_artifact.write_bytes(b"corrupt")

        with self.assertRaises(ReceivableLedgerRecoveryRequired):
            service.roll_forward_from_artifact(
                paths.current_after_artifact,
                marker["current_after_hash"],
                self.paths.current_path,
            )

        self.assertEqual(self.paths.current_path.read_bytes(), b"original-target")

    def test_recovered_target_hash_exactly_matches_marker(self):
        paths, marker, _ = self.prepare_artifacts("tx-recovered-hash")

        recovered_hash = service.roll_forward_from_artifact(
            paths.current_after_artifact,
            marker["current_after_hash"],
            self.paths.current_path,
        )

        self.assertEqual(recovered_hash, marker["current_after_hash"])
        self.assertEqual(
            hashlib.sha256(self.paths.current_path.read_bytes()).hexdigest(),
            marker["current_after_hash"],
        )

    def test_crash_fixture_current_after_can_roll_history_forward(self):
        paths, marker, contents = self.prepare_artifacts("tx-crash-current")
        self.paths.current_path.write_bytes(contents["current_after"])
        self.paths.history_path.write_bytes(contents["history_before"])

        classification = service.classify_recovery_state(
            self.paths.current_path, self.paths.history_path, paths.marker_path
        )
        actions = service.plan_recovery_actions(marker, classification)
        service.roll_forward_from_artifact(
            paths.history_after_artifact,
            marker["history_after_hash"],
            self.paths.history_path,
        )

        self.assertEqual(actions, ("ROLL_FORWARD_HISTORY",))
        self.assertEqual(
            self.paths.history_path.read_bytes(), contents["history_after"]
        )

    def test_crash_fixture_history_after_can_roll_current_forward(self):
        paths, marker, contents = self.prepare_artifacts("tx-crash-history")
        self.paths.current_path.write_bytes(contents["current_before"])
        self.paths.history_path.write_bytes(contents["history_after"])

        classification = service.classify_recovery_state(
            self.paths.current_path, self.paths.history_path, paths.marker_path
        )
        actions = service.plan_recovery_actions(marker, classification)
        service.roll_forward_from_artifact(
            paths.current_after_artifact,
            marker["current_after_hash"],
            self.paths.current_path,
        )

        self.assertEqual(actions, ("ROLL_FORWARD_CURRENT",))
        self.assertEqual(
            self.paths.current_path.read_bytes(), contents["current_after"]
        )

    def test_atomic_primitives_work_inside_ledger_lock(self):
        with receivable_ledger_lock(self.receivables_directory):
            paths, marker, _ = self.prepare_artifacts("tx-under-lock")
            service.roll_forward_from_artifact(
                paths.current_after_artifact,
                marker["current_after_hash"],
                self.paths.current_path,
            )

        self.assertTrue(self.paths.current_path.exists())

    def test_committed_workspace_can_be_cleaned(self):
        paths, _, _ = self.prepare_artifacts("tx-clean-committed")
        service.transition_transaction_marker(
            paths.marker_path, "COMMITTED"
        )

        service.cleanup_transaction_workspace(paths.workspace_directory)

        self.assertFalse(paths.workspace_directory.exists())

    def test_explicitly_rolled_back_preparing_workspace_can_be_cleaned(self):
        paths, _ = service.create_transaction_workspace(
            self.receivables_directory, "tx-clean-rollback"
        )
        service.transition_transaction_marker(
            paths.marker_path,
            "PREPARING",
            decision="ROLLBACK",
        )

        service.cleanup_transaction_workspace(paths.workspace_directory)

        self.assertFalse(paths.workspace_directory.exists())

    def test_recovery_required_workspace_is_never_cleaned(self):
        paths, _, _ = self.prepare_artifacts("tx-keep-recovery")
        service.mark_transaction_recovery_required(
            paths.marker_path, "manual inspection"
        )

        with self.assertRaises(ReceivableLedgerRecoveryError):
            service.cleanup_transaction_workspace(paths.workspace_directory)

        self.assertTrue(paths.workspace_directory.exists())

    def test_coordinator_commits_two_exact_files_and_cleans_workspace(self):
        contents = self.write_coordinator_before_targets()

        result = self.commit_coordinator(contents=contents)

        self.assertEqual(result.state, "COMMITTED")
        self.assertFalse(result.recovered)
        self.assertTrue(result.workspace_cleaned)
        self.assertEqual(
            self.paths.current_path.read_bytes(), contents["current_after"]
        )
        self.assertEqual(
            self.paths.history_path.read_bytes(), contents["history_after"]
        )
        self.assertEqual(
            result.current_after_hash,
            hashlib.sha256(contents["current_after"]).hexdigest(),
        )
        self.assertEqual(
            result.history_after_hash,
            hashlib.sha256(contents["history_after"]).hexdigest(),
        )
        transaction_paths = service.resolve_receivable_transaction_paths(
            self.receivables_directory, "tx-coordinator"
        )
        self.assertFalse(transaction_paths.workspace_directory.exists())

    def test_coordinator_replaces_current_before_history(self):
        contents = self.write_coordinator_before_targets()
        target_order = []
        real_roll_forward = service.roll_forward_from_artifact

        def recording_roll_forward(artifact_path, expected_hash, target_path):
            target_order.append(Path(target_path).name)
            return real_roll_forward(artifact_path, expected_hash, target_path)

        with patch.object(
            service,
            "roll_forward_from_artifact",
            side_effect=recording_roll_forward,
        ):
            self.commit_coordinator("tx-order", contents)

        self.assertEqual(
            target_order[:2], ["current.csv", "receivable_history.csv"]
        )

    def test_marker_transitions_follow_verified_target_bytes(self):
        contents = self.write_coordinator_before_targets()
        real_transition = service.transition_transaction_marker
        observed_states = []

        def checking_transition(marker_path, state, **kwargs):
            current = self.paths.current_path.read_bytes()
            history = self.paths.history_path.read_bytes()
            if state == "CURRENT_REPLACED":
                self.assertEqual(current, contents["current_after"])
                self.assertEqual(history, contents["history_before"])
            elif state in {"HISTORY_REPLACED", "COMMITTED"}:
                self.assertEqual(current, contents["current_after"])
                self.assertEqual(history, contents["history_after"])
            observed_states.append(state)
            return real_transition(marker_path, state, **kwargs)

        with patch.object(
            service,
            "transition_transaction_marker",
            side_effect=checking_transition,
        ):
            self.commit_coordinator("tx-state-order", contents)

        self.assertEqual(
            observed_states,
            ["CURRENT_REPLACED", "HISTORY_REPLACED", "COMMITTED"],
        )

    def test_coordinator_reaches_committed_marker_before_cleanup(self):
        contents = self.write_coordinator_before_targets()

        with patch.object(service, "cleanup_transaction_workspace"):
            result = self.commit_coordinator("tx-marker-committed", contents)

        paths = service.resolve_receivable_transaction_paths(
            self.receivables_directory, "tx-marker-committed"
        )
        marker = service.read_transaction_marker(paths.marker_path)
        self.assertEqual(marker["state"], "COMMITTED")
        self.assertEqual(result.state, "COMMITTED")

    def test_coordinator_releases_lock_after_success(self):
        contents = self.write_coordinator_before_targets()
        self.commit_coordinator("tx-lock-release", contents)

        with receivable_ledger_lock(
            self.receivables_directory,
            timeout_seconds=0.1,
            poll_interval_seconds=0.01,
        ):
            acquired = True

        self.assertTrue(acquired)

    def test_coordinator_uses_existing_ledger_lock_timeout(self):
        contents = self.write_coordinator_before_targets()
        result = []

        def attempt_commit():
            try:
                service.commit_receivable_ledger_transaction(
                    self.receivables_directory,
                    "tx-lock-timeout",
                    current_before_bytes=contents["current_before"],
                    current_after_bytes=contents["current_after"],
                    history_before_bytes=contents["history_before"],
                    history_after_bytes=contents["history_after"],
                    lock_timeout_seconds=0.1,
                    lock_poll_interval_seconds=0.01,
                )
            except ReceivableLedgerLockTimeout:
                result.append("timeout")

        with receivable_ledger_lock(self.receivables_directory):
            thread = threading.Thread(target=attempt_commit)
            thread.start()
            thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result, ["timeout"])

    def test_coordinator_preserves_bom_newlines_and_extra_bytes(self):
        contents = {
            "current_before": b"\xef\xbb\xbfextra,current\nold,1\r\n",
            "current_after": b"\xef\xbb\xbfextra,current\nnew,2\r\n\n",
            "history_before": b"\xef\xbb\xbfextra,history\r\nold,1\n",
            "history_after": b"\xef\xbb\xbfextra,history\r\nnew,2\n\r\n",
        }
        self.write_coordinator_before_targets(contents)

        self.commit_coordinator("tx-exact-bytes", contents)

        self.assertEqual(
            self.paths.current_path.read_bytes(), contents["current_after"]
        )
        self.assertEqual(
            self.paths.history_path.read_bytes(), contents["history_after"]
        )

    def test_current_before_conflict_stops_before_workspace_creation(self):
        contents = self.write_coordinator_before_targets()
        contents["current_before"] = b"stale-current"

        with self.assertRaises(service.ReceivableLedgerConflictError):
            self.commit_coordinator("tx-current-conflict", contents)

        paths = service.resolve_receivable_transaction_paths(
            self.receivables_directory, "tx-current-conflict"
        )
        self.assertFalse(paths.workspace_directory.exists())

    def test_history_before_conflict_stops_before_workspace_creation(self):
        contents = self.write_coordinator_before_targets()
        contents["history_before"] = b"stale-history"

        with self.assertRaises(service.ReceivableLedgerConflictError):
            self.commit_coordinator("tx-history-conflict", contents)

        paths = service.resolve_receivable_transaction_paths(
            self.receivables_directory, "tx-history-conflict"
        )
        self.assertFalse(paths.workspace_directory.exists())

    def test_missing_target_is_explicit_conflict(self):
        contents = self.write_coordinator_before_targets()
        self.paths.history_path.unlink()

        with self.assertRaises(service.ReceivableLedgerConflictError):
            self.commit_coordinator("tx-missing-target", contents)

    def test_duplicate_transaction_id_is_rejected(self):
        contents = self.write_coordinator_before_targets()
        service.create_transaction_workspace(
            self.receivables_directory, "tx-duplicate"
        )

        with self.assertRaises(
            service.ReceivableLedgerDuplicateTransactionError
        ):
            self.commit_coordinator("tx-duplicate", contents)

    def test_pending_recovery_runs_before_new_transaction(self):
        pending_paths, pending_marker, pending = self.prepare_artifacts(
            "tx-pending-first"
        )
        self.paths.current_path.write_bytes(pending["current_before"])
        self.paths.history_path.write_bytes(pending["history_before"])
        next_contents = {
            "current_before": pending["current_after"],
            "current_after": b"current-next-after",
            "history_before": pending["history_after"],
            "history_after": b"history-next-after",
        }

        result = self.commit_coordinator("tx-after-pending", next_contents)

        self.assertEqual(result.state, "COMMITTED")
        self.assertFalse(pending_paths.workspace_directory.exists())
        self.assertEqual(
            self.paths.current_path.read_bytes(), next_contents["current_after"]
        )
        self.assertEqual(
            pending_marker["decision"], "COMMIT"
        )

    def test_multiple_nonterminal_workspaces_block_recovery(self):
        self.write_coordinator_before_targets()
        service.create_transaction_workspace(
            self.receivables_directory, "tx-multiple-a"
        )
        service.create_transaction_workspace(
            self.receivables_directory, "tx-multiple-b"
        )

        with self.assertRaises(ReceivableLedgerRecoveryRequired):
            service.recover_receivable_ledger_transactions(
                self.receivables_directory,
                lock_timeout_seconds=0.5,
                lock_poll_interval_seconds=0.01,
            )

    def test_preparing_both_before_aborts_and_cleans(self):
        contents = self.write_coordinator_before_targets()
        paths, _ = service.create_transaction_workspace(
            self.receivables_directory, "tx-crash-preparing"
        )
        service.atomic_write_bytes(
            paths.current_before_artifact, contents["current_before"]
        )
        service.atomic_write_bytes(
            paths.history_before_artifact, contents["history_before"]
        )

        results = service.recover_receivable_ledger_transactions(
            self.receivables_directory
        )

        self.assertEqual(results[0].state, "ABORTED")
        self.assertFalse(paths.workspace_directory.exists())
        self.assertEqual(
            self.paths.current_path.read_bytes(), contents["current_before"]
        )

    def recover_prepared_crash(
        self,
        transaction_id,
        current_content,
        history_content,
        marker_state=None,
    ):
        paths, marker, contents = self.prepare_artifacts(transaction_id)
        self.paths.current_path.write_bytes(current_content(contents))
        self.paths.history_path.write_bytes(history_content(contents))
        if marker_state is not None:
            service.transition_transaction_marker(
                paths.marker_path, marker_state
            )
        results = service.recover_receivable_ledger_transactions(
            self.receivables_directory
        )
        return paths, marker, contents, results[0]

    def test_recover_ready_both_before(self):
        paths, _, contents, result = self.recover_prepared_crash(
            "tx-ready-before",
            lambda value: value["current_before"],
            lambda value: value["history_before"],
        )
        self.assertEqual(result.state, "COMMITTED")
        self.assertEqual(
            self.paths.current_path.read_bytes(), contents["current_after"]
        )
        self.assertFalse(paths.workspace_directory.exists())

    def test_recover_ready_current_after_history_before(self):
        _, _, contents, result = self.recover_prepared_crash(
            "tx-ready-current-after",
            lambda value: value["current_after"],
            lambda value: value["history_before"],
        )
        self.assertEqual(result.state, "COMMITTED")
        self.assertEqual(
            self.paths.history_path.read_bytes(), contents["history_after"]
        )

    def test_recover_current_replaced_marker(self):
        _, _, contents, result = self.recover_prepared_crash(
            "tx-current-replaced",
            lambda value: value["current_after"],
            lambda value: value["history_before"],
            "CURRENT_REPLACED",
        )
        self.assertEqual(result.state, "COMMITTED")
        self.assertEqual(
            self.paths.history_path.read_bytes(), contents["history_after"]
        )

    def test_recover_current_before_history_after(self):
        _, _, contents, result = self.recover_prepared_crash(
            "tx-history-first-crash",
            lambda value: value["current_before"],
            lambda value: value["history_after"],
        )
        self.assertEqual(result.state, "COMMITTED")
        self.assertEqual(
            self.paths.current_path.read_bytes(), contents["current_after"]
        )

    def test_recover_history_replaced_both_after(self):
        _, _, _, result = self.recover_prepared_crash(
            "tx-history-replaced",
            lambda value: value["current_after"],
            lambda value: value["history_after"],
            "HISTORY_REPLACED",
        )
        self.assertEqual(result.state, "COMMITTED")

    def test_recover_ready_both_after(self):
        _, _, _, result = self.recover_prepared_crash(
            "tx-ready-both-after",
            lambda value: value["current_after"],
            lambda value: value["history_after"],
        )
        self.assertEqual(result.state, "COMMITTED")

    def test_recovery_unknown_marks_required_and_keeps_targets(self):
        paths, _, contents = self.prepare_artifacts("tx-recovery-unknown")
        self.paths.current_path.write_bytes(b"unknown-current")
        self.paths.history_path.write_bytes(contents["history_before"])

        with self.assertRaisesRegex(
            ReceivableLedgerRecoveryRequired,
            "transaction_id=tx-recovery-unknown",
        ):
            service.recover_receivable_ledger_transactions(
                self.receivables_directory
            )

        marker = service.read_transaction_marker(paths.marker_path)
        self.assertEqual(marker["state"], "RECOVERY_REQUIRED")
        self.assertEqual(self.paths.current_path.read_bytes(), b"unknown-current")

    def test_recovery_required_workspace_blocks_new_transaction(self):
        contents = self.write_coordinator_before_targets()
        paths, _, _ = self.prepare_artifacts("tx-blocking-recovery")
        service.mark_transaction_recovery_required(
            paths.marker_path, "manual recovery"
        )

        with self.assertRaises(ReceivableLedgerRecoveryRequired):
            self.commit_coordinator("tx-must-not-start", contents)

        new_paths = service.resolve_receivable_transaction_paths(
            self.receivables_directory, "tx-must-not-start"
        )
        self.assertFalse(new_paths.workspace_directory.exists())

    def test_recovery_rejects_tampered_marker_target(self):
        paths, marker, contents = self.prepare_artifacts("tx-target-tamper")
        self.paths.current_path.write_bytes(contents["current_before"])
        self.paths.history_path.write_bytes(contents["history_before"])
        marker["current_target"] = str(
            (self.receivables_directory / "outside.csv").resolve()
        )
        service.write_transaction_marker(paths.marker_path, marker)

        with self.assertRaises(ReceivableLedgerRecoveryRequired):
            service.recover_receivable_ledger_transactions(
                self.receivables_directory
            )

        self.assertFalse((self.receivables_directory / "outside.csv").exists())

    def test_recovery_rejects_artifact_path_outside_workspace(self):
        paths, marker, contents = self.prepare_artifacts("tx-artifact-tamper")
        self.paths.current_path.write_bytes(contents["current_before"])
        self.paths.history_path.write_bytes(contents["history_before"])
        marker["current_after_artifact"] = "../outside.csv"
        paths.marker_path.write_bytes(service._marker_json_bytes(marker))

        with self.assertRaises(ReceivableLedgerRecoveryRequired):
            service.recover_receivable_ledger_transactions(
                self.receivables_directory
            )

    def test_prepare_artifact_failure_aborts_without_target_change(self):
        contents = self.write_coordinator_before_targets()
        real_atomic_write = service.atomic_write_bytes

        def fail_first_artifact(target_path, content):
            if Path(target_path).name == "current.before.csv":
                raise ReceivableLedgerWriteError("artifact prepare failed")
            return real_atomic_write(target_path, content)

        with patch.object(
            service,
            "atomic_write_bytes",
            side_effect=fail_first_artifact,
        ):
            with self.assertRaises(ReceivableLedgerWriteError):
                self.commit_coordinator("tx-artifact-failure", contents)

        paths = service.resolve_receivable_transaction_paths(
            self.receivables_directory, "tx-artifact-failure"
        )
        self.assertFalse(paths.workspace_directory.exists())
        self.assertEqual(
            self.paths.current_path.read_bytes(), contents["current_before"]
        )

    def test_ready_marker_failure_aborts_preparing_workspace(self):
        contents = self.write_coordinator_before_targets()
        real_write_marker = service.write_transaction_marker

        def fail_ready(marker_path, marker):
            if marker["state"] == "READY_TO_COMMIT":
                raise ReceivableLedgerWriteError("ready marker failed")
            return real_write_marker(marker_path, marker)

        with patch.object(
            service, "write_transaction_marker", side_effect=fail_ready
        ):
            with self.assertRaises(ReceivableLedgerWriteError):
                self.commit_coordinator("tx-ready-failure", contents)

        paths = service.resolve_receivable_transaction_paths(
            self.receivables_directory, "tx-ready-failure"
        )
        self.assertFalse(paths.workspace_directory.exists())

    def test_current_write_transient_failure_recovers_same_call(self):
        contents = self.write_coordinator_before_targets()
        real_roll_forward = service.roll_forward_from_artifact
        current_attempts = 0

        def fail_current_once(artifact_path, expected_hash, target_path):
            nonlocal current_attempts
            if Path(target_path).name == "current.csv":
                current_attempts += 1
                if current_attempts == 1:
                    raise ReceivableLedgerWriteError("current write failed")
            return real_roll_forward(artifact_path, expected_hash, target_path)

        with patch.object(
            service,
            "roll_forward_from_artifact",
            side_effect=fail_current_once,
        ):
            result = self.commit_coordinator("tx-current-retry", contents)

        self.assertTrue(result.recovered)
        self.assertEqual(current_attempts, 2)

    def test_current_marker_transient_failure_recovers_same_call(self):
        contents = self.write_coordinator_before_targets()
        real_transition = service.transition_transaction_marker
        failed = False

        def fail_current_marker(marker_path, state, **kwargs):
            nonlocal failed
            if state == "CURRENT_REPLACED" and not failed:
                failed = True
                raise ReceivableLedgerWriteError("current marker failed")
            return real_transition(marker_path, state, **kwargs)

        with patch.object(
            service,
            "transition_transaction_marker",
            side_effect=fail_current_marker,
        ):
            result = self.commit_coordinator("tx-current-marker-retry", contents)

        self.assertTrue(result.recovered)
        self.assertEqual(result.state, "COMMITTED")

    def test_history_write_transient_failure_recovers_same_call(self):
        contents = self.write_coordinator_before_targets()
        real_roll_forward = service.roll_forward_from_artifact
        history_attempts = 0

        def fail_history_once(artifact_path, expected_hash, target_path):
            nonlocal history_attempts
            if Path(target_path).name == "receivable_history.csv":
                history_attempts += 1
                if history_attempts == 1:
                    raise ReceivableLedgerWriteError("history write failed")
            return real_roll_forward(artifact_path, expected_hash, target_path)

        with patch.object(
            service,
            "roll_forward_from_artifact",
            side_effect=fail_history_once,
        ):
            result = self.commit_coordinator("tx-history-retry", contents)

        self.assertTrue(result.recovered)
        self.assertEqual(history_attempts, 2)

    def test_history_marker_transient_failure_recovers_same_call(self):
        contents = self.write_coordinator_before_targets()
        real_transition = service.transition_transaction_marker
        failed = False

        def fail_history_marker(marker_path, state, **kwargs):
            nonlocal failed
            if state == "HISTORY_REPLACED" and not failed:
                failed = True
                raise ReceivableLedgerWriteError("history marker failed")
            return real_transition(marker_path, state, **kwargs)

        with patch.object(
            service,
            "transition_transaction_marker",
            side_effect=fail_history_marker,
        ):
            result = self.commit_coordinator("tx-history-marker-retry", contents)

        self.assertTrue(result.recovered)

    def test_final_verification_transient_failure_recovers_same_call(self):
        contents = self.write_coordinator_before_targets()
        real_verify = service._verify_final_after_hashes
        attempts = 0

        def fail_final_once(paths, marker):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ReceivableLedgerVerificationError("final verify failed")
            return real_verify(paths, marker)

        with patch.object(
            service,
            "_verify_final_after_hashes",
            side_effect=fail_final_once,
        ):
            result = self.commit_coordinator("tx-final-retry", contents)

        self.assertTrue(result.recovered)
        self.assertEqual(attempts, 2)

    def test_committed_marker_transient_failure_recovers_same_call(self):
        contents = self.write_coordinator_before_targets()
        real_transition = service.transition_transaction_marker
        failed = False

        def fail_committed_marker(marker_path, state, **kwargs):
            nonlocal failed
            if state == "COMMITTED" and not failed:
                failed = True
                raise ReceivableLedgerWriteError("committed marker failed")
            return real_transition(marker_path, state, **kwargs)

        with patch.object(
            service,
            "transition_transaction_marker",
            side_effect=fail_committed_marker,
        ):
            result = self.commit_coordinator("tx-committed-retry", contents)

        self.assertTrue(result.recovered)
        self.assertEqual(result.state, "COMMITTED")

    def test_cleanup_failure_keeps_committed_workspace_recoverable(self):
        contents = self.write_coordinator_before_targets()

        with patch.object(
            service,
            "cleanup_transaction_workspace",
            side_effect=ReceivableLedgerRecoveryError("cleanup failed"),
        ):
            result = self.commit_coordinator("tx-cleanup-pending", contents)

        paths = service.resolve_receivable_transaction_paths(
            self.receivables_directory, "tx-cleanup-pending"
        )
        self.assertEqual(result.state, "COMMITTED")
        self.assertFalse(result.workspace_cleaned)
        self.assertTrue(paths.workspace_directory.exists())

        recovered = service.recover_receivable_ledger_transactions(
            self.receivables_directory
        )

        self.assertTrue(recovered[0].workspace_cleaned)
        self.assertFalse(paths.workspace_directory.exists())

    def test_idempotency_key_hash_is_deterministic_and_hides_raw_key(self):
        raw_key = "customer/secret operation key"
        first = service.calculate_idempotency_key_hash(raw_key)
        second = service.calculate_idempotency_key_hash(raw_key)
        path = service.resolve_settlement_receipt_path(
            self.receivables_directory, first
        )

        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)
        self.assertEqual(path.name, f"{first}.json")
        self.assertNotIn(raw_key, str(path))
        self.assertFalse(path.parent.exists())

    def test_empty_idempotency_key_is_rejected(self):
        with self.assertRaises(ValueError):
            service.calculate_idempotency_key_hash("")

    def test_request_hash_is_canonical_for_dict_but_preserves_list_order(self):
        first = {"b": 2, "a": 1, "items": [1, 2]}
        reordered = {"items": [1, 2], "a": 1, "b": 2}
        changed_list = {"b": 2, "a": 1, "items": [2, 1]}

        self.assertEqual(
            service.calculate_request_hash(first),
            service.calculate_request_hash(reordered),
        )
        self.assertNotEqual(
            service.calculate_request_hash(first),
            service.calculate_request_hash(changed_list),
        )

    def test_precomputed_request_hash_is_accepted_by_commit_api(self):
        contents = self.write_coordinator_before_targets()
        payload = {"customer": "日本商事", "amount": 900}

        result = service.commit_receivable_ledger_transaction_with_receipt(
            self.receivables_directory,
            "tx-precomputed-request-hash",
            settlement_id="settlement-receipt",
            idempotency_key="precomputed-request-key",
            request_hash=service.calculate_request_hash(payload),
            settlement_response=self.settlement_response(),
            current_before_bytes=contents["current_before"],
            current_after_bytes=contents["current_after"],
            history_before_bytes=contents["history_before"],
            history_after_bytes=contents["history_after"],
            lock_timeout_seconds=0.5,
            lock_poll_interval_seconds=0.01,
        )

        self.assertFalse(result.replayed)
        with self.assertRaises(ValueError):
            service.resolve_request_hash(
                request_payload=payload,
                request_hash=service.calculate_request_hash(payload),
            )

    def test_receipt_commit_persists_exact_reload_and_survives_cleanup(self):
        contents = self.write_coordinator_before_targets()
        result = self.commit_with_receipt(contents=contents)

        loaded = service.read_settlement_receipt(result.receipt_path)

        self.assertFalse(result.replayed)
        self.assertEqual(result.settlement, self.settlement_response())
        self.assertEqual(loaded.receipt["settlement"], result.settlement)
        self.assertEqual(loaded.receipt_hash, result.receipt_hash)
        self.assertEqual(loaded.receipt["schema_version"], 1)
        self.assertTrue(result.receipt_path.exists())
        workspace = service.resolve_receivable_transaction_paths(
            self.receivables_directory, "tx-receipt"
        ).workspace_directory
        self.assertFalse(workspace.exists())

    def test_locked_receipt_commit_matches_public_coordinator(self):
        contents = self.write_coordinator_before_targets()
        request_payload = {"customer": "日本商事", "amount": 900}
        with receivable_ledger_lock(self.receivables_directory):
            result = (
                service._commit_receivable_ledger_transaction_with_receipt_locked(
                    self.receivables_directory,
                    "tx-locked-receipt",
                    settlement_id="settlement-receipt",
                    idempotency_key_hash=service.calculate_idempotency_key_hash(
                        "locked-receipt-key"
                    ),
                    request_hash=service.calculate_request_hash(request_payload),
                    settlement_response=self.settlement_response(),
                    current_before_bytes=contents["current_before"],
                    current_after_bytes=contents["current_after"],
                    history_before_bytes=contents["history_before"],
                    history_after_bytes=contents["history_after"],
                )
            )

        self.assertFalse(result.replayed)
        self.assertEqual(
            self.paths.current_path.read_bytes(), contents["current_after"]
        )
        self.assertTrue(result.receipt_path.exists())

    def test_receipt_preserves_japanese_candidates_rows_and_signed_difference(self):
        for index, difference in enumerate((-100, 100)):
            with self.subTest(difference=difference):
                contents = self.write_coordinator_before_targets()
                settlement = self.settlement_response(difference)
                result = self.commit_with_receipt(
                    f"tx-signed-{index}",
                    f"signed-key-{index}",
                    {"operation": index},
                    contents,
                    settlement,
                )
                self.assertEqual(result.settlement["difference"], difference)
                self.assertEqual(len(result.settlement["source_candidates"]), 2)
                self.assertEqual(len(result.settlement["rows"]), 2)
                self.assertEqual(
                    result.settlement["customer_name"], "日本商事"
                )

    def test_ready_marker_durably_contains_receipt_recovery_snapshot(self):
        paths, marker, _, receipt_path = self.prepare_receipt_artifacts(
            "tx-ready-receipt-snapshot"
        )
        reloaded = service.read_transaction_marker(paths.marker_path)

        self.assertEqual(marker["state"], "READY_TO_COMMIT")
        self.assertTrue(reloaded["receipt_required"])
        self.assertEqual(
            reloaded["settlement_response"], self.settlement_response()
        )
        self.assertEqual(reloaded["receipt_path"], str(receipt_path.resolve()))
        self.assertIsNotNone(reloaded["request_hash"])
        self.assertIsNotNone(reloaded["committed_at"])

    def test_receipt_is_durable_before_committed_marker(self):
        contents = self.write_coordinator_before_targets()
        real_transition = service.transition_transaction_marker

        def assert_receipt_before_committed(marker_path, state, **kwargs):
            if state == "COMMITTED":
                marker = service.read_transaction_marker(marker_path)
                self.assertTrue(Path(marker["receipt_path"]).exists())
                self.assertIsNotNone(marker["receipt_hash"])
            return real_transition(marker_path, state, **kwargs)

        with patch.object(
            service,
            "transition_transaction_marker",
            side_effect=assert_receipt_before_committed,
        ):
            result = self.commit_with_receipt(
                "tx-receipt-order", "receipt-order", contents=contents
            )

        self.assertFalse(result.replayed)

    def test_same_key_same_request_replays_without_ledger_update_or_workspace(self):
        contents = self.write_coordinator_before_targets()
        first = self.commit_with_receipt(contents=contents)
        current_after_first = self.paths.current_path.read_bytes()
        history_after_first = self.paths.history_path.read_bytes()

        replay = self.commit_with_receipt(
            "tx-must-not-exist",
            contents={
                **contents,
                "current_before": b"intentionally stale",
                "history_before": b"intentionally stale",
            },
        )

        self.assertTrue(replay.replayed)
        self.assertEqual(replay.settlement, first.settlement)
        self.assertEqual(self.paths.current_path.read_bytes(), current_after_first)
        self.assertEqual(self.paths.history_path.read_bytes(), history_after_first)
        replay_workspace = service.resolve_receivable_transaction_paths(
            self.receivables_directory, "tx-must-not-exist"
        ).workspace_directory
        self.assertFalse(replay_workspace.exists())

    def test_same_key_different_request_is_conflict_without_update(self):
        contents = self.write_coordinator_before_targets()
        self.commit_with_receipt(contents=contents)
        before = self.paths.current_path.read_bytes()

        with self.assertRaises(service.ReceivableIdempotencyConflictError):
            self.commit_with_receipt(
                "tx-conflicting-request",
                request_payload={"customer": "日本商事", "amount": 901},
                contents=contents,
            )

        self.assertEqual(self.paths.current_path.read_bytes(), before)

    def test_different_key_same_request_allows_new_operation(self):
        first_contents = self.write_coordinator_before_targets()
        self.commit_with_receipt(contents=first_contents)
        second_contents = {
            "current_before": first_contents["current_after"],
            "current_after": b"second-current-after",
            "history_before": first_contents["history_after"],
            "history_after": b"second-history-after",
        }

        second = self.commit_with_receipt(
            "tx-second-key",
            "client-operation-002",
            contents=second_contents,
        )

        self.assertFalse(second.replayed)
        self.assertEqual(
            self.paths.current_path.read_bytes(), b"second-current-after"
        )

    def test_receipt_malformed_and_missing_field_are_explicit_errors(self):
        for index, raw in enumerate(
            (b"{bad-json", json.dumps({"schema_version": 1}).encode())
        ):
            with self.subTest(index=index):
                path = self.receivables_directory / f"bad-{index}.json"
                path.write_bytes(raw)
                with self.assertRaises(ReceivableSettlementReceiptError):
                    service.read_settlement_receipt(path)

    def valid_receipt_mapping(self):
        return {
            "schema_version": 1,
            "idempotency_key_hash": "a" * 64,
            "request_hash": "b" * 64,
            "transaction_id": "tx-valid-receipt",
            "settlement_id": "settlement-valid",
            "settlement": self.settlement_response(),
            "current_after_hash": "c" * 64,
            "history_after_hash": "d" * 64,
            "committed_at": "2026-08-31T00:00:00+00:00",
        }

    def test_receipt_atomic_write_failures_leave_no_receipt(self):
        failure_patches = (
            (service, "_write_all_and_fsync", OSError("temp write")),
            (service.os, "fsync", OSError("fsync")),
        )
        for index, (owner, name, error) in enumerate(failure_patches):
            with self.subTest(index=index):
                path = self.receivables_directory / f"receipt-fail-{index}.json"
                with patch.object(owner, name, side_effect=error):
                    with self.assertRaises(ReceivableLedgerWriteError):
                        service.save_settlement_receipt(
                            path, self.valid_receipt_mapping()
                        )
                self.assertFalse(path.exists())

    def test_receipt_replace_failure_leaves_no_receipt(self):
        path = self.receivables_directory / "receipt-replace-fail.json"
        with patch.object(
            service.os, "replace", side_effect=PermissionError("replace")
        ):
            with self.assertRaises(ReceivableLedgerWriteError):
                service.save_settlement_receipt(
                    path, self.valid_receipt_mapping()
                )
        self.assertFalse(path.exists())

    def test_receipt_readback_failure_is_explicit(self):
        path = self.receivables_directory / "receipt-readback-fail.json"
        with patch.object(
            service,
            "read_settlement_receipt",
            side_effect=ReceivableSettlementReceiptError("readback"),
        ):
            with self.assertRaises(ReceivableSettlementReceiptError):
                service.save_settlement_receipt(
                    path, self.valid_receipt_mapping()
                )
        self.assertTrue(path.exists())

    def test_crash_both_after_receipt_missing_is_recovered(self):
        paths, _, contents, receipt_path = self.prepare_receipt_artifacts(
            "tx-crash-receipt-missing"
        )
        self.paths.current_path.write_bytes(contents["current_after"])
        self.paths.history_path.write_bytes(contents["history_after"])
        service.transition_transaction_marker(
            paths.marker_path, "HISTORY_REPLACED"
        )

        result = service.recover_receivable_ledger_transactions(
            self.receivables_directory
        )[0]

        self.assertEqual(result.state, "COMMITTED")
        self.assertTrue(receipt_path.exists())
        self.assertFalse(paths.workspace_directory.exists())

    def test_crash_valid_receipt_before_committed_is_verified(self):
        paths, marker, contents, receipt_path = self.prepare_receipt_artifacts(
            "tx-crash-receipt-valid"
        )
        self.paths.current_path.write_bytes(contents["current_after"])
        self.paths.history_path.write_bytes(contents["history_after"])
        marker = service.transition_transaction_marker(
            paths.marker_path, "HISTORY_REPLACED"
        )
        service.save_settlement_receipt(
            receipt_path, service._expected_receipt_from_marker(marker)
        )

        result = service.recover_receivable_ledger_transactions(
            self.receivables_directory
        )[0]

        self.assertEqual(result.state, "COMMITTED")
        self.assertTrue(receipt_path.exists())

    def test_crash_partial_targets_without_receipt_roll_forward(self):
        cases = (
            ("current_after", "history_before"),
            ("current_before", "history_after"),
        )
        for index, (current_key, history_key) in enumerate(cases):
            with self.subTest(index=index):
                paths, _, contents, receipt_path = (
                    self.prepare_receipt_artifacts(f"tx-partial-receipt-{index}")
                )
                self.paths.current_path.write_bytes(contents[current_key])
                self.paths.history_path.write_bytes(contents[history_key])

                service.recover_receivable_ledger_transactions(
                    self.receivables_directory
                )

                self.assertEqual(
                    self.paths.current_path.read_bytes(), contents["current_after"]
                )
                self.assertEqual(
                    self.paths.history_path.read_bytes(), contents["history_after"]
                )
                self.assertTrue(receipt_path.exists())
                self.assertFalse(paths.workspace_directory.exists())

    def test_existing_receipt_with_known_before_targets_rolls_forward(self):
        paths, marker, contents, receipt_path = self.prepare_receipt_artifacts(
            "tx-receipt-before-targets"
        )
        marker = service.read_transaction_marker(paths.marker_path)
        service.save_settlement_receipt(
            receipt_path, service._expected_receipt_from_marker(marker)
        )

        service.recover_receivable_ledger_transactions(
            self.receivables_directory
        )

        self.assertEqual(
            self.paths.current_path.read_bytes(), contents["current_after"]
        )

    def test_receipt_marker_request_or_settlement_conflict_requires_recovery(self):
        for index, conflict_field in enumerate(("request_hash", "settlement_id")):
            with self.subTest(field=conflict_field):
                original_directory = self.receivables_directory
                original_paths = self.paths
                try:
                    self.receivables_directory = original_directory / f"case-{index}"
                    self.receivables_directory.mkdir()
                    self.paths = resolve_receivable_ledger_paths(
                        self.receivables_directory
                    )
                    paths, marker, contents, receipt_path = (
                        self.prepare_receipt_artifacts(
                            f"tx-receipt-conflict-{index}"
                        )
                    )
                    self.paths.current_path.write_bytes(contents["current_after"])
                    self.paths.history_path.write_bytes(contents["history_after"])
                    receipt = service._expected_receipt_from_marker(marker)
                    receipt[conflict_field] = (
                        "f" * 64
                        if conflict_field == "request_hash"
                        else "other-settlement"
                    )
                    service.save_settlement_receipt(receipt_path, receipt)

                    with self.assertRaises(ReceivableLedgerRecoveryRequired):
                        service.recover_receivable_ledger_transactions(
                            self.receivables_directory
                        )
                    reloaded = service.read_transaction_marker(paths.marker_path)
                    self.assertEqual(reloaded["state"], "RECOVERY_REQUIRED")
                finally:
                    self.receivables_directory = original_directory
                    self.paths = original_paths

    def test_receipt_hash_conflict_requires_recovery(self):
        paths, marker, contents, receipt_path = self.prepare_receipt_artifacts(
            "tx-receipt-hash-conflict"
        )
        self.paths.current_path.write_bytes(contents["current_after"])
        self.paths.history_path.write_bytes(contents["history_after"])
        loaded = service.save_settlement_receipt(
            receipt_path, service._expected_receipt_from_marker(marker)
        )
        marker["receipt_hash"] = "0" * 64
        service.write_transaction_marker(paths.marker_path, marker)

        with self.assertRaises(ReceivableLedgerRecoveryRequired):
            service.recover_receivable_ledger_transactions(
                self.receivables_directory
            )
        self.assertNotEqual(loaded.receipt_hash, "0" * 64)

    def test_receipt_committed_marker_failure_recovers_same_call(self):
        contents = self.write_coordinator_before_targets()
        real_transition = service.transition_transaction_marker
        failed = False

        def fail_once(marker_path, state, **kwargs):
            nonlocal failed
            if state == "COMMITTED" and not failed:
                failed = True
                raise ReceivableLedgerWriteError("committed marker")
            return real_transition(marker_path, state, **kwargs)

        with patch.object(
            service, "transition_transaction_marker", side_effect=fail_once
        ):
            result = self.commit_with_receipt(
                "tx-receipt-commit-retry", "receipt-commit-retry", contents=contents
            )

        self.assertTrue(result.recovered)
        self.assertTrue(result.receipt_path.exists())

    def test_receipt_cleanup_failure_does_not_remove_receipt(self):
        contents = self.write_coordinator_before_targets()
        with patch.object(
            service,
            "cleanup_transaction_workspace",
            side_effect=ReceivableLedgerRecoveryError("cleanup"),
        ):
            result = self.commit_with_receipt(
                "tx-receipt-cleanup", "receipt-cleanup", contents=contents
            )

        self.assertFalse(result.workspace_cleaned)
        self.assertTrue(result.receipt_path.exists())

    def test_read_only_health_without_transactions_is_ready(self):
        health = service.inspect_receivable_ledger_health_read_only(
            self.receivables_directory
        )
        self.assertEqual(health.status, service.LEDGER_HEALTH_READY)
        self.assertEqual(health.transaction_count, 0)
        self.assertFalse(
            (self.receivables_directory / ".transactions").exists()
        )

    def test_ready_current_snapshot_allows_valid_history(self):
        self.write_current()
        self.write_history()

        snapshot = service.read_receivable_current_snapshot_when_ready(
            self.receivables_directory
        )

        self.assertTrue(snapshot.settlement_available)

    def test_ready_current_snapshot_allows_missing_history_without_creation(self):
        self.write_current()
        self.assertFalse(self.paths.history_path.exists())

        snapshot = service.read_receivable_current_snapshot_when_ready(
            self.receivables_directory
        )

        self.assertTrue(snapshot.settlement_available)
        self.assertFalse(self.paths.history_path.exists())

    def test_ready_current_snapshot_rejects_malformed_history_without_update(self):
        self.write_current()
        self.paths.history_path.write_bytes(b"\xff\xfeinvalid-history")
        history_before = self.paths.history_path.read_bytes()

        snapshot = service.read_receivable_current_snapshot_when_ready(
            self.receivables_directory
        )

        self.assertFalse(snapshot.settlement_available)
        self.assertEqual(self.paths.history_path.read_bytes(), history_before)

    def test_ready_current_snapshot_rejects_history_schema_without_update(self):
        self.write_current()
        pd.DataFrame([{"消込ID": "S001"}]).to_csv(
            self.paths.history_path,
            index=False,
            encoding="utf-8-sig",
        )
        history_before = self.paths.history_path.read_bytes()

        snapshot = service.read_receivable_current_snapshot_when_ready(
            self.receivables_directory
        )

        self.assertFalse(snapshot.settlement_available)
        self.assertEqual(self.paths.history_path.read_bytes(), history_before)

    def test_read_only_health_committed_residue_is_ready_without_cleanup(self):
        paths, _, _ = self.prepare_artifacts("tx-health-committed")
        service.transition_transaction_marker(
            paths.marker_path, "CURRENT_REPLACED"
        )
        service.transition_transaction_marker(
            paths.marker_path, "HISTORY_REPLACED"
        )
        service.transition_transaction_marker(paths.marker_path, "COMMITTED")

        health = service.inspect_receivable_ledger_health_read_only(
            self.receivables_directory
        )

        self.assertEqual(health.status, service.LEDGER_HEALTH_READY)
        self.assertTrue(paths.workspace_directory.exists())
        self.assertEqual(
            service.read_transaction_marker(paths.marker_path)["state"],
            "COMMITTED",
        )

    def test_read_only_health_recoverable_nonterminal_is_pending(self):
        paths, _, contents = self.prepare_artifacts("tx-health-pending")
        self.paths.current_path.write_bytes(contents["current_before"])
        self.paths.history_path.write_bytes(contents["history_before"])

        health = service.inspect_receivable_ledger_health_read_only(
            self.receivables_directory
        )

        self.assertEqual(
            health.status, service.LEDGER_HEALTH_RECOVERY_PENDING
        )
        self.assertTrue(paths.workspace_directory.exists())

    def test_read_only_health_recovery_required_state_is_required(self):
        paths, _, _ = self.prepare_artifacts("tx-health-required")
        service.mark_transaction_recovery_required(
            paths.marker_path, "manual inspection"
        )

        health = service.inspect_receivable_ledger_health_read_only(
            self.receivables_directory
        )

        self.assertEqual(
            health.status, service.LEDGER_HEALTH_RECOVERY_REQUIRED
        )

    def test_read_only_health_unknown_targets_is_required_without_marking(self):
        paths, _, contents = self.prepare_artifacts("tx-health-unknown")
        self.paths.current_path.write_bytes(b"unknown-current")
        self.paths.history_path.write_bytes(contents["history_before"])
        marker_before = paths.marker_path.read_bytes()

        health = service.inspect_receivable_ledger_health_read_only(
            self.receivables_directory
        )

        self.assertEqual(
            health.status, service.LEDGER_HEALTH_RECOVERY_REQUIRED
        )
        self.assertEqual(paths.marker_path.read_bytes(), marker_before)

    def test_read_only_health_multiple_nonterminal_is_required(self):
        service.create_transaction_workspace(
            self.receivables_directory, "tx-health-multiple-a"
        )
        service.create_transaction_workspace(
            self.receivables_directory, "tx-health-multiple-b"
        )

        health = service.inspect_receivable_ledger_health_read_only(
            self.receivables_directory
        )

        self.assertEqual(
            health.status, service.LEDGER_HEALTH_RECOVERY_REQUIRED
        )
        self.assertEqual(health.nonterminal_count, 2)

    def test_read_only_health_does_not_update_marker_artifacts_or_targets(self):
        paths, _, contents = self.prepare_artifacts("tx-health-immutable")
        self.paths.current_path.write_bytes(contents["current_before"])
        self.paths.history_path.write_bytes(contents["history_before"])
        protected_paths = [
            paths.marker_path,
            paths.current_before_artifact,
            paths.current_after_artifact,
            paths.history_before_artifact,
            paths.history_after_artifact,
            self.paths.current_path,
            self.paths.history_path,
        ]
        before = {path: path.read_bytes() for path in protected_paths}

        health = service.inspect_receivable_ledger_health_read_only(
            self.receivables_directory
        )

        self.assertEqual(
            health.status, service.LEDGER_HEALTH_RECOVERY_PENDING
        )
        self.assertEqual(
            {path: path.read_bytes() for path in protected_paths}, before
        )


if __name__ == "__main__":
    unittest.main()

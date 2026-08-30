import hashlib
import io
import json
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import receivable_persistence_service as persistence  # noqa: E402
import receivable_settlement_execute_service as execute_service  # noqa: E402
from receivable_engine import CURRENT_RECEIVABLE_COLUMNS, HISTORY_COLUMNS  # noqa: E402
from receivable_preview_service import (  # noqa: E402
    DIFFERENCE_ACCOUNT_MODE,
    PARTIAL_SETTLEMENT_MODE,
)
from receivable_settlement_service import (  # noqa: E402
    ReceivableSettlementConflictError,
    ReceivableSettlementValidationError,
)


def current_row(
    code="C001",
    receivable_id="R001",
    *,
    customer="A商事",
    invoice_date="2026-08-01",
    balance="1000",
    status="未処理",
):
    return {
        "コード": code,
        "未収ID": receivable_id,
        "得意先名": customer,
        "請求日": invoice_date,
        "入金予定日": "2026-08-31",
        "未収科目": "未収金",
        "未収補助": "",
        "部門": "営業部",
        "摘要": "8月請求",
        "請求金額": balance,
        "入金済額": "0",
        "残高": balance,
        "ステータス": status,
    }


class ReceivableSettlementExecuteServiceTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        self.paths = persistence.resolve_receivable_ledger_paths(self.directory)
        self.write_current([current_row()])
        self.write_history([])
        self.preview_revision = persistence.calculate_current_revision(
            self.paths.current_path.read_bytes()
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def write_current(self, rows):
        pd.DataFrame(rows, columns=CURRENT_RECEIVABLE_COLUMNS).to_csv(
            self.paths.current_path,
            index=False,
            encoding="utf-8-sig",
        )

    def write_history(self, rows):
        pd.DataFrame(rows, columns=HISTORY_COLUMNS).to_csv(
            self.paths.history_path,
            index=False,
            encoding="utf-8-sig",
        )

    def execute(self, **overrides):
        arguments = {
            "idempotency_key": "operation-key-001",
            "preview_revision": self.preview_revision,
            "customer_name": "A商事",
            "settlement_date": date(2026, 8, 30),
            "payment_amount": 1000,
            "receipt_account": "普通預金",
            "mode": None,
            "difference_account": None,
            "difference_summary": None,
            "lock_timeout_seconds": 0.5,
            "lock_poll_interval_seconds": 0.01,
        }
        arguments.update(overrides)
        with patch.object(
            execute_service, "_new_settlement_id", return_value="fixed-settlement"
        ), patch.object(
            execute_service,
            "_new_created_at",
            return_value="2026-08-30T12:00:00+00:00",
        ):
            return execute_service.execute_receivable_settlement(
                self.directory, **arguments
            )

    def loaded_current(self):
        return persistence.load_current_receivables_read_only(
            self.paths.current_path
        ).dataframe

    def loaded_history(self):
        return persistence.load_receivable_history_read_only(
            self.paths.history_path
        ).dataframe

    def test_complete_settlement_persists_two_ledgers_and_receipt(self):
        result = self.execute()

        self.assertFalse(result.replayed)
        self.assertEqual(self.loaded_current().iloc[0]["残高"], "0")
        self.assertEqual(self.loaded_current().iloc[0]["ステータス"], "完了")
        self.assertEqual(self.loaded_history().iloc[0]["消込額"], "1000")
        self.assertTrue(result.receipt_path.exists())
        self.assertEqual(result.settlement_id, "fixed-settlement")

    def test_partial_settlement_persists_partial_balance(self):
        result = self.execute(
            payment_amount=400,
            mode=PARTIAL_SETTLEMENT_MODE,
        )
        self.assertEqual(self.loaded_current().iloc[0]["残高"], "600")
        self.assertEqual(result.settlement["target_total"], 400)
        self.assertEqual(result.settlement["difference"], 0)

    def test_shortage_difference_persists_full_settlement(self):
        result = self.execute(
            payment_amount=900,
            mode=DIFFERENCE_ACCOUNT_MODE,
            difference_account="支払手数料",
        )
        self.assertEqual(self.loaded_current().iloc[0]["残高"], "0")
        self.assertEqual(result.settlement["difference"], -100)
        self.assertEqual(len(result.settlement["rows"]), 2)

    def test_overpayment_persists_positive_difference(self):
        result = self.execute(
            payment_amount=1200,
            mode=DIFFERENCE_ACCOUNT_MODE,
            difference_account="仮受金",
        )
        self.assertEqual(self.loaded_current().iloc[0]["残高"], "0")
        self.assertEqual(result.settlement["difference"], 200)
        self.assertEqual(result.settlement["rows"][-1]["貸方科目"], "仮受金")

    def test_receipt_preserves_domain_settlement_dto_exactly(self):
        result = self.execute()
        loaded = persistence.read_settlement_receipt(result.receipt_path)
        self.assertEqual(loaded.receipt["settlement"], result.settlement)
        self.assertEqual(
            set(result.settlement),
            {
                "settlement_id", "settlement_date", "customer_name",
                "payment_amount", "target_total", "difference",
                "source_candidates", "rows", "created_at",
            },
        )
        json.dumps(result.settlement, allow_nan=False)

    def test_result_hashes_match_exact_target_bytes(self):
        result = self.execute()
        self.assertEqual(
            result.current_after_hash,
            hashlib.sha256(self.paths.current_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            result.history_after_hash,
            hashlib.sha256(self.paths.history_path.read_bytes()).hexdigest(),
        )

    def test_revision_conflict_is_non_mutating_and_skips_domain_plan(self):
        self.paths.history_path.unlink()
        current_before = self.paths.current_path.read_bytes()
        with patch.object(
            execute_service, "build_receivable_settlement_plan"
        ) as domain_plan:
            with self.assertRaises(persistence.ReceivableLedgerConflictError):
                self.execute(preview_revision="0" * 64)

        domain_plan.assert_not_called()
        self.assertEqual(self.paths.current_path.read_bytes(), current_before)
        self.assertFalse(self.paths.history_path.exists())
        self.assertFalse((self.directory / ".transactions").exists())
        self.assertFalse((self.directory / ".settlements").exists())

    def test_same_key_same_request_replays_before_revision_check(self):
        first = self.execute()
        self.paths.current_path.write_bytes(b"revision changed and malformed")

        replay = self.execute()

        self.assertTrue(replay.replayed)
        self.assertEqual(replay.settlement, first.settlement)
        self.assertEqual(
            self.paths.current_path.read_bytes(), b"revision changed and malformed"
        )

    def test_replay_does_not_run_domain_plan_or_generate_ids(self):
        self.execute()
        with patch.object(
            execute_service, "build_receivable_settlement_plan"
        ) as domain_plan, patch.object(
            execute_service, "_new_settlement_id"
        ) as new_id, patch.object(
            execute_service, "_new_created_at"
        ) as new_time:
            replay = execute_service.execute_receivable_settlement(
                self.directory,
                idempotency_key="operation-key-001",
                preview_revision=self.preview_revision,
                customer_name="A商事",
                settlement_date=date(2026, 8, 30),
                payment_amount=1000,
                receipt_account="普通預金",
            )

        self.assertTrue(replay.replayed)
        domain_plan.assert_not_called()
        new_id.assert_not_called()
        new_time.assert_not_called()

    def test_same_key_different_request_is_idempotency_conflict(self):
        self.execute()
        with self.assertRaises(persistence.ReceivableIdempotencyConflictError):
            self.execute(payment_amount=999, mode=PARTIAL_SETTLEMENT_MODE)

    def test_replay_creates_no_new_workspace_and_changes_no_ledger_bytes(self):
        self.execute()
        current_before = self.paths.current_path.read_bytes()
        history_before = self.paths.history_path.read_bytes()
        transaction_directory = self.directory / ".transactions"
        before_entries = list(transaction_directory.iterdir())

        replay = self.execute()

        self.assertTrue(replay.replayed)
        self.assertEqual(self.paths.current_path.read_bytes(), current_before)
        self.assertEqual(self.paths.history_path.read_bytes(), history_before)
        self.assertEqual(list(transaction_directory.iterdir()), before_entries)

    def test_execute_acquires_ledger_lock_exactly_once(self):
        real_lock = execute_service.receivable_ledger_lock
        calls = []

        @contextmanager
        def counting_lock(*args, **kwargs):
            calls.append((args, kwargs))
            with real_lock(*args, **kwargs):
                yield

        with patch.object(
            execute_service, "receivable_ledger_lock", counting_lock
        ):
            result = self.execute()

        self.assertFalse(result.replayed)
        self.assertEqual(len(calls), 1)

    def test_lock_contention_times_out_and_releases_cleanly(self):
        errors = []

        def attempt_execute():
            try:
                self.execute(
                    lock_timeout_seconds=0.1,
                    lock_poll_interval_seconds=0.01,
                )
            except Exception as exc:
                errors.append(exc)

        with persistence.receivable_ledger_lock(self.directory):
            thread = threading.Thread(target=attempt_execute)
            thread.start()
            thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertIsInstance(errors[0], persistence.ReceivableLedgerLockTimeout)
        with persistence.receivable_ledger_lock(
            self.directory, timeout_seconds=0.2
        ):
            pass

    def test_exception_releases_execute_lock(self):
        with self.assertRaises(persistence.ReceivableLedgerConflictError):
            self.execute(preview_revision="0" * 64)
        with persistence.receivable_ledger_lock(
            self.directory, timeout_seconds=0.2
        ):
            pass

    def test_missing_history_is_initialized_only_after_domain_success(self):
        self.paths.history_path.unlink()
        initialized = []
        real_atomic_write = execute_service.atomic_write_bytes

        def recording_atomic_write(path, content):
            if Path(path) == self.paths.history_path:
                initialized.append(content)
            return real_atomic_write(path, content)

        with patch.object(
            execute_service,
            "atomic_write_bytes",
            side_effect=recording_atomic_write,
        ):
            result = self.execute()

        self.assertFalse(result.replayed)
        self.assertEqual(
            initialized, [persistence.build_empty_receivable_history_bytes()]
        )
        self.assertTrue(self.paths.history_path.exists())
        self.assertTrue(self.paths.history_path.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertEqual(list(self.loaded_history().columns), HISTORY_COLUMNS)
        self.assertEqual(len(self.loaded_history()), 1)

    def test_missing_history_domain_validation_failure_creates_nothing(self):
        self.paths.history_path.unlink()
        with self.assertRaises(ReceivableSettlementValidationError):
            self.execute(customer_name="不存在")
        self.assertFalse(self.paths.history_path.exists())
        self.assertFalse((self.directory / ".transactions").exists())
        self.assertFalse((self.directory / ".settlements").exists())

    def test_empty_history_bytes_match_domain_serializer_contract(self):
        expected = persistence.build_empty_receivable_history_bytes()
        self.paths.history_path.unlink()
        self.execute()
        history_text = expected.decode("utf-8-sig")
        self.assertEqual(history_text.splitlines()[0].split(","), HISTORY_COLUMNS)
        self.assertTrue(expected.startswith(b"\xef\xbb\xbf"))

    def test_current_missing_is_explicit(self):
        self.paths.current_path.unlink()
        with self.assertRaises(persistence.ReceivableLedgerMissingError):
            self.execute()

    def test_current_malformed_is_explicit(self):
        self.paths.current_path.write_bytes(b"\xff\xfeinvalid")
        revision = persistence.calculate_current_revision(
            self.paths.current_path.read_bytes()
        )
        with self.assertRaises(persistence.ReceivableLedgerMalformedError):
            self.execute(preview_revision=revision)

    def test_history_malformed_is_explicit_and_not_overwritten(self):
        self.paths.history_path.write_bytes(b"\xff\xfeinvalid")
        before = self.paths.history_path.read_bytes()
        with self.assertRaises(persistence.ReceivableLedgerMalformedError):
            self.execute()
        self.assertEqual(self.paths.history_path.read_bytes(), before)

    def test_domain_conflict_writes_no_transaction_or_receipt(self):
        self.write_current(
            [
                current_row("C1", "DUP", balance="400"),
                current_row("C2", "DUP", balance="600"),
            ]
        )
        self.preview_revision = persistence.calculate_current_revision(
            self.paths.current_path.read_bytes()
        )
        current_before = self.paths.current_path.read_bytes()
        history_before = self.paths.history_path.read_bytes()

        with self.assertRaises(ReceivableSettlementConflictError):
            self.execute()

        self.assertEqual(self.paths.current_path.read_bytes(), current_before)
        self.assertEqual(self.paths.history_path.read_bytes(), history_before)
        self.assertFalse((self.directory / ".transactions").exists())
        self.assertFalse((self.directory / ".settlements").exists())

    def test_missing_history_domain_conflict_does_not_initialize_file(self):
        self.write_current(
            [
                current_row("C1", "DUP", balance="400"),
                current_row("C2", "DUP", balance="600"),
            ]
        )
        self.paths.history_path.unlink()
        self.preview_revision = persistence.calculate_current_revision(
            self.paths.current_path.read_bytes()
        )

        with self.assertRaises(ReceivableSettlementConflictError):
            self.execute()

        self.assertFalse(self.paths.history_path.exists())
        self.assertFalse((self.directory / ".transactions").exists())
        self.assertFalse((self.directory / ".settlements").exists())

    def test_pending_recovery_runs_before_revision_validation(self):
        current_before = self.paths.current_path.read_bytes()
        history_before = self.paths.history_path.read_bytes()
        after_frame = pd.DataFrame(
            [current_row(balance="900")], columns=CURRENT_RECEIVABLE_COLUMNS
        )
        buffer = io.BytesIO()
        after_frame.to_csv(buffer, index=False, encoding="utf-8-sig")
        current_after = buffer.getvalue()
        history_after_frame = pd.DataFrame(
            [
                {
                    "消込ID": "pending",
                    "消込日": "2026/08/29",
                    "得意先名": "A商事",
                    "コード": "C001",
                    "消込額": "100",
                    "消込前残高": "1000",
                    "消込後残高": "900",
                    "仕訳登録済": "0",
                }
            ],
            columns=HISTORY_COLUMNS,
        )
        buffer = io.BytesIO()
        history_after_frame.to_csv(buffer, index=False, encoding="utf-8-sig")
        history_after = buffer.getvalue()
        paths, _ = persistence.prepare_transaction_artifacts(
            self.directory,
            "pending-before-execute",
            current_before_bytes=current_before,
            current_after_bytes=current_after,
            history_before_bytes=history_before,
            history_after_bytes=history_after,
        )

        with self.assertRaises(persistence.ReceivableLedgerConflictError):
            self.execute()

        self.assertEqual(self.paths.current_path.read_bytes(), current_after)
        self.assertFalse(paths.workspace_directory.exists())

    def test_recovery_required_blocks_execute(self):
        paths, _ = persistence.create_transaction_workspace(
            self.directory, "manual-recovery"
        )
        persistence.mark_transaction_recovery_required(
            paths.marker_path, "manual inspection"
        )
        with self.assertRaises(persistence.ReceivableLedgerRecoveryRequired):
            self.execute()

    def test_receipt_write_failure_then_retry_recovers_and_replays(self):
        with patch.object(
            persistence,
            "save_settlement_receipt",
            side_effect=persistence.ReceivableLedgerWriteError("receipt write"),
        ):
            with self.assertRaises(persistence.ReceivableLedgerWriteError):
                self.execute()

        retry = self.execute()
        self.assertTrue(retry.replayed)
        self.assertTrue(retry.receipt_path.exists())
        self.assertEqual(self.loaded_current().iloc[0]["残高"], "0")

    def test_request_payload_contains_only_formal_intent_fields(self):
        payload = execute_service.build_receivable_execute_request_payload(
            preview_revision="a" * 64,
            customer_name="A商事",
            settlement_date="2026/08/30",
            payment_amount="1,000",
            receipt_account="普通預金",
            mode=None,
            difference_account=None,
            difference_summary=None,
        )
        self.assertEqual(
            list(payload),
            [
                "customer_name", "settlement_date", "payment_amount",
                "receipt_account", "mode", "difference_account",
                "difference_summary", "preview_revision",
            ],
        )
        self.assertEqual(payload["settlement_date"], "2026-08-30")
        self.assertEqual(payload["payment_amount"], 1000)


if __name__ == "__main__":
    unittest.main()

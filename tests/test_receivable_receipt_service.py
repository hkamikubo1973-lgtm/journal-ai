import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import receivable_persistence_service as persistence  # noqa: E402
import receivable_receipt_service as service  # noqa: E402


class ReceivableReceiptServiceTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        self.receipt_ref = service.build_receipt_ref("operation-key-001")
        self.receipt_path = persistence.resolve_settlement_receipt_path(
            self.directory, self.receipt_ref
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def settlement(self):
        return {
            "settlement_id": "settlement-001",
            "settlement_date": "2026-08-30",
            "customer_name": "A商事",
            "payment_amount": 1000,
            "target_total": 1000,
            "difference": 0,
            "source_candidates": [
                {
                    "コード": "C001",
                    "未収ID": "R001",
                    "請求日": "2026-08-01",
                    "請求額": 1000,
                    "残高": 1000,
                    "消込予定": 1000,
                    "未収科目": "未収運賃",
                    "未収補助": "補助A",
                    "部門": "営業部",
                    "取引先": "A商事",
                    "摘要": "8月分",
                }
            ],
            "rows": [
                {
                    "借方科目": "普通預金",
                    "貸方科目": "未収運賃",
                    "貸方補助": "補助A",
                    "部門": "営業部",
                    "金額": 1000,
                    "摘要": "A商事入金",
                }
            ],
            "created_at": "2026-08-30T12:00:00+00:00",
        }

    def receipt(self):
        return {
            "schema_version": 1,
            "idempotency_key_hash": self.receipt_ref,
            "request_hash": "a" * 64,
            "transaction_id": "transaction-001",
            "settlement_id": "settlement-001",
            "settlement": self.settlement(),
            "current_after_hash": "b" * 64,
            "history_after_hash": "c" * 64,
            "committed_at": "2026-08-30T12:00:01+00:00",
        }

    def save_receipt(self, receipt=None, path=None):
        return persistence.save_settlement_receipt(
            path or self.receipt_path,
            copy.deepcopy(receipt or self.receipt()),
        )

    def rewrite_receipt(self, mutate):
        receipt = self.receipt()
        mutate(receipt)
        self.receipt_path.parent.mkdir(parents=True, exist_ok=True)
        self.receipt_path.write_text(
            json.dumps(receipt, ensure_ascii=False, allow_nan=True),
            encoding="utf-8",
        )

    def read(self, **kwargs):
        return service.read_receivable_settlement_receipt(
            self.directory, self.receipt_ref, **kwargs
        )

    def prepare_pending_workspace(self):
        ledger = persistence.resolve_receivable_ledger_paths(self.directory)
        current_before = b"current-before\r\n"
        history_before = b"history-before\r\n"
        ledger.current_path.write_bytes(current_before)
        ledger.history_path.write_bytes(history_before)
        return persistence.prepare_transaction_artifacts(
            self.directory,
            "pending-transaction",
            current_before_bytes=current_before,
            current_after_bytes=b"current-after\r\n",
            history_before_bytes=history_before,
            history_after_bytes=b"history-after\r\n",
        )[0]

    def test_receipt_ref_is_deterministic_shared_sha256(self):
        expected = hashlib.sha256(b"operation-key-001").hexdigest()
        self.assertEqual(service.build_receipt_ref("operation-key-001"), expected)
        self.assertEqual(service.build_receipt_ref("operation-key-001"), expected)

    def test_valid_receipt_is_deeply_loaded(self):
        saved = self.save_receipt()

        result = self.read(expected_settlement_id="settlement-001")

        self.assertEqual(result.receipt_ref, self.receipt_ref)
        self.assertEqual(result.settlement_id, "settlement-001")
        self.assertEqual(result.settlement, self.settlement())
        self.assertEqual(result.receipt_hash, saved.receipt_hash)

    def test_returned_settlement_is_a_copy(self):
        self.save_receipt()
        first = self.read()
        first.settlement["rows"][0]["摘要"] = "changed"
        self.assertEqual(self.read().settlement["rows"][0]["摘要"], "A商事入金")

    def test_result_does_not_expose_raw_idempotency_key(self):
        self.save_receipt()
        result = self.read()
        self.assertNotIn("idempotency_key", vars(result))
        self.assertNotIn("idempotency_key", result.settlement)

    def test_uppercase_receipt_ref_is_rejected(self):
        with self.assertRaises(service.ReceivableReceiptReferenceError):
            service.read_receivable_settlement_receipt(
                self.directory, self.receipt_ref.upper()
            )

    def test_short_receipt_ref_is_rejected(self):
        with self.assertRaises(service.ReceivableReceiptReferenceError):
            service.read_receivable_settlement_receipt(self.directory, "a" * 63)

    def test_path_separator_is_rejected(self):
        with self.assertRaises(service.ReceivableReceiptReferenceError):
            service.read_receivable_settlement_receipt(
                self.directory, "a" * 32 + "/" + "b" * 31
            )

    def test_traversal_is_rejected(self):
        with self.assertRaises(service.ReceivableReceiptReferenceError):
            service.read_receivable_settlement_receipt(
                self.directory, "../" + "a" * 61
            )

    def test_missing_receipt_has_dedicated_error(self):
        with self.assertRaises(
            persistence.ReceivableSettlementReceiptNotFoundError
        ):
            self.read()

    def test_malformed_json_is_rejected(self):
        self.receipt_path.parent.mkdir(parents=True)
        self.receipt_path.write_text("{malformed", encoding="utf-8")
        with self.assertRaises(persistence.ReceivableSettlementReceiptError):
            self.read()

    def test_invalid_schema_version_is_rejected(self):
        self.rewrite_receipt(lambda value: value.update(schema_version=2))
        with self.assertRaises(persistence.ReceivableSettlementReceiptError):
            self.read()

    def test_missing_receipt_field_is_rejected(self):
        self.rewrite_receipt(lambda value: value.pop("request_hash"))
        with self.assertRaises(persistence.ReceivableSettlementReceiptError):
            self.read()

    def test_receipt_ref_and_stored_hash_mismatch_is_rejected(self):
        self.rewrite_receipt(
            lambda value: value.update(idempotency_key_hash="d" * 64)
        )
        with self.assertRaises(persistence.ReceivableSettlementReceiptError):
            self.read()

    def test_top_and_nested_settlement_ids_must_match(self):
        self.rewrite_receipt(
            lambda value: value["settlement"].update(
                settlement_id="different-settlement"
            )
        )
        with self.assertRaises(
            service.ReceivableReceiptSettlementConflictError
        ):
            self.read()

    def test_expected_settlement_id_must_match(self):
        self.save_receipt()
        with self.assertRaises(
            service.ReceivableReceiptSettlementConflictError
        ):
            self.read(expected_settlement_id="different-settlement")

    def test_rows_must_be_a_list(self):
        self.rewrite_receipt(
            lambda value: value["settlement"].update(rows={})
        )
        with self.assertRaises(service.ReceivableReceiptValidationError):
            self.read()

    def test_row_missing_field_is_rejected(self):
        self.rewrite_receipt(
            lambda value: value["settlement"]["rows"][0].pop("部門")
        )
        with self.assertRaises(service.ReceivableReceiptValidationError):
            self.read()

    def test_row_amount_must_be_a_positive_integer(self):
        for invalid in (0, -1, "1000", True, float("nan")):
            with self.subTest(invalid=invalid):
                self.rewrite_receipt(
                    lambda value, invalid=invalid: value["settlement"]["rows"][
                        0
                    ].update(金額=invalid)
                )
                with self.assertRaises(service.ReceivableReceiptValidationError):
                    self.read()

    def test_row_order_is_preserved(self):
        receipt = self.receipt()
        second = copy.deepcopy(receipt["settlement"]["rows"][0])
        second["貸方科目"] = "売掛金"
        second["金額"] = 1
        receipt["settlement"]["rows"].append(second)
        self.save_receipt(receipt)

        result = self.read()

        self.assertEqual(
            [row["貸方科目"] for row in result.settlement["rows"]],
            ["未収運賃", "売掛金"],
        )

    def test_invalid_settlement_date_is_rejected(self):
        self.rewrite_receipt(
            lambda value: value["settlement"].update(
                settlement_date="2026-02-30"
            )
        )
        with self.assertRaises(service.ReceivableReceiptValidationError):
            self.read()

    def test_malformed_source_candidate_is_rejected(self):
        self.rewrite_receipt(
            lambda value: value["settlement"]["source_candidates"][0].pop(
                "未収ID"
            )
        )
        with self.assertRaises(service.ReceivableReceiptValidationError):
            self.read()

    def test_settlement_amount_invariants_are_validated(self):
        self.rewrite_receipt(
            lambda value: value["settlement"].update(target_total=999)
        )
        with self.assertRaises(service.ReceivableReceiptValidationError):
            self.read()

    def test_recovery_pending_is_rejected_without_mutation(self):
        self.save_receipt()
        paths = self.prepare_pending_workspace()
        tracked_paths = [
            paths.marker_path,
            paths.current_before_artifact,
            paths.current_after_artifact,
            paths.history_before_artifact,
            paths.history_after_artifact,
            self.receipt_path,
            persistence.resolve_receivable_ledger_paths(
                self.directory
            ).current_path,
            persistence.resolve_receivable_ledger_paths(
                self.directory
            ).history_path,
        ]
        before = {path: path.read_bytes() for path in tracked_paths}

        with self.assertRaises(persistence.ReceivableLedgerRecoveryRequired):
            self.read()

        self.assertEqual(
            {path: path.read_bytes() for path in tracked_paths}, before
        )

    def test_recovery_required_is_rejected(self):
        self.save_receipt()
        paths, _ = persistence.create_transaction_workspace(
            self.directory, "required-transaction"
        )
        persistence.mark_transaction_recovery_required(
            paths.marker_path, "manual inspection"
        )
        marker_before = paths.marker_path.read_bytes()

        with self.assertRaises(persistence.ReceivableLedgerRecoveryRequired):
            self.read()

        self.assertEqual(paths.marker_path.read_bytes(), marker_before)

    def test_successful_read_does_not_change_ledger_or_receipt_bytes(self):
        self.save_receipt()
        ledger = persistence.resolve_receivable_ledger_paths(self.directory)
        ledger.current_path.write_bytes(b"current-bytes")
        ledger.history_path.write_bytes(b"history-bytes")
        before = {
            path: path.read_bytes()
            for path in (
                ledger.current_path,
                ledger.history_path,
                self.receipt_path,
            )
        }

        self.read()

        self.assertEqual(
            {
                path: path.read_bytes()
                for path in (
                    ledger.current_path,
                    ledger.history_path,
                    self.receipt_path,
                )
            },
            before,
        )

    def test_receipt_link_is_rejected_before_read(self):
        def is_link_like(path):
            return path == self.receipt_path

        with patch.object(
            persistence, "_path_is_link_like", side_effect=is_link_like
        ), self.assertRaises(persistence.ReceivableSettlementReceiptError):
            self.read()


if __name__ == "__main__":
    unittest.main()

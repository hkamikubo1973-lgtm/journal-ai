"""Union API integration against real secure receipts and isolated CSV files."""

import copy
import csv
from datetime import date
from functools import partial
import io
import unittest
from unittest.mock import patch

import test_input_excel_receivable_application_service as fixtures
import api.journal as api
import epson_export_application_service as application
import journal_export_service as export_service
import journal_save_service as save_service
import receivable_registration_handoff_application_service as handoff
import receivable_persistence_service as persistence
from columns import EPSON_COLUMNS
from journal_registration_service import build_registration_id
from receivable_epson_materialization_service import ReceivableEpsonMaterializationError
from receivable_receipt_service import (
    ReceivableReceiptReferenceError,
    ReceivableReceiptSettlementConflictError,
    ReceivableReceiptValidationError,
)


class EpsonUnionTest(unittest.TestCase):
    # Reuse only fixture builders, not the other suite's test methods.
    setUp_fixture = fixtures.InputExcelReceivableApplicationServiceTest.setUp
    tearDown = fixtures.InputExcelReceivableApplicationServiceTest.tearDown
    row = staticmethod(fixtures.InputExcelReceivableApplicationServiceTest.row)
    save_receipt = fixtures.InputExcelReceivableApplicationServiceTest.save_receipt
    receivable_item = staticmethod(fixtures.InputExcelReceivableApplicationServiceTest.receivable_item)
    searched_item = staticmethod(fixtures.InputExcelReceivableApplicationServiceTest.searched_item)

    def setUp(self):
        self.setUp_fixture()
        self.template = {column: "" for column in EPSON_COLUMNS}
        self.template.update({
            "借方科目": "100", "貸方科目": "200", "貸方補助": "01",
            "貸方部門": "10", "形式": "4", "伝票日付": "20260901",
            "入力マシン": "TEMPLATE", "摘要": "old", "借方金額": "1", "貸方金額": "1",
        })
        self.write_transactions([self.template])
        self.master_loader = self.start_patch(application, "load_journal_masters", return_value=self.masters)
        self.snapshot_loader = self.start_patch(
            application, "load_epson_transactions_snapshot",
            side_effect=lambda: application.load_transactions_df(self.transactions_path).to_dict(orient="records"),
        )
        self.start_patch(export_service, "load_system_settings", return_value={"company_name": "TEST"})
        self.start_patch(export_service, "load_journal_masters", return_value=self.masters)
        self.start_patch(api, "save_and_register_epson_csv", side_effect=partial(
            save_service.save_and_register_epson_csv,
            export_dir=str(self.directory), transactions_path=self.transactions_path,
            today=date(2026, 9, 9), start_month=4,
        ))

    def start_patch(self, target, name, **kwargs):
        patcher = patch.object(target, name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def write_transactions(self, rows):
        with self.transactions_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=EPSON_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    def items(self, rows=None, settlement_id="settlement-001"):
        settlement, ref, self.receipt_path = self.save_receipt(rows, settlement_id)
        return [self.receivable_item(settlement, ref, index) for index in range(len(settlement["rows"]))]

    def post(self, items, save=False):
        return self.client.post(
            "/api/journal/" + ("save-epson-csv" if save else "export-epson-csv"),
            json={"items": items},
        )

    def resolve(self, items):
        return application.resolve_epson_export_items(items, receivables_directory=self.receivables_directory)

    def csv_rows(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        return list(csv.DictReader(io.StringIO(response.content.decode("cp932"))))

    def assert_blocked(self, items, code=None, status=422):
        before = self.transactions_path.read_bytes()
        for save in (False, True):
            with self.subTest(save=save):
                response = self.post(items, save)
                self.assertEqual(response.status_code, status, response.text)
                if code:
                    self.assertEqual(response.json()["detail"]["code"], code)
                self.assertEqual(self.transactions_path.read_bytes(), before)
                self.assertFalse((self.directory / save_service.EPSON_EXPORT_SUBDIR).exists())

    def test_searched_download_legacy_and_explicit_source(self):
        item = self.searched_item()
        first = self.post([item])
        item["source_type"] = "searched_journal"
        self.assertEqual(self.csv_rows(first), self.csv_rows(self.post([item])))
        self.master_loader.assert_not_called()
        self.snapshot_loader.assert_not_called()

    def test_searched_save_legacy_contract(self):
        item = self.searched_item()
        # Use a valid dated base row, as in existing prepare-registration output.
        item["epson_base_row"].update(self.template)
        item["registration_id"] = build_registration_id(item["prepared_journal"], item["epson_base_row"])
        response = self.post([item], True)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["csv_saved"])

    def test_single_download_reconstructs_real_receipt(self):
        rows = self.csv_rows(self.post(self.items()))
        self.assertEqual(rows[0]["摘要"], "receipt summary")
        self.assertEqual(rows[0]["借方金額"], "1000")
        self.assertEqual(rows[0]["貸方補助"], "01")

    def test_multi_download(self):
        rows = self.csv_rows(self.post(self.items([self.row(摘要="one"), self.row(摘要="two")])) )
        self.assertEqual([row["摘要"] for row in rows], ["one", "two"])

    def test_receivable_save_registers_real_transactions(self):
        response = self.post(self.items(), True)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["db_registered"], response.text)
        self.assertEqual(response.json()["appended_count"], 1)
        rows = application.load_transactions_df(self.transactions_path).to_dict(orient="records")
        self.assertEqual(rows[0]["摘要"], "receipt summary")
        self.assertEqual(rows[0]["入力マシン"], "TEMPLATE")

    def test_mixed_download_preserves_reversed_receipt_request_order(self):
        items = self.items([self.row(摘要="one"), self.row(摘要="two")])
        searched = self.resolve([self.searched_item()])[0]
        searched["epson_base_row"]["摘要"] = "searched"
        searched["registration_id"] = build_registration_id(searched["prepared_journal"], searched["epson_base_row"])
        rows = self.csv_rows(self.post([items[1], searched, items[0]]))
        self.assertEqual([row["摘要"] for row in rows], ["two", "searched", "one"])

    def test_mixed_save_preserves_request_order(self):
        items = self.items([self.row(摘要="one"), self.row(摘要="two")])
        searched = self.searched_item()
        searched["epson_base_row"].update(self.template, 摘要="searched")
        searched["registration_id"] = build_registration_id(searched["prepared_journal"], searched["epson_base_row"])
        response = self.post([items[1], searched, items[0]], True)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["appended_count"], 3, response.text)
        rows = application.load_transactions_df(self.transactions_path)
        self.assertEqual(rows["摘要"].tolist()[:3], ["two", "searched", "one"])

    def test_provenance_tampering_blocks(self):
        original = self.items()
        for key, value in (("row_count", 2), ("row_index", 1), ("settlement_row_id", "0" * 64)):
            with self.subTest(key=key):
                items = copy.deepcopy(original)
                items[0]["provenance"][key] = value
                self.assert_blocked(items, "provenance_mismatch")

    def test_frontend_fields_are_ignored(self):
        items = self.items()
        expected = self.resolve(items)
        items[0].update({
            "prepared_journal": {"amount": -999}, "amount": -999, "account": "evil",
            "sub": "evil", "department": "evil", "summary": "evil",
            "epson_base_row": {}, "epson_preview_row": {},
            "registration_id": "frontend-id", "epson_capability": {"status": "blocked"},
        })
        self.assertEqual(self.resolve(items), expected)
        self.assertEqual(self.csv_rows(self.post(items))[0]["借方金額"], "1000")

    def test_registration_id_is_generated_by_backend(self):
        item = self.resolve(self.items())[0]
        self.assertEqual(item["registration_id"], build_registration_id(item["prepared_journal"], item["epson_base_row"]))

    def test_template_not_found(self):
        self.write_transactions([])
        self.assert_blocked(self.items(), "template_not_found")

    def test_template_ambiguous(self):
        self.write_transactions([self.template, dict(self.template, 形式="9")])
        self.assert_blocked(self.items(), "template_ambiguous")

    def test_template_invalid(self):
        invalid = dict(self.template)
        del invalid["形式"]
        self.snapshot_loader.side_effect = lambda: [invalid]
        self.assert_blocked(self.items(), "template_invalid")

    def test_invalid_receipt(self):
        items = self.items()
        self.receipt_path.write_text("invalid", encoding="utf-8")
        self.assert_blocked(items, status=503)

    def test_master_validation_failure(self):
        self.masters["accounts"] = []
        self.assert_blocked(self.items(), "master_validation_failed")

    def test_multi_row_failure_blocks_everything(self):
        self.assert_blocked(self.items([self.row(), self.row(借方科目="当座預金")]), "template_not_found")

    def test_missing_or_duplicate_settlement_row_blocks(self):
        items = self.items([self.row(), self.row()])
        self.assert_blocked(items[:1], "provenance_mismatch")
        self.assert_blocked([items[0], items[0]], "provenance_mismatch")

    def test_mixed_cart_invalid_searched_blocks_all(self):
        item = self.searched_item()
        item["registration_id"] = "tampered"
        self.assert_blocked(self.items() + [item])

    def test_download_does_not_mutate_files_or_create_materialization_storage(self):
        items = self.items()
        # Existing read lock is allowed, its bytes must remain unchanged.
        lock = self.receivables_directory / ".receivable_ledger.lock"
        lock.write_bytes(b"\x00")
        before = {p: p.read_bytes() for p in self.directory.rglob("*") if p.is_file()}
        self.csv_rows(self.post(items))
        after = {p: p.read_bytes() for p in self.directory.rglob("*") if p.is_file()}
        self.assertEqual(after, before)

    def test_save_failure_does_not_register(self):
        items = self.items()
        before = self.transactions_path.read_bytes()
        with patch.object(save_service, "save_csv_bytes_to_export_dir", side_effect=OSError("PRIVATE_PATH")), patch.object(save_service, "register_epson_rows_to_search_db") as registrar:
            response = self.post(items, True)
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("PRIVATE_PATH", response.text)
        registrar.assert_not_called()
        self.assertEqual(self.transactions_path.read_bytes(), before)

    def test_save_registers_base_rows_only_after_csv_exists(self):
        items = self.items()
        expected = [item["epson_base_row"] for item in self.resolve(items)]
        def register(rows, **kwargs):
            paths = list((self.directory / save_service.EPSON_EXPORT_SUBDIR).glob("*.csv"))
            self.assertEqual(len(paths), 1)
            self.assertEqual(rows, expected)
            exported = list(csv.DictReader(io.StringIO(paths[0].read_bytes().decode("cp932"))))
            self.assertNotEqual(exported[0]["入力マシン"], rows[0]["入力マシン"])
            return True, len(rows)
        before_history = self.history_path.read_bytes()
        before_receipt = self.receipt_path.read_bytes()
        with patch.object(save_service, "register_epson_rows_to_search_db", side_effect=register) as registrar:
            response = self.post(items, True)
        self.assertTrue(response.json()["db_registered"], response.text)
        registrar.assert_called_once()
        self.assertEqual(self.history_path.read_bytes(), before_history)
        self.assertEqual(self.receipt_path.read_bytes(), before_receipt)

    def test_searched_integrity_is_still_required(self):
        for key in ("prepared_journal", "epson_base_row"):
            item = self.searched_item()
            item[key]["summary" if key == "prepared_journal" else "摘要"] = "tampered"
            self.assert_blocked([item])

    def test_master_and_transactions_loaded_once_for_multiple_settlements(self):
        items = self.items([self.row(), self.row()]) + self.items(settlement_id="second")
        with patch.object(handoff, "read_receivable_settlement_receipt", wraps=handoff.read_receivable_settlement_receipt) as reader:
            self.csv_rows(self.post(items))
        self.assertEqual(reader.call_count, 2)
        self.master_loader.assert_called_once()
        self.snapshot_loader.assert_called_once()
        export_service.load_journal_masters.assert_not_called()

    def test_save_also_shares_master_snapshot(self):
        response = self.post(self.items(), True)
        self.assertEqual(response.status_code, 200, response.text)
        self.master_loader.assert_called_once()
        self.snapshot_loader.assert_called_once()
        export_service.load_journal_masters.assert_not_called()

    def test_each_request_rematerializes_current_transactions(self):
        items = self.items()
        self.csv_rows(self.post(items))
        self.write_transactions([])
        self.assertEqual(self.post(items, True).json()["detail"]["code"], "template_not_found")
        self.assertEqual(self.snapshot_loader.call_count, 2)

    def test_error_mapping_hides_internal_paths(self):
        items = self.items()
        cases = [
            (persistence.ReceivableLedgerLockTimeout("PRIVATE_PATH"), 423),
            (persistence.ReceivableLedgerRecoveryRequired("PRIVATE_PATH"), 503),
            (persistence.ReceivableSettlementReceiptNotFoundError("PRIVATE_PATH"), 404),
            (ReceivableReceiptReferenceError("PRIVATE_PATH"), 422),
            (ReceivableReceiptSettlementConflictError("PRIVATE_PATH"), 409),
            (ReceivableReceiptValidationError("PRIVATE_PATH"), 503),
            (RuntimeError("PRIVATE_PATH"), 500),
        ]
        for error, status in cases:
            for save in (False, True):
                with self.subTest(error=type(error).__name__, save=save), patch.object(application, "build_receivable_registration_handoff_application_result", side_effect=error):
                    response = self.post(items, save)
                    self.assertEqual(response.status_code, status, response.text)
                    self.assertNotIn("PRIVATE_PATH", response.text)

    def test_partial_db_failure_message_is_sanitized(self):
        with patch.object(save_service, "register_epson_rows_to_search_db", side_effect=OSError("PRIVATE_PATH")):
            response = self.post(self.items(), True)
        self.assertTrue(response.json()["partial_failure"])
        self.assertNotIn("PRIVATE_PATH", response.text)

    def test_prepared_journal_invalid_mapping(self):
        items = self.items()
        with patch.object(application, "materialize_receivable_epson_items", side_effect=ReceivableEpsonMaterializationError("prepared_journal_invalid", "PRIVATE_PATH")):
            self.assert_blocked(items, "prepared_journal_invalid")

    def test_missing_receipt_blocks(self):
        items = self.items()
        self.receipt_path.unlink()
        self.assert_blocked(items, status=404)

    def test_settlement_id_mismatch_blocks(self):
        items = self.items()
        items[0]["provenance"]["settlement_id"] = "different"
        self.assert_blocked(items, status=409)

    def test_current_master_change_is_used_on_next_request(self):
        items = self.items()
        self.csv_rows(self.post(items))
        self.masters["accounts"] = []
        self.assert_blocked(items, "master_validation_failed")

    def test_union_rejects_invalid_source_and_strict_provenance_types(self):
        self.assert_blocked([dict(self.searched_item(), source_type="unknown")])
        original = self.items()
        for key, value in (("row_index", True), ("row_count", "1"), ("receipt_ref", "../PRIVATE_PATH")):
            items = copy.deepcopy(original)
            items[0]["provenance"][key] = value
            self.assert_blocked(items)


if __name__ == "__main__":
    unittest.main()

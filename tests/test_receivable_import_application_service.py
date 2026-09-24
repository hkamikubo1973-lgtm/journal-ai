import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import receivable_engine as engine
import receivable_import_application_service as service
from api.journal import app
from api.receivable import get_receivables_directory


class ReceivableImportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.current = self.directory / "current.csv"
        self.arguments = dict(invoice_date="2026-01-15", default_account="未収運賃", receivables_directory=self.directory)
        app.dependency_overrides[get_receivables_directory] = lambda: self.directory
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)

    def excel(self, rows=None, sheets=None):
        if sheets is None:
            sheets = {"プリント用": [["title", None, None], ["コード", "得意先名１", "繰越し"]] + (rows if rows is not None else [[1, "A社", 1200]])}
        data = io.BytesIO()
        with pd.ExcelWriter(data, engine="openpyxl") as writer:
            for name, values in sheets.items():
                pd.DataFrame(values).to_excel(writer, sheet_name=name, header=False, index=False)
        return data.getvalue()

    def preview(self, data=None, **kwargs):
        return service.preview_receivable_import(data or self.excel(), **(self.arguments | kwargs))

    def execute(self, data=None, **kwargs):
        return service.execute_receivable_import(data or self.excel(), **(self.arguments | kwargs))

    def existing(self):
        self.execute()
        return pd.read_csv(self.current, dtype=str).fillna("")

    def save_current(self, frame):
        frame.to_csv(self.current, index=False, encoding="utf-8-sig")

    def post(self, action, content=None, **fields):
        return self.client.post(f"/api/receivables/import/{action}", files={"file": ("billing.xlsx", content or self.excel())}, data={"invoice_date": "2026-01-15", "default_account": "未収運賃", **fields})

    def test_print_sheet_priority(self):
        result = self.preview(self.excel(sheets={"first": [["invalid"]], "プリント用": [["コード", "得意先名１", "繰越し"], [1, "A", 20]]}))
        self.assertEqual(result["sheet_name"], "プリント用")
        self.assertEqual(result["importable_count"], 1)

    def test_first_sheet_fallback(self):
        result = self.preview(self.excel(sheets={"first": [["コード", "得意先名１", "繰越し"], [1, "A", 20]], "last": [["invalid"]]}))
        self.assertEqual(result["sheet_name"], "first")

    def test_carry_forward_is_amount_and_balance(self):
        result = self.preview(self.excel(sheets={"x": [["コード", "得意先名１", "繰越し", "請求額"], [1, "A", 1200, 9999]]}))
        row = result["valid_rows"][0]
        self.assertEqual((row["請求金額"], row["残高"], row["入金済額"], row["ステータス"]), ("1200", "1200", "0", "未完了"))

    def test_customer_sub_and_summary(self):
        row = self.preview()["valid_rows"][0]
        self.assertEqual(row["未収補助"], "A社")
        self.assertEqual(row["摘要"], "A社 繰越未収")

    def test_due_date_defaults_to_next_month_end(self):
        self.assertEqual(self.preview()["valid_rows"][0]["入金予定日"], "2026-02-28")

    def test_explicit_due_date(self):
        self.assertEqual(self.preview(payment_due_date="2026-03-10")["valid_rows"][0]["入金予定日"], "2026-03-10")

    def test_empty_rows_are_not_errors(self):
        result = self.preview(self.excel([[1, "A", 20], [None, None, None], [2, "B", 30]]))
        self.assertEqual((result["importable_count"], result["excluded_count"]), (2, 0))

    def test_invalid_and_valid_rows_partial_import(self):
        data = self.excel([[1, "A", 20], [2, "B", 0], [3, "C", -1], [4, "D", 1.5], ["bad", "E", 2]])
        preview = self.preview(data)
        self.assertEqual((preview["importable_count"], preview["excluded_count"]), (1, 4))
        self.assertEqual(self.execute(data)["imported_count"], 1)

    def test_excel_error_row_number_and_reason(self):
        error = self.preview(self.excel([[1, "A", 0]]))["exclusions"][0]
        self.assertEqual((error["source_row_label"], error["source_row"], error["reason"]), ("Excel行", 3, "繰越しが0以下です"))

    def test_same_input_duplicate(self):
        result = self.preview(self.excel([[1, "A", 20], [1, "A", 20]]))
        self.assertEqual((result["importable_count"], result["excluded_count"], result["duplicate_count"]), (1, 1, 1))

    def test_current_duplicate(self):
        self.existing()
        result = self.preview()
        self.assertEqual((result["importable_count"], result["duplicate_count"]), (0, 1))

    def test_history_not_used_for_duplicates(self):
        frame = self.existing()
        self.current.unlink()
        frame.to_csv(self.directory / "receivable_history.csv", index=False)
        self.assertEqual(self.preview()["importable_count"], 1)

    def test_absent_current_preview_does_not_create_any_files(self):
        self.assertEqual(self.preview()["importable_count"], 1)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_latest_current_preview_bytes_unchanged(self):
        self.existing()
        before = self.current.read_bytes()
        self.preview(self.excel([[2, "B", 30]]))
        self.assertEqual(self.current.read_bytes(), before)

    def test_execute_rechecks_current_after_preview(self):
        data = self.excel()
        self.assertEqual(self.preview(data)["importable_count"], 1)
        self.execute(data)
        result = self.execute(data)
        self.assertEqual((result["imported_count"], result["duplicate_count"]), (0, 1))

    def test_absent_current_first_import_schema_and_uuid(self):
        self.assertEqual(self.execute()["imported_count"], 1)
        frame = pd.read_csv(self.current, dtype=str).fillna("")
        self.assertEqual(list(frame.columns), engine.CURRENT_RECEIVABLE_COLUMNS)
        self.assertRegex(frame.iloc[0]["未収ID"], r"^[0-9a-f]{32}$")
        self.assertEqual(frame.iloc[0]["コード"], "1")

    def test_same_file_reimport_keeps_current_bytes(self):
        self.execute()
        before = self.current.read_bytes()
        self.assertEqual(self.execute()["imported_count"], 0)
        self.assertEqual(self.current.read_bytes(), before)

    def test_execute_preserves_history_transactions_receipt(self):
        paths = [self.directory / name for name in ("receivable_history.csv", "transactions.csv", "receipt.json")]
        for path in paths:
            path.write_bytes(b"protected")
        self.execute()
        for path in paths:
            self.assertEqual(path.read_bytes(), b"protected")
        self.assertFalse((self.directory / ".transactions").exists())
        self.assertFalse((self.directory / ".settlements").exists())

    def test_maintenance_missing_id_column(self):
        frame = self.existing().drop(columns=["未収ID"])
        self.save_current(frame)
        self.preview()
        result = pd.read_csv(self.current, dtype=str)
        self.assertEqual(list(result.columns)[1], "未収ID")
        self.assertEqual(result.iloc[0]["未収ID"], "1")

    def test_maintenance_empty_id(self):
        frame = self.existing()
        frame["未収ID"] = "  "
        self.save_current(frame)
        self.preview()
        self.assertEqual(pd.read_csv(self.current, dtype=str).iloc[0]["未収ID"], "1")

    def test_maintenance_legacy_code_after_id_fill(self):
        frame = self.existing().drop(columns=["未収ID"])
        legacy = "1-" + "a" * 32
        frame["コード"] = legacy
        self.save_current(frame)
        self.preview()
        row = pd.read_csv(self.current, dtype=str).iloc[0]
        self.assertEqual((row["コード"], row["未収ID"]), ("1", legacy))

    def test_maintenance_empty_row_removal(self):
        frame = self.existing()
        extra = {column: "" for column in frame.columns}
        extra.update({"コード": "keep?", "摘要": "nonempty", "請求金額": "0", "残高": "0"})
        self.save_current(pd.concat([frame, pd.DataFrame([extra])], ignore_index=True))
        self.preview()
        self.assertEqual(len(pd.read_csv(self.current)), 1)

    def test_missing_defaults_alone_do_not_trigger_save(self):
        frame = self.existing().drop(columns=["未収科目", "未収補助", "部門"])
        self.save_current(frame)
        before = self.current.read_bytes()
        self.preview()
        self.assertEqual(self.current.read_bytes(), before)

    def test_missing_defaults_saved_with_maintenance(self):
        frame = self.existing().drop(columns=["未収科目", "未収補助", "部門", "未収ID"])
        self.save_current(frame)
        self.preview()
        self.assertEqual(pd.read_csv(self.current).iloc[0]["未収科目"], "売掛金")

    def test_no_new_master_or_fractional_code_validation(self):
        result = self.preview(self.excel([[1.5, "Unknown", 20]]), default_account="UNKNOWN", department="UNKNOWN")
        self.assertEqual(result["importable_count"], 1)
        self.assertEqual(result["valid_rows"][0]["コード"], "1.5")

    def test_duplicate_key_excludes_due_department_summary_balance(self):
        frame = self.existing()
        frame["入金予定日"] = "2030-01-01"
        frame["部門"] = "other"
        frame["摘要"] = "other"
        frame["残高"] = "0"
        self.save_current(frame)
        self.assertEqual(self.preview()["duplicate_count"], 1)

    def test_api_multipart_preview(self):
        response = self.post("preview")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["importable_count"], 1)
        self.assertFalse(self.current.exists())

    def test_api_execute_ignores_frontend_rows(self):
        response = self.post("execute", normalized_rows=json.dumps([{"請求金額": 9999}]))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(pd.read_csv(self.current).iloc[0]["請求金額"], 1200)

    def test_api_missing_header_safe_error(self):
        response = self.post("preview", self.excel(sheets={"x": [["bad"]]}))
        self.assertEqual(response.status_code, 422)
        self.assertIn("見出し行", response.json()["detail"])

    def test_api_missing_reader_dependency_safe_error(self):
        with patch.object(service.pd, "ExcelFile", side_effect=ImportError("PRIVATE_PATH xlrd")):
            response = self.post("preview")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("PRIVATE_PATH", response.text)

    def test_api_unexpected_exception_safe_error(self):
        with patch.object(service.pd, "ExcelFile", side_effect=OSError("PRIVATE_PATH")):
            response = self.post("preview")
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("PRIVATE_PATH", response.text)

    def test_no_valid_rows_execute_does_not_create_current(self):
        result = self.execute(self.excel([[1, "A", 0]]))
        self.assertEqual(result["imported_count"], 0)
        self.assertFalse(self.current.exists())


if __name__ == "__main__":
    unittest.main()

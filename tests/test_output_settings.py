"""Output-folder Web setting and existing save-folder behavior, using only tmp paths."""

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from api.journal import app  # noqa: E402
from columns import EPSON_COLUMNS  # noqa: E402
from export_file_service import ExportFileError, resolve_export_target_dir  # noqa: E402
from input_excel_save_service import save_input_excel  # noqa: E402
from input_excel_service import InputExcelExport, export_input_excel  # noqa: E402
from journal_export_service import EpsonCsvExport, export_epson_csv  # noqa: E402
from journal_registration_service import build_registration_id  # noqa: E402
from journal_save_service import save_and_register_epson_csv  # noqa: E402
import system_settings  # noqa: E402


class OutputSettingsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.base = self.root / "output"
        self.base.mkdir()
        self.settings_path = self.root / "config" / "settings.json"
        self.settings_path.parent.mkdir()
        self.original = {
            "company_name": "TEST",
            "csv_export_dir": str(self.base),
            "fiscal_year_start_month": 4,
            "future_setting": {"preserve": True},
        }
        self.settings_path.write_text(json.dumps(self.original, ensure_ascii=False), encoding="utf-8")
        patcher = patch.object(system_settings, "SETTINGS_PATH", self.settings_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(app)

    def saved(self):
        return json.loads(self.settings_path.read_text(encoding="utf-8"))

    def test_get_current_output_folder(self):
        response = self.client.get("/api/settings/output")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"csv_export_dir": str(self.base)})

    def test_save_local_path_trims_and_preserves_other_settings(self):
        another = self.root / "another"
        another.mkdir()
        response = self.client.put("/api/settings/output", json={"csv_export_dir": f"  {another}  "})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["csv_export_dir"], str(another))
        self.assertEqual(self.saved(), {**self.original, "csv_export_dir": str(another)})

    def test_save_unc_path_without_requiring_network_share_at_setting_time(self):
        unc = r"\\192.168.0.210\共有\仕訳システム"
        response = self.client.put("/api/settings/output", json={"csv_export_dir": unc})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.saved()["csv_export_dir"], unc)

    def test_empty_folder_rejected_without_overwrite(self):
        before = self.settings_path.read_bytes()
        response = self.client.put("/api/settings/output", json={"csv_export_dir": "  "})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.settings_path.read_bytes(), before)

    def test_invalid_folder_rejected_without_overwrite(self):
        before = self.settings_path.read_bytes()
        for invalid in ("bad\x00path", "C::\\bad", 12):
            with self.subTest(invalid=invalid):
                response = self.client.put("/api/settings/output", json={"csv_export_dir": invalid})
                self.assertEqual(response.status_code, 422)
                self.assertEqual(self.settings_path.read_bytes(), before)

    def test_corrupt_existing_config_is_not_overwritten(self):
        self.settings_path.write_bytes(b"{invalid")
        response = self.client.put("/api/settings/output", json={"csv_export_dir": str(self.base)})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.settings_path.read_bytes(), b"{invalid")

    def test_missing_base_is_accepted_as_setting_but_existing_save_check_blocks(self):
        missing = self.root / "configured-but-missing"
        response = self.client.put("/api/settings/output", json={"csv_export_dir": str(missing)})
        self.assertEqual(response.status_code, 200)
        with self.assertRaises(ExportFileError):
            resolve_export_target_dir(system_settings.load_system_settings()["csv_export_dir"], "01_エプソン取込CSV")
        self.assertFalse(missing.exists())

    def test_epson_save_uses_configured_base_and_existing_01_subfolder(self):
        generated = EpsonCsvExport(content=b"test", filename="epson.csv", epson_base_rows=({"row": "1"},))
        result = save_and_register_epson_csv(
            [], export_builder=lambda items: generated,
            duplicate_checker=lambda *args, **kwargs: True,
            transactions_path=self.root / "unused.csv",
        )
        saved_path = Path(result.save_path)
        self.assertTrue(saved_path.is_file())
        self.assertEqual(saved_path.parent, self.base / "01_エプソン取込CSV")
        self.assertEqual(saved_path.read_bytes(), b"test")

    def test_input_excel_save_uses_configured_base_and_existing_02_subfolder(self):
        generated = InputExcelExport(content=b"test", filename="input.xlsx")
        result = save_input_excel([], export_builder=lambda items, **kwargs: generated)
        saved_path = Path(result.saved_path)
        self.assertTrue(saved_path.is_file())
        self.assertEqual(saved_path.parent, self.base / "02_入力用Excel")
        self.assertEqual(saved_path.read_bytes(), b"test")

    def test_existing_03_report_subfolder_is_preserved(self):
        target = resolve_export_target_dir(str(self.base), "03_未収消込確認表")
        self.assertEqual(target, self.base / "03_未収消込確認表")
        self.assertTrue(target.is_dir())

    def test_download_builders_do_not_use_export_base(self):
        system_settings.save_output_folder(str(self.root / "missing"))
        base = {column: "" for column in EPSON_COLUMNS}
        prepared = {
            "voucher_date": "20260926", "voucher_no": "test", "voucher_summary": "",
            "debit_account_code": "114", "debit_account_name": "普通預金",
            "debit_sub_code": "", "debit_sub_name": "", "debit_dept_code": "", "debit_dept_name": "",
            "credit_account_code": "604", "credit_account_name": "雑収入",
            "credit_sub_code": "", "credit_sub_name": "", "credit_dept_code": "", "credit_dept_name": "",
            "amount": 200, "summary": "test", "source_debit_amount": "200", "source_credit_amount": "200",
        }
        item = {"prepared_journal": prepared, "epson_base_row": base,
                "print_metadata": {"print_category": ""}, "print_warnings": [],
                "registration_id": build_registration_id(prepared, base)}
        epson = export_epson_csv([item], account_master={}, sub_master={}, company_name="",
                                  export_datetime=datetime(2026, 9, 26))
        excel = export_input_excel([item], export_datetime=datetime(2026, 9, 26))
        self.assertTrue(epson.content)
        self.assertTrue(excel.content)
        self.assertFalse((self.root / "missing").exists())


if __name__ == "__main__":
    unittest.main()

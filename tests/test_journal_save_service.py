import csv
from datetime import date, datetime
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from api.journal import (  # noqa: E402
    EpsonExportCsvRequest,
    post_export_epson_csv,
    post_save_epson_csv,
)
from columns import EPSON_COLUMNS  # noqa: E402
from export_file_service import ExportFileError  # noqa: E402
from journal_export_service import EpsonCsvExport, export_epson_csv  # noqa: E402
from journal_registration_service import EDIT_FORM_FIELDS, build_registration_id  # noqa: E402
from journal_save_service import (  # noqa: E402
    EPSON_EXPORT_SUBDIR,
    EpsonSaveError,
    EpsonSaveResult,
    save_and_register_epson_csv,
)


ACCOUNT_MASTER = {"現金": "100", "売上": "200"}
SUB_MASTER = {"小口": "1"}
TODAY = date(2026, 8, 21)


class JournalSaveServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.output_dir = self.root / "base_output"
        self.output_dir.mkdir()
        self.transactions_path = self.root / "transactions.csv"

    def test_save_creates_subdir_writes_same_bytes_and_registers_base_rows(self):
        existing_rows = [
            self._base_row("OLD-1", "20260201"),
            self._base_row("OLD-2", "20250201"),
        ]
        existing_rows[0]["月種別"] = "  既存セルを維持  "
        self._write_transactions(existing_rows)
        items = [self._item("A"), self._item("B")]
        expected_csv = self._build_export(items).content

        result = self._save(items)

        saved_path = Path(result.save_path)
        self.assertTrue(result.ok)
        self.assertTrue(result.csv_saved)
        self.assertTrue(result.db_registered)
        self.assertEqual(result.appended_count, 2)
        self.assertEqual(saved_path.parent.name, EPSON_EXPORT_SUBDIR)
        self.assertTrue(saved_path.is_file())
        self.assertEqual(saved_path.read_bytes(), expected_csv)

        stored_rows = self._read_transactions()
        self.assertEqual(
            [row["証番号"] for row in stored_rows[:2]],
            ["CERT-A", "CERT-B"],
        )
        self.assertEqual(
            [row["証番号"] for row in stored_rows[2:]],
            ["CERT-OLD-1", "CERT-OLD-2"],
        )
        self.assertEqual(stored_rows[2]["月種別"], "  既存セルを維持  ")

        csv_row = next(
            csv.DictReader(io.StringIO(expected_csv.decode("cp932")))
        )
        self.assertEqual(csv_row["入力マシン"], "FINAL-MACHINE")
        self.assertEqual(stored_rows[0]["入力マシン"], "SOURCE-MACHINE-A")

    def test_missing_subdir_is_created_automatically(self):
        self._write_transactions([self._base_row("OLD", "20260201")])
        target_dir = self.output_dir / EPSON_EXPORT_SUBDIR
        self.assertFalse(target_dir.exists())

        result = self._save([self._item("A")])

        self.assertTrue(result.csv_saved)
        self.assertTrue(target_dir.is_dir())

    def test_save_failure_never_calls_duplicate_or_db(self):
        self._write_transactions([self._base_row("OLD", "20260201")])
        checker = Mock()
        registrar = Mock()
        saver = Mock(side_effect=ExportFileError("書込み不能"))

        with self.assertRaisesRegex(EpsonSaveError, "検索DBは更新していません"):
            self._save(
                [self._item("A")],
                csv_saver=saver,
                duplicate_checker=checker,
                db_registrar=registrar,
            )

        checker.assert_not_called()
        registrar.assert_not_called()

    def test_missing_configured_base_dir_does_not_fallback_or_update_db(self):
        self._write_transactions([self._base_row("OLD", "20260201")])
        missing_output_dir = self.root / "configured-but-missing"
        checker = Mock()
        registrar = Mock()

        with self.assertRaises(EpsonSaveError):
            save_and_register_epson_csv(
                [self._item("A")],
                export_dir=str(missing_output_dir),
                transactions_path=self.transactions_path,
                today=TODAY,
                start_month=2,
                export_builder=self._build_export,
                duplicate_checker=checker,
                db_registrar=registrar,
            )

        self.assertFalse(missing_output_dir.exists())
        checker.assert_not_called()
        registrar.assert_not_called()

    def test_invalid_requests_never_save_or_update(self):
        valid = self._item("A")
        invalid_id = self._item("B")
        invalid_id["registration_id"] = "tampered"
        missing_column = self._item("C")
        del missing_column["epson_base_row"]["借方消費税コード"]

        for items in ([], [valid, invalid_id], [missing_column]):
            with self.subTest(item_count=len(items)):
                saver = Mock()
                registrar = Mock()
                with self.assertRaises(ValueError):
                    self._save(
                        items,
                        csv_saver=saver,
                        db_registrar=registrar,
                    )
                saver.assert_not_called()
                registrar.assert_not_called()

    def test_db_failure_keeps_saved_csv_and_returns_partial_failure(self):
        self._write_transactions([self._base_row("OLD", "20260201")])
        registrar = Mock(return_value=(False, "DB書込み失敗"))

        result = self._save(
            [self._item("A")],
            db_registrar=registrar,
        )

        self.assertFalse(result.ok)
        self.assertTrue(result.csv_saved)
        self.assertFalse(result.db_registered)
        self.assertTrue(result.partial_failure)
        self.assertTrue(Path(result.save_path).is_file())
        self.assertIn("CSVは保存しましたが", result.message)

    def test_duplicate_batch_saves_csv_without_second_db_append(self):
        item = self._item("A")
        self._write_transactions([item["epson_base_row"]])
        before = self.transactions_path.read_bytes()

        result = self._save([item])

        self.assertTrue(result.ok)
        self.assertTrue(result.csv_saved)
        self.assertTrue(result.already_registered)
        self.assertFalse(result.db_registered)
        self.assertEqual(self.transactions_path.read_bytes(), before)
        self.assertTrue(Path(result.save_path).is_file())

    def test_invalid_date_stops_before_temp_db_write_but_keeps_csv(self):
        self._write_transactions([self._base_row("OLD", "20260201")])
        before = self.transactions_path.read_bytes()
        invalid = self._item("BAD")
        invalid["prepared_journal"]["voucher_date"] = "20260230"
        invalid["epson_base_row"]["伝票日付"] = "20260230"
        invalid["registration_id"] = build_registration_id(
            invalid["prepared_journal"],
            invalid["epson_base_row"],
        )

        result = self._save([invalid])

        self.assertTrue(result.csv_saved)
        self.assertTrue(result.partial_failure)
        self.assertFalse(result.db_registered)
        self.assertEqual(self.transactions_path.read_bytes(), before)
        self.assertTrue(Path(result.save_path).is_file())

    def test_retention_boundary_and_existing_order_remain_phase_3_21c(self):
        existing = [
            self._base_row("EXPIRED", "20230131"),
            self._base_row("BOUNDARY", "20230201"),
            self._base_row("RECENT", "20250201"),
        ]
        self._write_transactions(existing)

        result = self._save([self._item("A"), self._item("B")])

        self.assertTrue(result.db_registered)
        stored = self._read_transactions()
        self.assertEqual(
            [row["証番号"] for row in stored],
            ["CERT-A", "CERT-B", "CERT-BOUNDARY", "CERT-RECENT"],
        )

    def test_save_route_returns_structured_success(self):
        request = EpsonExportCsvRequest(items=[self._item("A")])
        expected = EpsonSaveResult(
            ok=True,
            csv_saved=True,
            db_registered=True,
            already_registered=False,
            partial_failure=False,
            filename="epson_output_20260821_1234.csv",
            save_path="X:/01_エプソン取込CSV/epson.csv",
            appended_count=1,
            message="EPSON CSVを保存しました。検索DBへ登録しました。",
        )

        with patch("api.journal.save_and_register_epson_csv", return_value=expected):
            response = post_save_epson_csv(request)

        self.assertEqual(response, expected.to_dict())

    def test_save_route_validation_error_is_422(self):
        item = self._item("A")
        item["registration_id"] = "tampered"

        with self.assertRaises(HTTPException) as raised:
            post_save_epson_csv(EpsonExportCsvRequest(items=[item]))

        self.assertEqual(raised.exception.status_code, 422)

    def test_download_route_still_does_not_call_save_or_db(self):
        request = EpsonExportCsvRequest(items=[self._item("A")])
        payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
        generated = self._build_export(payload["items"])

        with patch("api.journal.export_epson_csv", return_value=generated), patch(
            "api.journal.save_and_register_epson_csv"
        ) as saver:
            response = post_export_epson_csv(request)

        self.assertEqual(response.body, generated.content)
        saver.assert_not_called()

    def _save(self, items, **overrides):
        return save_and_register_epson_csv(
            items,
            export_dir=str(self.output_dir),
            transactions_path=self.transactions_path,
            today=TODAY,
            start_month=2,
            export_builder=self._build_export,
            **overrides,
        )

    @staticmethod
    def _build_export(items):
        return export_epson_csv(
            items,
            account_master=ACCOUNT_MASTER,
            sub_master=SUB_MASTER,
            company_name="テスト会社",
            export_datetime=datetime(2026, 8, 21, 12, 34),
            machine_name="FINAL-MACHINE",
            user_name="FINAL-USER",
            input_date="20260821",
        )

    def _write_transactions(self, rows):
        with self.transactions_path.open(
            "w", encoding="utf-8-sig", newline=""
        ) as file:
            writer = csv.DictWriter(file, fieldnames=EPSON_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    def _read_transactions(self):
        with self.transactions_path.open(
            encoding="utf-8-sig", newline=""
        ) as file:
            return list(csv.DictReader(file))

    @staticmethod
    def _prepared(marker, voucher_date="20260821"):
        values = {
            "voucher_date": voucher_date,
            "voucher_no": f"CERT-{marker}",
            "voucher_summary": f"伝票摘要{marker}",
            "debit_account_code": "100",
            "debit_account_name": "現金",
            "debit_sub_code": "1",
            "debit_sub_name": "小口",
            "debit_dept_code": "10",
            "debit_dept_name": "営業",
            "credit_account_code": "200",
            "credit_account_name": "売上",
            "credit_sub_code": "",
            "credit_sub_name": "",
            "credit_dept_code": "20",
            "credit_dept_name": "管理",
            "amount": 1234,
            "summary": f"摘要{marker}",
            "source_debit_amount": "1234",
            "source_credit_amount": "1234",
        }
        return {field: values[field] for field in EDIT_FORM_FIELDS}

    @classmethod
    def _base_row(cls, marker, voucher_date="20260821"):
        prepared = cls._prepared(marker, voucher_date)
        row = {column: f"元値:{column}" for column in EPSON_COLUMNS}
        row.update(
            {
                "伝票日付": voucher_date,
                "証番号": prepared["voucher_no"],
                "伝票摘要": prepared["voucher_summary"],
                "借方部門": prepared["debit_dept_code"],
                "借方部門名": prepared["debit_dept_name"],
                "借方科目": prepared["debit_account_code"],
                "借方科目名": prepared["debit_account_name"],
                "借方補助": prepared["debit_sub_code"],
                "借方補助科目名": prepared["debit_sub_name"],
                "借方金額": str(prepared["amount"]),
                "貸方部門": prepared["credit_dept_code"],
                "貸方部門名": prepared["credit_dept_name"],
                "貸方科目": prepared["credit_account_code"],
                "貸方科目名": prepared["credit_account_name"],
                "貸方補助": prepared["credit_sub_code"],
                "貸方補助科目名": prepared["credit_sub_name"],
                "貸方金額": str(prepared["amount"]),
                "摘要": prepared["summary"],
                "入力マシン": f"SOURCE-MACHINE-{marker}",
                "入力ユーザ": f"SOURCE-USER-{marker}",
                "入力会社": f"SOURCE-COMPANY-{marker}",
                "入力日付": "20260101",
            }
        )
        return row

    @classmethod
    def _item(cls, marker):
        prepared = cls._prepared(marker)
        base_row = cls._base_row(marker)
        return {
            "registration_id": build_registration_id(prepared, base_row),
            "prepared_journal": prepared,
            "epson_base_row": base_row,
        }


if __name__ == "__main__":
    unittest.main()

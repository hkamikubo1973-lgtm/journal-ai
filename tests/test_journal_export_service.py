import csv
from datetime import datetime
import inspect
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from fastapi import HTTPException


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from api.journal import (  # noqa: E402
    EpsonExportCsvRequest,
    post_export_epson_csv,
)
from columns import EPSON_COLUMNS  # noqa: E402
from epson_export_service import build_epson_rows  # noqa: E402
import journal_export_service  # noqa: E402
from journal_export_service import (  # noqa: E402
    EpsonCsvExport,
    EpsonExportValidationError,
    export_epson_csv,
)
from journal_registration_service import (  # noqa: E402
    EDIT_FORM_FIELDS,
    build_registration_id,
)


ACCOUNT_MASTER = {"現金": "100", "売上": "200"}
SUB_MASTER = {"小口": "1"}
FIXED_EXPORT_ARGUMENTS = {
    "account_master": ACCOUNT_MASTER,
    "sub_master": SUB_MASTER,
    "company_name": "テスト会社",
    "export_datetime": datetime(2026, 8, 21, 12, 34),
    "machine_name": "TEST-MACHINE",
    "user_name": "TEST-USER",
    "input_date": "20260821",
}


class JournalExportServiceTest(unittest.TestCase):
    def test_single_item_has_header_45_columns_cp932_and_no_bom(self):
        result = export_epson_csv(
            [self._item("一")],
            **FIXED_EXPORT_ARGUMENTS,
        )

        self.assertEqual(result.filename, "epson_output_20260821_1234.csv")
        self.assertFalse(result.content.startswith(b"\xef\xbb\xbf"))
        self.assertFalse(result.content.startswith(b"\xff\xfe"))
        decoded = result.content.decode("cp932")
        rows = list(csv.reader(io.StringIO(decoded)))
        self.assertEqual(rows[0], EPSON_COLUMNS)
        self.assertEqual(len(rows[0]), 45)
        self.assertEqual(len(rows), 2)
        self.assertIn("摘要一", rows[1])

    def test_multiple_items_keep_cart_order(self):
        result = export_epson_csv(
            [self._item("A"), self._item("B"), self._item("C")],
            **FIXED_EXPORT_ARGUMENTS,
        )

        rows = list(
            csv.DictReader(io.StringIO(result.content.decode("cp932")))
        )
        self.assertEqual(
            [row["証番号"] for row in rows],
            ["CERT-A", "CERT-B", "CERT-C"],
        )

    def test_uses_shared_build_epson_rows(self):
        with patch(
            "journal_export_service.build_epson_rows",
            wraps=build_epson_rows,
        ) as shared_builder:
            export_epson_csv([self._item("A")], **FIXED_EXPORT_ARGUMENTS)

        shared_builder.assert_called_once()

    def test_streamlit_csv_bytes_are_exactly_identical(self):
        item = self._item("互換")
        expected_rows = build_epson_rows(
            [item["epson_base_row"]],
            "テスト会社",
            ACCOUNT_MASTER,
            SUB_MASTER,
            {},
            machine_name="TEST-MACHINE",
            user_name="TEST-USER",
            input_date="20260821",
        )
        legacy_bytes = (
            pd.DataFrame(expected_rows, columns=EPSON_COLUMNS)
            .fillna("")
            .to_csv(index=False)
            .encode("cp932")
        )

        result = export_epson_csv([item], **FIXED_EXPORT_ARGUMENTS)

        self.assertEqual(result.content, legacy_bytes)

    def test_tampered_registration_id_stops_entire_batch_before_generation(self):
        valid_item = self._item("A")
        tampered_item = self._item("B")
        tampered_item["registration_id"] = "tampered"

        with patch("journal_export_service.build_epson_rows") as builder:
            with self.assertRaises(EpsonExportValidationError):
                export_epson_csv(
                    [valid_item, tampered_item],
                    **FIXED_EXPORT_ARGUMENTS,
                )

        builder.assert_not_called()

    def test_missing_base_column_stops_before_row_generation(self):
        item = self._item("A")
        del item["epson_base_row"]["借方消費税コード"]

        with patch("journal_export_service.build_epson_rows") as builder:
            with self.assertRaises(EpsonExportValidationError):
                export_epson_csv([item], **FIXED_EXPORT_ARGUMENTS)

        builder.assert_not_called()

    def test_missing_prepared_field_stops_before_row_generation(self):
        item = self._item("A")
        del item["prepared_journal"]["summary"]

        with patch("journal_export_service.build_epson_rows") as builder:
            with self.assertRaises(EpsonExportValidationError):
                export_epson_csv([item], **FIXED_EXPORT_ARGUMENTS)

        builder.assert_not_called()

    def test_empty_cart_is_validation_error(self):
        with self.assertRaisesRegex(
            EpsonExportValidationError,
            "出力対象がありません",
        ):
            export_epson_csv([], **FIXED_EXPORT_ARGUMENTS)

    def test_invalid_id_route_returns_422_without_csv(self):
        item = self._item("A")
        item["registration_id"] = "tampered"
        request = EpsonExportCsvRequest(items=[item])

        with self.assertRaises(HTTPException) as raised:
            post_export_epson_csv(request)

        self.assertEqual(raised.exception.status_code, 422)

    def test_missing_base_column_route_returns_422(self):
        item = self._item("A")
        del item["epson_base_row"]["借方消費税コード"]

        with self.assertRaises(HTTPException) as raised:
            post_export_epson_csv(EpsonExportCsvRequest(items=[item]))

        self.assertEqual(raised.exception.status_code, 422)

    def test_empty_cart_route_returns_422(self):
        with self.assertRaises(HTTPException) as raised:
            post_export_epson_csv(EpsonExportCsvRequest(items=[]))

        self.assertEqual(raised.exception.status_code, 422)

    def test_success_route_returns_raw_bytes_and_backend_filename(self):
        request = EpsonExportCsvRequest(items=[self._item("A")])
        expected = EpsonCsvExport(
            content=b"raw-cp932-bytes",
            filename="epson_output_20260821_1234.csv",
        )

        with patch("api.journal.export_epson_csv", return_value=expected):
            response = post_export_epson_csv(request)

        self.assertEqual(response.body, expected.content)
        self.assertEqual(
            response.headers["content-disposition"],
            'attachment; filename="epson_output_20260821_1234.csv"',
        )
        self.assertIn("shift_jis", response.headers["content-type"])

    def test_export_module_has_no_search_db_update_dependency(self):
        source = inspect.getsource(journal_export_service)
        for forbidden_name in (
            "register_epson_rows_to_search_db",
            "update_search_csv",
            "append_to_csv",
            "keep_recent_years",
        ):
            self.assertNotIn(forbidden_name, source)

    @staticmethod
    def _prepared(marker):
        values = {
            "voucher_date": "20260821",
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
    def _item(cls, marker):
        prepared = cls._prepared(marker)
        base_row = {
            column: f"元値:{column}"
            for column in EPSON_COLUMNS
        }
        base_row.update(
            {
                "伝票日付": prepared["voucher_date"],
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
                "期日": None,
                "借方消費税コード": "課対仕入",
                "貸方消費税コード": "課税売上",
            }
        )
        return {
            "registration_id": build_registration_id(prepared, base_row),
            "prepared_journal": prepared,
            "epson_base_row": base_row,
        }


if __name__ == "__main__":
    unittest.main()

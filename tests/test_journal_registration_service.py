import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from columns import EPSON_COLUMNS  # noqa: E402
from epson_export_service import build_epson_rows  # noqa: E402
from journal_registration_service import prepare_registration  # noqa: E402


MASTERS = {
    "accounts": [
        {"code": "100", "name": "現金", "selectable": True},
        {"code": "101", "name": "普通預金", "selectable": True},
        {"code": "200", "name": "売上", "selectable": True},
    ],
    "departments": [
        {"code": "10", "name": "営業"},
        {"code": "20", "name": "管理"},
    ],
    "sub_account_relations": [
        {"account_code": "100", "sub_code": "1", "sub_name": "小口"},
        {"account_code": "101", "sub_code": "2", "sub_name": "本店"},
    ],
    "diagnostics": {
        "duplicate_sub_account_relation_keys": [],
        "invalid_sub_account_relation_rows": 0,
    },
}


class JournalRegistrationServiceTest(unittest.TestCase):
    def setUp(self):
        patcher = patch(
            "journal_registration_service.load_journal_masters",
            return_value=copy.deepcopy(MASTERS),
        )
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_builds_ordered_45_column_base_without_mutating_source(self):
        source_row = self._source_row()
        source_row["追加キー"] = "正式行には含めない"
        before = copy.deepcopy(source_row)

        response = prepare_registration(self._payload(source_row=source_row))

        self.assertTrue(response["ok"])
        self.assertEqual(len(response["epson_preview_row"]), 18)
        base_row = response["epson_base_row"]
        self.assertEqual(list(base_row), EPSON_COLUMNS)
        self.assertEqual(len(base_row), 45)
        self.assertNotIn("追加キー", base_row)
        self.assertEqual(source_row, before)

        expected_edits = {
            "伝票日付": "20260821",
            "証番号": "CERT-EDITED",
            "伝票摘要": "伝票摘要編集後",
            "借方科目": "100",
            "借方科目名": "現金",
            "借方補助": "1",
            "借方補助科目名": "小口",
            "借方部門": "10",
            "借方部門名": "営業",
            "借方金額": "1234",
            "貸方科目": "200",
            "貸方科目名": "売上",
            "貸方補助": "",
            "貸方補助科目名": "",
            "貸方部門": "20",
            "貸方部門名": "管理",
            "貸方金額": "1234",
            "摘要": "摘要編集後",
        }
        for column, expected in expected_edits.items():
            self.assertEqual(base_row[column], expected, column)

        for column in (
            "月種別",
            "借方消費税コード",
            "借方消費税税率",
            "借方資金区分",
            "借方任意項目１",
            "借方インボイス情報",
            "貸方消費税コード",
            "貸方消費税税率",
            "貸方資金区分",
            "貸方任意項目２",
            "貸方インボイス情報",
            "期日",
            "入力マシン",
        ):
            self.assertEqual(base_row[column], source_row[column], column)

    def test_missing_source_column_blocks_all_registration_rows(self):
        source_row = self._source_row()
        del source_row["借方消費税コード"]

        response = prepare_registration(self._payload(source_row=source_row))

        self.assertTrue(response["blocked"])
        self.assertTrue(any("45列が不足" in error for error in response["errors"]))
        for field in (
            "registration_id",
            "prepared_journal",
            "epson_preview_row",
            "epson_base_row",
        ):
            self.assertIsNone(response[field], field)

    def test_non_single_editable_row_blocks_epson_base_row(self):
        payload = self._payload()
        payload["candidate_meta"]["editable_row_count"] = 2

        response = prepare_registration(payload)

        self.assertTrue(response["blocked"])
        self.assertIsNone(response["epson_base_row"])
        self.assertIsNone(response["registration_id"])

    def test_registration_id_changes_for_each_validated_edit(self):
        original = prepare_registration(self._payload())
        cases = (
            {"voucher_no": "CERT-OTHER"},
            {"voucher_summary": "別の伝票摘要"},
            {"amount": "1,235"},
            {
                "debit_account_code": "101",
                "debit_account_name": "普通預金",
                "debit_sub_code": "2",
                "debit_sub_name": "本店",
            },
            {"debit_dept_code": "20", "debit_dept_name": "管理"},
        )

        for edit_overrides in cases:
            with self.subTest(edit_overrides=edit_overrides):
                changed = prepare_registration(
                    self._payload(edit_overrides=edit_overrides)
                )
                self.assertTrue(changed["ok"])
                self.assertNotEqual(
                    original["registration_id"],
                    changed["registration_id"],
                )

    def test_registration_id_changes_for_unedited_tax_column(self):
        original = prepare_registration(self._payload())
        changed = prepare_registration(
            self._payload(source_overrides={"借方消費税コード": "TAX-OTHER"})
        )

        self.assertNotEqual(
            original["registration_id"],
            changed["registration_id"],
        )

    def test_identical_content_has_same_id_regardless_of_source_key_order(self):
        source_row = self._source_row()
        reversed_source_row = dict(reversed(list(source_row.items())))

        first = prepare_registration(self._payload(source_row=source_row))
        second = prepare_registration(
            self._payload(source_row=reversed_source_row)
        )

        self.assertEqual(first["registration_id"], second["registration_id"])

    def test_base_row_can_feed_shared_epson_builder(self):
        response = prepare_registration(self._payload())

        result = build_epson_rows(
            [response["epson_base_row"]],
            "テスト会社",
            {"現金": "100", "売上": "200"},
            {"小口": "1"},
            machine_name="TEST-MACHINE",
            user_name="TEST-USER",
            input_date="20260821",
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(list(result[0]), EPSON_COLUMNS)
        self.assertEqual(len(result[0]), 45)
        self.assertEqual(result[0]["借方消費税コード"], "TAX-D")
        self.assertEqual(result[0]["入力会社"], "テスト会社")

    @staticmethod
    def _source_row(**overrides):
        row = {
            column: f"元値:{column}"
            for column in EPSON_COLUMNS
        }
        row.update(
            {
                "伝票日付": "20260131",
                "証番号": "CERT-SOURCE",
                "伝票摘要": "元伝票摘要",
                "借方科目": "999",
                "借方科目名": "元借方",
                "借方補助": "9",
                "借方補助科目名": "元借方補助",
                "借方部門": "99",
                "借方部門名": "元借方部門",
                "借方金額": "999",
                "借方消費税コード": "TAX-D",
                "借方消費税税率": "10",
                "貸方科目": "998",
                "貸方科目名": "元貸方",
                "貸方補助": "8",
                "貸方補助科目名": "元貸方補助",
                "貸方部門": "98",
                "貸方部門名": "元貸方部門",
                "貸方金額": "999",
                "貸方消費税コード": "TAX-C",
                "貸方消費税税率": "10",
                "摘要": "元摘要",
            }
        )
        row.update(overrides)
        return row

    def _payload(
        self,
        *,
        source_row=None,
        source_overrides=None,
        edit_overrides=None,
    ):
        if source_row is None:
            source_row = self._source_row(**(source_overrides or {}))
        edit_form = {
            "voucher_date": "2026-08-21",
            "voucher_no": "CERT-EDITED",
            "voucher_summary": "伝票摘要編集後",
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
            "amount": "1,234",
            "summary": "摘要編集後",
            "source_debit_amount": "999",
            "source_credit_amount": "999",
        }
        edit_form.update(edit_overrides or {})
        return {
            "edit_form": edit_form,
            "candidate_meta": {
                "editable_row_count": 1,
                "source_row_count": 1,
                "block_row_count": 1,
            },
            "source_row": source_row,
        }


if __name__ == "__main__":
    unittest.main()

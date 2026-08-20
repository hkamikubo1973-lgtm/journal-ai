import copy
import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from columns import EPSON_COLUMNS  # noqa: E402
from epson_export_service import build_epson_rows  # noqa: E402


ACCOUNT_MASTER = {
    "編集後借方": "1111",
    "編集後貸方": "2222",
}
FALLBACK_ACCOUNT_MASTER = {
    "検索DB借方": "3333",
    "検索DB貸方": "4444",
}
SUB_MASTER = {
    "借方補助あり": "D001",
    "貸方補助あり": "C001",
}
METADATA = {
    "machine_name": "TEST-MACHINE",
    "user_name": "TEST-USER",
    "app_name": "仕訳検索システム",
    "input_date": "20260821",
}


class EpsonExportServiceTest(unittest.TestCase):
    def test_fixed_45_columns_and_row_order(self):
        rows = [
            self._source_row("1", debit_name="編集後借方"),
            self._source_row("2", credit_name="編集後貸方"),
        ]

        result = self._build(rows)

        self.assertEqual(len(EPSON_COLUMNS), 45)
        self.assertEqual(len(result), 2)
        self.assertEqual(list(result[0].keys()), EPSON_COLUMNS)
        self.assertEqual(list(result[1].keys()), EPSON_COLUMNS)
        self.assertEqual([row["伝票番号"] for row in result], ["1", "2"])

    def test_unedited_columns_are_preserved(self):
        source = self._source_row(
            "1",
            debit_name="編集後借方",
            credit_name="編集後貸方",
        )

        result = self._build([source])[0]

        for column in (
            "伝票摘要",
            "借方部門",
            "借方部門名",
            "借方消費税コード",
            "借方消費税業種",
            "借方消費税税率",
            "借方資金区分",
            "借方任意項目１",
            "借方任意項目２",
            "借方インボイス情報",
            "貸方部門",
            "貸方部門名",
            "貸方消費税コード",
            "貸方消費税業種",
            "貸方消費税税率",
            "貸方資金区分",
            "貸方任意項目１",
            "貸方任意項目２",
            "貸方インボイス情報",
            "証番号",
        ):
            self.assertEqual(result[column], source[column], column)

    def test_edited_columns_and_output_metadata_are_overwritten(self):
        source = self._source_row(
            "1",
            debit_name="編集後借方",
            credit_name="検索DB貸方",
            debit_sub_name="借方補助あり",
            credit_sub_name="貸方補助あり",
        )

        result = self._build([source], company_name="テスト会社")[0]

        self.assertEqual(result["借方科目"], "1111")
        self.assertEqual(result["貸方科目"], "4444")
        self.assertEqual(result["借方補助"], "D001")
        self.assertEqual(result["貸方補助"], "C001")
        self.assertEqual(result["伝票日付"], "20260820")
        self.assertEqual(result["借方金額"], "1234")
        self.assertEqual(result["貸方金額"], "1234")
        self.assertEqual(result["摘要"], "編集後摘要")
        self.assertEqual(result["伝票摘要"], "元の伝票摘要")
        self.assertEqual(result["入力マシン"], "TEST-MACHINE")
        self.assertEqual(result["入力ユーザ"], "TEST-USER")
        self.assertEqual(result["入力アプリ"], "仕訳検索システム")
        self.assertEqual(result["入力会社"], "テスト会社")
        self.assertEqual(result["入力日付"], "20260821")

    def test_missing_sub_name_clears_sub_code_like_legacy_function(self):
        source = self._source_row("1")
        source["借方補助"] = "OLD-D"
        source["貸方補助"] = "OLD-C"

        result = self._build([source])[0]

        self.assertEqual(result["借方補助"], "")
        self.assertEqual(result["貸方補助"], "")

    def test_unknown_names_fall_back_to_original_codes(self):
        source = self._source_row(
            "1",
            debit_name="未知借方",
            credit_name="未知貸方",
            debit_sub_name="未知借方補助",
            credit_sub_name="未知貸方補助",
        )
        source["借方科目"] = "OLD-D"
        source["貸方科目"] = "OLD-C"
        source["借方補助"] = "OLD-DS"
        source["貸方補助"] = "OLD-CS"

        result = self._build([source])[0]

        self.assertEqual(result["借方科目"], "OLD-D")
        self.assertEqual(result["貸方科目"], "OLD-C")
        self.assertEqual(result["借方補助"], "OLD-DS")
        self.assertEqual(result["貸方補助"], "OLD-CS")

    def test_input_rows_are_not_modified(self):
        rows = [
            self._source_row(
                "1",
                debit_name="編集後借方",
                debit_sub_name="借方補助あり",
            ),
            self._source_row(
                "2",
                credit_name="編集後貸方",
                credit_sub_name="貸方補助あり",
            ),
        ]
        before = copy.deepcopy(rows)

        self._build(rows)

        self.assertEqual(rows, before)

    def _build(self, rows, company_name="会社"):
        return build_epson_rows(
            rows,
            company_name,
            ACCOUNT_MASTER,
            SUB_MASTER,
            FALLBACK_ACCOUNT_MASTER,
            **METADATA,
        )

    @staticmethod
    def _source_row(
        voucher_no,
        *,
        debit_name="",
        credit_name="",
        debit_sub_name="",
        credit_sub_name="",
    ):
        row = {
            column: f"元値:{column}"
            for column in EPSON_COLUMNS
        }
        row.update({
            "伝票日付": "20260820",
            "伝票番号": voucher_no,
            "伝票摘要": "元の伝票摘要",
            "借方科目": "1000",
            "借方科目名": debit_name,
            "借方補助": "",
            "借方補助科目名": debit_sub_name,
            "借方金額": "1234",
            "貸方科目": "2000",
            "貸方科目名": credit_name,
            "貸方補助": "",
            "貸方補助科目名": credit_sub_name,
            "貸方金額": "1234",
            "摘要": "編集後摘要",
            "証番号": "CERT-1",
        })
        return row


if __name__ == "__main__":
    unittest.main()

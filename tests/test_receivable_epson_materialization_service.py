import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from columns import EPSON_COLUMNS  # noqa: E402
from journal_registration_service import build_registration_id  # noqa: E402
from receivable_epson_materialization_service import (  # noqa: E402
    TEMPLATE_AMBIGUOUS,
    TEMPLATE_INVALID,
    TEMPLATE_NOT_FOUND,
    ReceivableEpsonMaterializationError,
    build_template_row_hash,
    materialize_receivable_epson_items,
)


class ReceivableEpsonMaterializationServiceTest(unittest.TestCase):
    def test_single_row_materializes(self):
        result = self.materialize([self.item()], [self.template()])

        self.assertEqual(len(result), 1)
        self.assertEqual(list(result[0]["epson_base_row"]), EPSON_COLUMNS)
        self.assertEqual(result[0]["epson_base_row"]["借方科目"], "101")

    def test_multi_row_materializes_in_input_order(self):
        second = self.item(self.prepared(
            debit_account_code="102",
            debit_account_name="現金",
        ))
        templates = [
            self.template(debit_account_code="102", marker="SECOND"),
            self.template(marker="FIRST"),
        ]

        result = self.materialize([self.item(), second], templates)

        self.assertEqual(
            [row["epson_base_row"]["入力マシン"] for row in result],
            ["FIRST", "SECOND"],
        )

    def test_each_row_can_use_a_different_template(self):
        second_prepared = self.prepared(
            debit_account_code="102",
            debit_account_name="現金",
            credit_account_code="202",
            credit_account_name="未収金",
        )
        result = self.materialize(
            [self.item(), self.item(second_prepared)],
            [
                self.template(marker="ROW-A"),
                self.template(
                    debit_account_code="102",
                    credit_account_code="202",
                    marker="ROW-B",
                    overrides={"形式": "9"},
                ),
            ],
        )

        self.assertEqual(result[0]["epson_base_row"]["形式"], "4")
        self.assertEqual(result[1]["epson_base_row"]["形式"], "9")

    def test_account_codes_must_match_exactly(self):
        candidate = self.template(debit_account_code="999")
        candidate["借方科目名"] = "普通預金"

        error = self.assert_materialization_error([candidate])

        self.assertEqual(error.reason_code, TEMPLATE_NOT_FOUND)

    def test_blank_sub_account_codes_match_blank_only(self):
        prepared = self.prepared(debit_sub_code="", debit_sub_name="")
        blank = self.template(debit_sub_code="", marker="BLANK")
        nonblank = self.template(debit_sub_code="7", marker="NONBLANK")

        result = self.materialize([self.item(prepared)], [nonblank, blank])

        self.assertEqual(result[0]["epson_base_row"]["入力マシン"], "BLANK")

    def test_different_sub_account_code_is_excluded(self):
        error = self.assert_materialization_error([
            self.template(credit_sub_code="999"),
        ])

        self.assertEqual(error.reason_code, TEMPLATE_NOT_FOUND)

    def test_blank_department_codes_match_blank_only(self):
        prepared = self.prepared(credit_dept_code="", credit_dept_name="")
        blank = self.template(credit_dept_code="", marker="BLANK")
        nonblank = self.template(credit_dept_code="99", marker="NONBLANK")

        result = self.materialize([self.item(prepared)], [nonblank, blank])

        self.assertEqual(result[0]["epson_base_row"]["入力マシン"], "BLANK")

    def test_different_department_code_is_excluded(self):
        error = self.assert_materialization_error([
            self.template(credit_dept_code="99"),
        ])

        self.assertEqual(error.reason_code, TEMPLATE_NOT_FOUND)

    def test_missing_template_fails_closed(self):
        error = self.assert_materialization_error([])

        self.assertEqual(error.reason_code, TEMPLATE_NOT_FOUND)

    def test_multiple_critical_signatures_are_ambiguous(self):
        first = self.template(overrides={"形式": "4"})
        second = self.template(overrides={"形式": "5"})

        error = self.assert_materialization_error([first, second])

        self.assertEqual(error.reason_code, TEMPLATE_AMBIGUOUS)

    def test_blank_and_zero_critical_values_are_not_equivalent(self):
        blank = self.template(overrides={"作成方法": ""})
        zero = self.template(overrides={"作成方法": "0"})

        error = self.assert_materialization_error([blank, zero])

        self.assertEqual(error.reason_code, TEMPLATE_AMBIGUOUS)

    def test_exact_normalized_customer_match_has_first_priority(self):
        customer = self.template(
            marker="CUSTOMER",
            overrides={"摘要": "株式会社サンプル運送", "伝票日付": "20250101"},
        )
        newer = self.template(
            marker="NEWER",
            overrides={"摘要": "別会社", "伝票日付": "20261231"},
        )

        result = self.materialize(
            [self.item()], [newer, customer], customer_name="サンプル運送"
        )

        self.assertEqual(result[0]["epson_base_row"]["入力マシン"], "CUSTOMER")
        self.assertTrue(result[0]["template_diagnostics"]["customer_exact_match"])

    def test_exact_normalized_summary_match_has_second_priority(self):
        summary = self.template(
            marker="SUMMARY",
            overrides={"摘要": "サンプル運送入金", "伝票日付": "20250101"},
        )
        newer = self.template(
            marker="NEWER",
            overrides={"摘要": "別摘要", "伝票日付": "20261231"},
        )

        result = self.materialize([self.item()], [newer, summary])

        self.assertEqual(result[0]["epson_base_row"]["入力マシン"], "SUMMARY")
        self.assertTrue(result[0]["template_diagnostics"]["summary_exact_match"])

    def test_newest_valid_template_date_breaks_same_signature_tie(self):
        older = self.template(
            marker="OLDER", overrides={"摘要": "別", "伝票日付": "20250101"}
        )
        newer = self.template(
            marker="NEWER", overrides={"摘要": "別", "伝票日付": "20260101"}
        )

        result = self.materialize([self.item()], [older, newer])

        self.assertEqual(result[0]["epson_base_row"]["入力マシン"], "NEWER")

    def test_canonical_hash_is_final_tie_break(self):
        first = self.template(marker="A", overrides={"摘要": "別"})
        second = self.template(marker="B", overrides={"摘要": "別"})
        expected = min([first, second], key=build_template_row_hash)

        result = self.materialize([self.item()], [first, second])

        self.assertEqual(
            result[0]["template_diagnostics"]["selected_template_hash"],
            build_template_row_hash(expected),
        )

    def test_snapshot_order_does_not_change_selection(self):
        first = self.template(marker="A", overrides={"摘要": "別"})
        second = self.template(marker="B", overrides={"摘要": "別"})

        forward = self.materialize([self.item()], [first, second])
        reverse = self.materialize([self.item()], [second, first])

        self.assertEqual(
            forward[0]["registration_id"], reverse[0]["registration_id"]
        )

    def test_amount_does_not_participate_in_template_selection(self):
        templates = [
            self.template(marker="A", overrides={"摘要": "別"}),
            self.template(marker="B", overrides={"摘要": "別"}),
        ]
        low = self.materialize([self.item(self.prepared(amount=100))], templates)
        high = self.materialize([self.item(self.prepared(amount=999999))], templates)

        self.assertEqual(
            low[0]["template_diagnostics"]["selected_template_hash"],
            high[0]["template_diagnostics"]["selected_template_hash"],
        )

    def test_settlement_date_does_not_participate_in_selection(self):
        template = self.template()

        first = self.materialize(
            [self.item()], [template], settlement_date="2026-01-01"
        )
        second = self.materialize(
            [self.item()], [template], settlement_date="2030-12-31"
        )

        self.assertEqual(first, second)

    def test_blank_voucher_summary_remains_blank(self):
        result = self.materialize(
            [self.item(self.prepared(voucher_summary=""))],
            [self.template(overrides={"伝票摘要": "過去伝票摘要"})],
        )

        self.assertEqual(result[0]["epson_base_row"]["伝票摘要"], "")

    def test_summary_is_not_copied_to_voucher_summary(self):
        prepared = self.prepared(
            summary="得意先入金",
            voucher_summary="",
        )

        result = self.materialize([self.item(prepared)], [self.template()])

        self.assertEqual(result[0]["epson_base_row"]["摘要"], "得意先入金")
        self.assertEqual(result[0]["epson_base_row"]["伝票摘要"], "")

    def test_tax_format_fund_and_invoice_metadata_are_preserved(self):
        critical = {
            "形式": "7",
            "借方消費税コード": "D-TAX",
            "借方資金区分": "D-FUND",
            "借方インボイス情報": "D-INVOICE",
            "貸方消費税コード": "C-TAX",
            "貸方資金区分": "C-FUND",
            "貸方インボイス情報": "C-INVOICE",
        }

        result = self.materialize(
            [self.item()], [self.template(overrides=critical)]
        )

        for column, expected in critical.items():
            self.assertEqual(result[0]["epson_base_row"][column], expected)

    def test_registration_id_uses_existing_algorithm(self):
        result = self.materialize([self.item()], [self.template()])[0]

        self.assertEqual(
            result["registration_id"],
            build_registration_id(
                result["prepared_journal"], result["epson_base_row"]
            ),
        )

    def test_multi_row_failure_returns_no_partial_result(self):
        second = self.item(self.prepared(credit_account_code="999"))

        with self.assertRaises(ReceivableEpsonMaterializationError) as caught:
            self.materialize([self.item(), second], [self.template()])

        self.assertEqual(caught.exception.reason_code, TEMPLATE_NOT_FOUND)
        self.assertEqual(caught.exception.item_number, 2)

    def test_no_empty_45_column_fallback_is_returned(self):
        with self.assertRaises(ReceivableEpsonMaterializationError):
            self.materialize([self.item()], [])

    def test_invalid_45_column_snapshot_has_reason_code(self):
        invalid = self.template()
        del invalid["貸方インボイス情報"]

        error = self.assert_materialization_error([invalid])

        self.assertEqual(error.reason_code, TEMPLATE_INVALID)

    def test_service_performs_no_filesystem_write(self):
        with patch("builtins.open", side_effect=AssertionError("filesystem access")):
            result = self.materialize([self.item()], [self.template()])

        self.assertEqual(len(result), 1)

    def test_source_items_and_snapshot_are_not_mutated(self):
        item = self.item()
        template = self.template()
        item_before = copy.deepcopy(item)
        template_before = copy.deepcopy(template)

        self.materialize([item], [template])

        self.assertEqual(item, item_before)
        self.assertEqual(template, template_before)

    @staticmethod
    def prepared(**overrides):
        prepared = {
            "voucher_date": "20260901",
            "voucher_no": "SETTLEMENT-1",
            "voucher_summary": "",
            "debit_account_code": "101",
            "debit_account_name": "普通預金",
            "debit_sub_code": "1",
            "debit_sub_name": "本店",
            "debit_dept_code": "",
            "debit_dept_name": "",
            "credit_account_code": "201",
            "credit_account_name": "未収運賃",
            "credit_sub_code": "11",
            "credit_sub_name": "サンプル運送",
            "credit_dept_code": "10",
            "credit_dept_name": "営業",
            "amount": 1234,
            "summary": "サンプル運送入金",
            "source_debit_amount": "1234",
            "source_credit_amount": "1234",
        }
        prepared.update(overrides)
        return prepared

    @staticmethod
    def item(prepared=None):
        return {"prepared_journal": prepared or ReceivableEpsonMaterializationServiceTest.prepared()}

    @staticmethod
    def template(
        *,
        debit_account_code="101",
        credit_account_code="201",
        debit_sub_code="1",
        credit_sub_code="11",
        debit_dept_code="",
        credit_dept_code="10",
        marker="TEMPLATE",
        overrides=None,
    ):
        row = {column: "" for column in EPSON_COLUMNS}
        row.update({
            "月種別": "0",
            "種類": "0",
            "形式": "4",
            "作成方法": "0",
            "伝票日付": "20260101",
            "借方科目": debit_account_code,
            "借方科目名": "過去借方",
            "借方補助": debit_sub_code,
            "借方補助科目名": "過去借方補助",
            "借方部門": debit_dept_code,
            "借方金額": "5000",
            "借方消費税コード": "D-TAX",
            "借方資金区分": "D-FUND",
            "貸方科目": credit_account_code,
            "貸方科目名": "過去貸方",
            "貸方補助": credit_sub_code,
            "貸方補助科目名": "過去貸方補助",
            "貸方部門": credit_dept_code,
            "貸方金額": "5000",
            "貸方消費税コード": "C-TAX",
            "貸方資金区分": "C-FUND",
            "摘要": "サンプル運送入金",
            "伝票摘要": "過去伝票摘要",
            "入力マシン": marker,
        })
        row.update(overrides or {})
        return row

    def materialize(
        self,
        items,
        templates,
        *,
        customer_name="",
        settlement_date="2026-09-01",
    ):
        return materialize_receivable_epson_items(
            items,
            templates,
            customer_name=customer_name,
            settlement_date=settlement_date,
        )

    def assert_materialization_error(self, templates):
        with self.assertRaises(ReceivableEpsonMaterializationError) as caught:
            self.materialize([self.item()], templates)
        return caught.exception


if __name__ == "__main__":
    unittest.main()

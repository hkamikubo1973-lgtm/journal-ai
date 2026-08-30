import copy
import sys
import unittest
from pathlib import Path

import pandas as pd


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from receivable_engine import CURRENT_RECEIVABLE_COLUMNS  # noqa: E402
from receivable_query_service import (  # noqa: E402
    ReceivableCustomerNotFoundError,
    build_receivable_customer_detail,
    build_receivable_summary,
    extract_active_receivables,
)


def current_row(
    customer="A商事",
    balance="1,000",
    *,
    code="C001",
    receivable_id="R001",
    status="未処理",
    billed_amount="1,500",
    paid_amount="500",
    billing_date="2026/08/01",
    planned_payment_date="",
):
    return {
        "コード": code,
        "未収ID": receivable_id,
        "得意先名": customer,
        "請求日": billing_date,
        "入金予定日": planned_payment_date,
        "未収科目": "未収運賃",
        "未収補助": "本社",
        "部門": "営業部",
        "摘要": "8月分",
        "請求金額": billed_amount,
        "入金済額": paid_amount,
        "残高": balance,
        "ステータス": status,
    }


def dataframe(rows):
    return pd.DataFrame(rows, columns=CURRENT_RECEIVABLE_COLUMNS)


class ReceivableQueryServiceTest(unittest.TestCase):
    def test_extracts_active_receivables(self):
        result = extract_active_receivables(dataframe([current_row()]))
        self.assertEqual(result["未収ID"].tolist(), ["R001"])

    def test_excludes_zero_balance(self):
        result = extract_active_receivables(
            dataframe([current_row(balance="0")])
        )
        self.assertTrue(result.empty)

    def test_excludes_negative_balance(self):
        result = extract_active_receivables(
            dataframe([current_row(balance="-1")])
        )
        self.assertTrue(result.empty)

    def test_invalid_balance_is_treated_as_zero(self):
        result = extract_active_receivables(
            dataframe([current_row(balance="invalid")])
        )
        self.assertTrue(result.empty)

    def test_excludes_exact_completed_status(self):
        result = extract_active_receivables(
            dataframe([current_row(status="完了")])
        )
        self.assertTrue(result.empty)

    def test_does_not_trim_completed_status(self):
        result = extract_active_receivables(
            dataframe([current_row(status=" 完了 ")])
        )
        self.assertEqual(result["未収ID"].tolist(), ["R001"])

    def test_excludes_blank_customer_after_trim_check(self):
        result = extract_active_receivables(
            dataframe([current_row(customer=" \t ")])
        )
        self.assertTrue(result.empty)

    def test_groups_by_original_customer_name(self):
        summary = build_receivable_summary(
            dataframe(
                [
                    current_row("A商事", "100", receivable_id="R1"),
                    current_row(" A商事", "200", receivable_id="R2"),
                ]
            )
        )
        self.assertEqual(summary["customer_count"], 2)
        self.assertEqual(
            [item["customer_name"] for item in summary["customers"]],
            [" A商事", "A商事"],
        )

    def test_summary_counts_rows_and_customers(self):
        summary = build_receivable_summary(
            dataframe(
                [
                    current_row("A商事", "100", receivable_id="R1"),
                    current_row("A商事", "200", receivable_id="R2"),
                    current_row("B商事", "300", receivable_id="R3"),
                ]
            )
        )
        self.assertEqual(summary["customer_count"], 2)
        self.assertEqual(summary["outstanding_count"], 3)

    def test_summary_sums_balances_as_python_int(self):
        summary = build_receivable_summary(
            dataframe(
                [
                    current_row("A商事", "1,200", receivable_id="R1"),
                    current_row("A商事", "300", receivable_id="R2"),
                ]
            )
        )
        self.assertEqual(summary["outstanding_balance"], 1500)
        self.assertIs(type(summary["outstanding_balance"]), int)

    def test_summary_is_sorted_by_balance_descending(self):
        summary = build_receivable_summary(
            dataframe(
                [
                    current_row("small", "100", receivable_id="R1"),
                    current_row("large", "900", receivable_id="R2"),
                    current_row("middle", "500", receivable_id="R3"),
                ]
            )
        )
        self.assertEqual(
            [item["customer_name"] for item in summary["customers"]],
            ["large", "middle", "small"],
        )

    def test_equal_balance_preserves_first_customer_order(self):
        summary = build_receivable_summary(
            dataframe(
                [
                    current_row("B商事", "500", receivable_id="R1"),
                    current_row("A商事", "500", receivable_id="R2"),
                ]
            )
        )
        self.assertEqual(
            [item["customer_name"] for item in summary["customers"]],
            ["B商事", "A商事"],
        )

    def test_detail_preserves_current_row_order(self):
        detail = build_receivable_customer_detail(
            dataframe(
                [
                    current_row(
                        receivable_id="late", billing_date="2026-08-31"
                    ),
                    current_row(
                        receivable_id="early", billing_date="2026-01-01"
                    ),
                ]
            ),
            "A商事",
        )
        self.assertEqual(
            [item["receivable_id"] for item in detail["receivables"]],
            ["late", "early"],
        )

    def test_detail_maps_all_http_fields_and_preserves_date_text(self):
        detail = build_receivable_customer_detail(
            dataframe(
                [
                    current_row(
                        billing_date="not-a-date",
                        planned_payment_date="2026/09/01",
                    )
                ]
            ),
            "A商事",
        )
        item = detail["receivables"][0]
        self.assertEqual(
            set(item),
            {
                "code",
                "receivable_id",
                "customer_name",
                "billing_date",
                "planned_payment_date",
                "receivable_account",
                "receivable_sub_account",
                "department",
                "summary",
                "billed_amount",
                "paid_amount",
                "balance",
                "status",
            },
        )
        self.assertEqual(item["billing_date"], "not-a-date")
        self.assertEqual(item["planned_payment_date"], "2026/09/01")

    def test_detail_amounts_are_python_ints(self):
        item = build_receivable_customer_detail(
            dataframe([current_row()]), "A商事"
        )["receivables"][0]
        for field in ("billed_amount", "paid_amount", "balance"):
            self.assertIs(type(item[field]), int)

    def test_input_dataframe_is_not_mutated(self):
        snapshot = dataframe(
            [
                current_row("B商事", "1,000", receivable_id="R1"),
                current_row("A商事", "500", receivable_id="R2"),
            ]
        )
        before = snapshot.copy(deep=True)
        build_receivable_summary(snapshot)
        build_receivable_customer_detail(snapshot, "A商事")
        pd.testing.assert_frame_equal(snapshot, before)

    def test_missing_active_customer_is_explicit(self):
        with self.assertRaises(ReceivableCustomerNotFoundError):
            build_receivable_customer_detail(
                dataframe([current_row(status="完了")]), "A商事"
            )

    def test_sequence_input_is_deep_copied(self):
        rows = [current_row()]
        before = copy.deepcopy(rows)
        build_receivable_summary(rows)
        self.assertEqual(rows, before)


if __name__ == "__main__":
    unittest.main()

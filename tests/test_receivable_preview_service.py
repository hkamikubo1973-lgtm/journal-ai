import copy
import inspect
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from pandas.testing import assert_frame_equal


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import receivable_preview_service  # noqa: E402
from receivable_engine import build_receivable_journal_rows  # noqa: E402
from receivable_preview_service import (  # noqa: E402
    DIFFERENCE_ACCOUNT_MODE,
    EXACT_MATCH_PATTERN,
    OVERPAYMENT_PATTERN,
    PARTIAL_SETTLEMENT_MODE,
    PARTIAL_SETTLEMENT_PATTERN,
    SHORTAGE_DIFFERENCE_PATTERN,
    build_receivable_fifo_candidates,
    build_receivable_preview,
    parse_receivable_payment_amount,
)


CANDIDATE_FIELDS = [
    "コード",
    "未収ID",
    "請求日",
    "請求額",
    "残高",
    "消込予定",
    "未収科目",
    "未収補助",
    "部門",
    "取引先",
    "摘要",
]


def receivable_row(
    code,
    invoice_date,
    balance,
    *,
    customer="A商事",
    status="未処理",
    account="未収運賃",
    sub_account="",
    department="",
    invoice_amount=None,
    summary=None,
):
    return {
        "コード": str(code),
        "未収ID": f"rid-{code}",
        "得意先名": customer,
        "請求日": invoice_date,
        "請求金額": str(
            balance if invoice_amount is None else invoice_amount
        ),
        "残高": str(balance),
        "ステータス": status,
        "未収科目": account,
        "未収補助": sub_account,
        "部門": department,
        "摘要": summary if summary is not None else f"摘要{code}",
    }


def legacy_app_fifo(detail_df, customer_name, payment_amount):
    """Phase 3-22C時点のapp.py FIFOをそのまま固定したcharacterization。"""

    fifo_df = detail_df.copy()
    fifo_df["_請求日"] = pd.to_datetime(
        fifo_df["請求日"],
        errors="coerce",
    )
    fifo_df["_表示順"] = range(len(fifo_df))
    fifo_df = fifo_df.sort_values(
        ["_請求日", "_表示順"],
        na_position="last",
        kind="stable",
    )

    target_candidates = []
    partial_candidates = []
    remaining = int(payment_amount)

    for _, detail in fifo_df.iterrows():
        target_amount = int(detail["残高"])
        if target_amount <= 0:
            continue

        target_candidates.append({
            "コード": detail["コード"],
            "未収ID": detail["未収ID"],
            "請求日": detail["請求日"],
            "請求額": detail["請求金額"],
            "残高": detail["残高"],
            "消込予定": target_amount,
            "未収科目": detail["未収科目"],
            "未収補助": detail["未収補助"],
            "部門": detail["部門"],
            "取引先": customer_name,
            "摘要": detail.get("摘要", ""),
        })

        if remaining <= 0:
            continue

        scheduled_amount = min(target_amount, remaining)
        if scheduled_amount <= 0:
            continue

        partial_candidates.append({
            **target_candidates[-1],
            "消込予定": scheduled_amount,
        })
        remaining -= scheduled_amount

    return target_candidates, partial_candidates, remaining


class ReceivablePreviewServiceTest(unittest.TestCase):
    def _snapshot(self, *rows):
        return pd.DataFrame(rows)

    def _prepared_customer_df(self, *rows):
        frame = self._snapshot(*rows)
        frame["残高"] = pd.to_numeric(frame["残高"]).astype(int)
        frame["請求金額"] = pd.to_numeric(frame["請求金額"]).astype(int)
        return frame

    def _fifo(self, rows, payment_amount):
        return build_receivable_fifo_candidates(
            self._snapshot(*rows),
            "A商事",
            payment_amount,
        )

    def _preview(
        self,
        rows,
        payment_amount,
        *,
        mode=None,
        difference_account=None,
        difference_summary=None,
        receipt_account="普通預金",
    ):
        return build_receivable_preview(
            self._snapshot(*rows),
            "A商事",
            payment_amount,
            date(2026, 8, 30),
            receipt_account,
            mode,
            difference_account,
            difference_summary,
        )

    def assert_fifo_matches_legacy(self, rows, payment_amount):
        prepared = self._prepared_customer_df(*rows)
        expected_target, expected_partial, expected_remaining = legacy_app_fifo(
            prepared,
            "A商事",
            payment_amount,
        )
        actual = self._fifo(rows, payment_amount)
        self.assertEqual(actual["target_candidates"], expected_target)
        self.assertEqual(actual["partial_candidates"], expected_partial)
        self.assertEqual(actual["remaining_payment"], expected_remaining)

    def test_payment_amount_validation_matches_streamlit_normalization(self):
        accepted = {
            "1,234円": 1234,
            "１": 1,
            " ￥ 5，000 ": 5000,
            99: 99,
        }
        for value, expected in accepted.items():
            with self.subTest(value=value):
                self.assertEqual(
                    parse_receivable_payment_amount(value),
                    expected,
                )

    def test_empty_non_numeric_zero_and_negative_payments_are_rejected(self):
        for value in (None, "", "abc", "0", 0, "-1", -1):
            with self.subTest(value=value):
                self.assertIsNone(parse_receivable_payment_amount(value))

        with self.assertRaisesRegex(ValueError, "入金額を入力してください"):
            self._fifo([receivable_row(1, "2026-01-01", 100)], 0)

    def test_different_invoice_dates_are_oldest_first(self):
        rows = [
            receivable_row(3, "2026-03-01", 30),
            receivable_row(1, "2026-01-01", 10),
            receivable_row(2, "2026-02-01", 20),
        ]
        self.assert_fifo_matches_legacy(rows, 60)
        result = self._fifo(rows, 60)
        self.assertEqual(
            [item["コード"] for item in result["target_candidates"]],
            ["1", "2", "3"],
        )

    def test_same_invoice_date_keeps_original_display_order(self):
        rows = [
            receivable_row("C", "2026-01-01", 30),
            receivable_row("A", "2026-01-01", 10),
            receivable_row("B", "2026-01-01", 20),
        ]
        self.assert_fifo_matches_legacy(rows, 60)
        result = self._fifo(rows, 60)
        self.assertEqual(
            [item["コード"] for item in result["target_candidates"]],
            ["C", "A", "B"],
        )

    def test_invalid_and_empty_dates_follow_valid_dates_in_original_order(self):
        rows = [
            receivable_row("invalid", "日付不正", 20),
            receivable_row("valid", "2026-01-01", 10),
            receivable_row("empty", "", 30),
        ]
        self.assert_fifo_matches_legacy(rows, 60)
        result = self._fifo(rows, 60)
        self.assertEqual(
            [item["コード"] for item in result["target_candidates"]],
            ["valid", "invalid", "empty"],
        )

    def test_payment_consumed_by_first_receivable_only(self):
        rows = [
            receivable_row(1, "2026-01-01", 100),
            receivable_row(2, "2026-02-01", 200),
        ]
        self.assert_fifo_matches_legacy(rows, 50)
        result = self._fifo(rows, 50)
        self.assertEqual(
            [(item["コード"], item["消込予定"])
             for item in result["partial_candidates"]],
            [("1", 50)],
        )

    def test_second_receivable_is_partially_settled(self):
        rows = [
            receivable_row(1, "2026-01-01", 100),
            receivable_row(2, "2026-02-01", 200),
            receivable_row(3, "2026-03-01", 300),
        ]
        self.assert_fifo_matches_legacy(rows, 150)
        result = self._fifo(rows, 150)
        self.assertEqual(
            [(item["コード"], item["消込予定"])
             for item in result["partial_candidates"]],
            [("1", 100), ("2", 50)],
        )

    def test_zero_balance_and_completed_rows_are_excluded(self):
        rows = [
            receivable_row(0, "2025-12-01", 0),
            receivable_row(1, "2026-01-01", 100, status="完了"),
            receivable_row(2, "2026-02-01", 200),
        ]
        result = self._fifo(rows, 200)
        self.assertEqual(
            [item["コード"] for item in result["target_candidates"]],
            ["2"],
        )

    def test_other_customer_is_excluded(self):
        rows = [
            receivable_row(1, "2026-01-01", 100, customer="B商事"),
            receivable_row(2, "2026-02-01", 200),
        ]
        result = self._fifo(rows, 200)
        self.assertEqual(
            [item["コード"] for item in result["target_candidates"]],
            ["2"],
        )

    def test_app_shaped_customer_rows_without_customer_column_are_supported(self):
        frame = self._snapshot(receivable_row(1, "2026-01-01", 100))
        frame = frame.drop(columns=["得意先名"])
        result = build_receivable_fifo_candidates(frame, "A商事", 100)
        self.assertEqual(
            [item["取引先"] for item in result["target_candidates"]],
            ["A商事"],
        )

    def test_candidate_fields_values_and_types_match_current_app(self):
        row = receivable_row(
            1,
            "2026-01-02",
            "1,200",
            invoice_amount="2,500",
            sub_account="A商事",
            department="営業",
            summary="1月運賃",
        )
        candidate = self._fifo([row], 1200)["target_candidates"][0]
        self.assertEqual(list(candidate), CANDIDATE_FIELDS)
        self.assertEqual(candidate, {
            "コード": "1",
            "未収ID": "rid-1",
            "請求日": "2026-01-02",
            "請求額": 2500,
            "残高": 1200,
            "消込予定": 1200,
            "未収科目": "未収運賃",
            "未収補助": "A商事",
            "部門": "営業",
            "取引先": "A商事",
            "摘要": "1月運賃",
        })

    def test_fifo_totals_and_remaining_payment_are_reported(self):
        rows = [
            receivable_row(1, "2026-01-01", 100),
            receivable_row(2, "2026-02-01", 200),
        ]
        result = self._fifo(rows, 350)
        self.assertEqual(result["total_receivable_balance"], 300)
        self.assertEqual(result["partial_total"], 300)
        self.assertEqual(result["remaining_payment"], 50)
        self.assertEqual(result["difference"], 50)

    def test_multiple_receivables_exact_match_uses_all_targets(self):
        rows = [
            receivable_row(1, "2026-01-01", 100),
            receivable_row(2, "2026-02-01", 200),
        ]
        preview = self._preview(rows, 300)
        self.assertEqual(preview["pattern"], EXACT_MATCH_PATTERN)
        self.assertIsNone(preview["mode"])
        self.assertEqual(preview["target_total"], 300)
        self.assertEqual(preview["difference"], 0)
        self.assertEqual(len(preview["source_candidates"]), 2)

    def test_shortage_partial_mode_uses_fifo_partial_candidates(self):
        rows = [
            receivable_row(1, "2026-01-01", 100),
            receivable_row(2, "2026-02-01", 200),
        ]
        preview = self._preview(
            rows,
            150,
            mode=PARTIAL_SETTLEMENT_MODE,
        )
        self.assertEqual(preview["pattern"], PARTIAL_SETTLEMENT_PATTERN)
        self.assertEqual(preview["target_total"], 150)
        self.assertEqual(preview["difference"], 0)
        self.assertEqual(
            [item["消込予定"] for item in preview["source_candidates"]],
            [100, 50],
        )

    def test_shortage_defaults_to_current_ui_partial_mode(self):
        rows = [
            receivable_row(1, "2026-01-01", 100),
            receivable_row(2, "2026-02-01", 200),
        ]
        preview = self._preview(rows, 150)
        self.assertEqual(preview["mode"], PARTIAL_SETTLEMENT_MODE)
        self.assertEqual(preview["pattern"], PARTIAL_SETTLEMENT_PATTERN)

    def test_shortage_difference_mode_uses_all_targets_and_negative_difference(self):
        rows = [
            receivable_row(1, "2026-01-01", 100),
            receivable_row(2, "2026-02-01", 200),
        ]
        preview = self._preview(
            rows,
            250,
            mode=DIFFERENCE_ACCOUNT_MODE,
            difference_account="支払手数料",
            difference_summary="不足調整",
        )
        self.assertEqual(preview["pattern"], SHORTAGE_DIFFERENCE_PATTERN)
        self.assertEqual(preview["target_total"], 300)
        self.assertEqual(preview["difference"], -50)
        self.assertEqual(len(preview["source_candidates"]), 2)

    def test_overpayment_uses_all_targets_and_positive_difference(self):
        rows = [
            receivable_row(1, "2026-01-01", 100),
            receivable_row(2, "2026-02-01", 200),
        ]
        preview = self._preview(
            rows,
            350,
            mode=DIFFERENCE_ACCOUNT_MODE,
            difference_account="仮受金",
            difference_summary="過入金調整",
        )
        self.assertEqual(preview["pattern"], OVERPAYMENT_PATTERN)
        self.assertEqual(preview["target_total"], 300)
        self.assertEqual(preview["difference"], 50)

    def test_unknown_mode_is_rejected_without_inventing_fallback(self):
        rows = [
            receivable_row(1, "2026-01-01", 100),
            receivable_row(2, "2026-02-01", 200),
        ]
        with self.assertRaisesRegex(ValueError, "処理方法が不正です"):
            self._preview(rows, 150, mode="未知の処理")

    def test_single_receivable_journal_rows_match_existing_builder(self):
        rows = [receivable_row(1, "2026-01-01", 100)]
        preview = self._preview(rows, 100)
        expected = build_receivable_journal_rows(
            preview["source_candidates"],
            100,
            "普通預金",
            "A商事",
        )
        self.assertEqual(preview["rows"], expected)
        self.assertEqual(preview["rows"], [{
            "借方科目": "普通預金",
            "貸方科目": "未収運賃",
            "貸方補助": "",
            "部門": "",
            "金額": 100,
            "摘要": "A商事入金",
        }])

    def test_same_account_receivables_are_grouped_like_existing_builder(self):
        rows = [
            receivable_row(1, "2026-01-01", 100),
            receivable_row(2, "2026-02-01", 200),
        ]
        preview = self._preview(rows, 300)
        self.assertEqual(len(preview["rows"]), 1)
        self.assertEqual(preview["rows"][0]["金額"], 300)

    def test_different_accounts_create_distinct_abstract_rows(self):
        rows = [
            receivable_row(1, "2026-01-01", 100, account="未収運賃"),
            receivable_row(2, "2026-02-01", 200, account="売掛金"),
        ]
        preview = self._preview(rows, 300)
        self.assertEqual(
            [(row["貸方科目"], row["金額"]) for row in preview["rows"]],
            [("未収運賃", 100), ("売掛金", 200)],
        )

    def test_sub_account_and_department_are_preserved_in_abstract_rows(self):
        rows = [receivable_row(
            1,
            "2026-01-01",
            100,
            sub_account="A商事",
            department="営業",
        )]
        journal = self._preview(rows, 100)["rows"][0]
        self.assertEqual(journal["貸方補助"], "A商事")
        self.assertEqual(journal["部門"], "営業")

    def test_shortage_difference_journal_rows_match_existing_builder(self):
        rows = [
            receivable_row(1, "2026-01-01", 100),
            receivable_row(2, "2026-02-01", 200),
        ]
        preview = self._preview(
            rows,
            250,
            mode=DIFFERENCE_ACCOUNT_MODE,
            difference_account="支払手数料",
            difference_summary="不足調整",
        )
        expected = build_receivable_journal_rows(
            preview["source_candidates"],
            250,
            "普通預金",
            "A商事",
            "支払手数料",
            "debit",
            "不足調整",
        )
        self.assertEqual(preview["rows"], expected)
        self.assertEqual(
            [(row["借方科目"], row["金額"]) for row in preview["rows"]],
            [("普通預金", 250), ("支払手数料", 50)],
        )

    def test_overpayment_journal_rows_match_existing_builder(self):
        rows = [receivable_row(1, "2026-01-01", 300)]
        preview = self._preview(
            rows,
            350,
            mode=DIFFERENCE_ACCOUNT_MODE,
            difference_account="仮受金",
            difference_summary="過入金調整",
        )
        expected = build_receivable_journal_rows(
            preview["source_candidates"],
            350,
            "普通預金",
            "A商事",
            "仮受金",
            "credit",
            "過入金調整",
        )
        self.assertEqual(preview["rows"], expected)
        self.assertEqual(preview["rows"][-1], {
            "借方科目": "普通預金",
            "貸方科目": "仮受金",
            "貸方補助": "",
            "部門": "",
            "金額": 50,
            "摘要": "過入金調整",
        })

    def test_dataframe_input_is_not_modified(self):
        source = self._snapshot(
            receivable_row(2, "2026-02-01", 200),
            receivable_row(1, "2026-01-01", 100),
        )
        before = source.copy(deep=True)
        build_receivable_preview(
            source,
            "A商事",
            300,
            date(2026, 8, 30),
            "普通預金",
        )
        assert_frame_equal(source, before)

    def test_rows_input_is_not_modified(self):
        source = [
            receivable_row(2, "2026-02-01", 200),
            receivable_row(1, "2026-01-01", 100),
        ]
        before = copy.deepcopy(source)
        build_receivable_preview(
            source,
            "A商事",
            300,
            date(2026, 8, 30),
            "普通預金",
        )
        self.assertEqual(source, before)

    def test_preview_has_no_execute_only_fields(self):
        preview = self._preview(
            [receivable_row(1, "2026-01-01", 100)],
            100,
        )
        for field in ("settlement_id", "created_at", "registered"):
            self.assertNotIn(field, preview)

    def test_service_does_not_read_or_write_csv_files(self):
        rows = [receivable_row(1, "2026-01-01", 100)]
        with patch("pandas.read_csv", side_effect=AssertionError("read_csv")), patch.object(
            pd.DataFrame,
            "to_csv",
            side_effect=AssertionError("to_csv"),
        ):
            preview = self._preview(rows, 100)
        self.assertEqual(preview["target_total"], 100)

    def test_service_source_has_no_ui_api_or_persistence_dependencies(self):
        source = inspect.getsource(receivable_preview_service)
        for forbidden in (
            "current.csv",
            "receivable_history.csv",
            "transactions.csv",
            "session_state",
            "import streamlit",
            "from streamlit",
            "import fastapi",
            "from fastapi",
            "uuid.uuid4",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source.lower())

    def test_app_uses_preview_service_and_keeps_execute_side_effects(self):
        app_source = (SRC_DIR / "app.py").read_text(encoding="utf-8")
        self.assertIn("build_receivable_fifo_candidates(", app_source)
        self.assertIn("build_receivable_preview_from_fifo(", app_source)
        self.assertNotIn('fifo_df["_請求日"]', app_source)
        self.assertIn("settlement_id = uuid.uuid4().hex", app_source)
        self.assertIn("apply_receivable_candidates(", app_source)
        self.assertIn('"receivable_generated_journals"', app_source)


if __name__ == "__main__":
    unittest.main()

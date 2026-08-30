import inspect
import json
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from pandas.testing import assert_frame_equal


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import receivable_engine  # noqa: E402
import receivable_settlement_service as service  # noqa: E402
from receivable_engine import (  # noqa: E402
    CURRENT_RECEIVABLE_COLUMNS,
    HISTORY_COLUMNS,
)
from receivable_preview_service import (  # noqa: E402
    DIFFERENCE_ACCOUNT_MODE,
    PARTIAL_SETTLEMENT_MODE,
)
from receivable_report_service import build_receivable_check_rows  # noqa: E402


def current_row(
    code="C001",
    receivable_id="R001",
    *,
    customer="A商事",
    invoice_date="2026-08-01",
    balance="1000",
    status="未処理",
    account="未収金",
    sub_account="",
    department="",
    paid="0",
    summary="8月請求",
):
    return {
        "コード": code,
        "未収ID": receivable_id,
        "得意先名": customer,
        "請求日": invoice_date,
        "入金予定日": "2026-08-31",
        "未収科目": account,
        "未収補助": sub_account,
        "部門": department,
        "摘要": summary,
        "請求金額": balance,
        "入金済額": paid,
        "残高": balance,
        "ステータス": status,
    }


def history_row(label="old"):
    return {
        "消込ID": label,
        "消込日": "2026/07/31",
        "得意先名": "旧得意先",
        "コード": "OLD",
        "消込額": "100",
        "消込前残高": "100",
        "消込後残高": "0",
        "仕訳登録済": "0",
    }


class ReceivableSettlementServiceTest(unittest.TestCase):
    def setUp(self):
        self.current = pd.DataFrame(
            [current_row()], columns=CURRENT_RECEIVABLE_COLUMNS
        )
        self.history = pd.DataFrame(columns=HISTORY_COLUMNS)

    def plan(self, current=None, history=None, **overrides):
        arguments = {
            "customer_name": "A商事",
            "settlement_date": date(2026, 8, 30),
            "payment_amount": 1000,
            "receipt_account": "普通預金",
            "mode": None,
            "difference_account": None,
            "difference_summary": None,
            "settlement_id": "settlement-fixed",
            "created_at": "2026-08-30T12:34:56+09:00",
        }
        arguments.update(overrides)
        return service.build_receivable_settlement_plan(
            self.current if current is None else current,
            self.history if history is None else history,
            **arguments,
        )

    def test_exact_match_updates_only_balance_and_status(self):
        plan = self.plan()

        self.assertEqual(plan.current_after.at[0, "残高"], "0")
        self.assertEqual(plan.current_after.at[0, "ステータス"], "完了")
        self.assertEqual(plan.current_after.at[0, "入金済額"], "0")
        for column in CURRENT_RECEIVABLE_COLUMNS:
            if column not in ("残高", "ステータス"):
                self.assertEqual(
                    plan.current_after.at[0, column], self.current.at[0, column]
                )

    def test_partial_settlement_leaves_balance_and_partial_status(self):
        plan = self.plan(
            payment_amount=400,
            mode=PARTIAL_SETTLEMENT_MODE,
        )

        self.assertEqual(plan.current_after.at[0, "残高"], "600")
        self.assertEqual(plan.current_after.at[0, "ステータス"], "部分消込")
        self.assertEqual(plan.settlement["target_total"], 400)
        self.assertEqual(plan.settlement["difference"], 0)

    def test_multiple_receivables_follow_fifo(self):
        current = pd.DataFrame(
            [
                current_row("C2", "R2", invoice_date="2026-08-02", balance="600"),
                current_row("C1", "R1", invoice_date="2026-08-01", balance="500"),
            ],
            columns=CURRENT_RECEIVABLE_COLUMNS,
        )
        plan = self.plan(
            current=current,
            payment_amount=800,
            mode=PARTIAL_SETTLEMENT_MODE,
        )

        self.assertEqual(
            [item["未収ID"] for item in plan.settlement["source_candidates"]],
            ["R1", "R2"],
        )
        self.assertEqual(list(plan.current_after["残高"]), ["300", "0"])

    def test_equal_invoice_dates_keep_original_row_order(self):
        current = pd.DataFrame(
            [
                current_row("C2", "R2", balance="500"),
                current_row("C1", "R1", balance="500"),
            ],
            columns=CURRENT_RECEIVABLE_COLUMNS,
        )
        plan = self.plan(
            current=current,
            payment_amount=600,
            mode=PARTIAL_SETTLEMENT_MODE,
        )
        self.assertEqual(
            [item["未収ID"] for item in plan.settlement["source_candidates"]],
            ["R2", "R1"],
        )

    def test_multiple_accounts_build_grouped_abstract_journal_rows(self):
        current = pd.DataFrame(
            [
                current_row("C1", "R1", balance="300", account="未収金A"),
                current_row(
                    "C2", "R2", balance="700", account="未収金B",
                    sub_account="補助B", department="営業部"
                ),
            ],
            columns=CURRENT_RECEIVABLE_COLUMNS,
        )
        plan = self.plan(current=current)
        self.assertEqual(len(plan.settlement["rows"]), 2)
        self.assertEqual(
            [row["貸方科目"] for row in plan.settlement["rows"]],
            ["未収金A", "未収金B"],
        )
        self.assertEqual(plan.settlement["rows"][1]["貸方補助"], "補助B")
        self.assertEqual(plan.settlement["rows"][1]["部門"], "営業部")

    def test_shortage_difference_mode_clears_full_receivable(self):
        plan = self.plan(
            payment_amount=900,
            mode=DIFFERENCE_ACCOUNT_MODE,
            difference_account="支払手数料",
            difference_summary="手数料差額",
        )
        self.assertEqual(plan.current_after.at[0, "残高"], "0")
        self.assertEqual(plan.settlement["target_total"], 1000)
        self.assertEqual(plan.settlement["difference"], -100)
        self.assertEqual(len(plan.settlement["rows"]), 2)

    def test_overpayment_clears_receivable_and_preserves_positive_difference(self):
        plan = self.plan(
            payment_amount=1200,
            mode=DIFFERENCE_ACCOUNT_MODE,
            difference_account="仮受金",
        )
        self.assertEqual(plan.current_after.at[0, "残高"], "0")
        self.assertEqual(plan.settlement["difference"], 200)
        self.assertEqual(plan.settlement["rows"][-1]["貸方科目"], "仮受金")

    def test_history_has_one_row_per_settled_receivable(self):
        current = pd.DataFrame(
            [current_row("C1", "R1", balance="400"), current_row("C2", "R2", balance="600")],
            columns=CURRENT_RECEIVABLE_COLUMNS,
        )
        plan = self.plan(current=current)
        self.assertEqual(len(plan.history_after), 2)
        self.assertEqual(list(plan.history_after["消込ID"]), ["settlement-fixed"] * 2)
        self.assertEqual(list(plan.history_after["消込額"]), ["400", "600"])

    def test_history_records_before_and_after_balance(self):
        plan = self.plan(payment_amount=300, mode=PARTIAL_SETTLEMENT_MODE)
        row = plan.history_after.iloc[0]
        self.assertEqual(row["消込前残高"], "1000")
        self.assertEqual(row["消込後残高"], "700")
        self.assertEqual(row["仕訳登録済"], "0")

    def test_existing_history_values_and_order_are_preserved(self):
        history = pd.DataFrame([history_row("first"), history_row("second")])
        before = history.copy(deep=True)
        plan = self.plan(history=history)
        assert_frame_equal(plan.history_after.iloc[:2].reset_index(drop=True), before)
        self.assertEqual(plan.history_after.iloc[2]["消込ID"], "settlement-fixed")

    def test_empty_row_sequence_becomes_formal_history(self):
        plan = self.plan(history=[])
        self.assertEqual(list(plan.history_after.columns), HISTORY_COLUMNS)
        self.assertEqual(len(plan.history_after), 1)

    def test_current_input_is_not_mutated(self):
        before = self.current.copy(deep=True)
        self.plan()
        assert_frame_equal(self.current, before)

    def test_history_input_is_not_mutated(self):
        history = pd.DataFrame([history_row()])
        before = history.copy(deep=True)
        self.plan(history=history)
        assert_frame_equal(history, before)

    def test_extra_current_columns_and_column_order_are_preserved(self):
        current = self.current.copy()
        current.insert(0, "追加列", ["保持"])
        plan = self.plan(current=current)
        self.assertEqual(list(plan.current_after.columns), list(current.columns))
        self.assertEqual(plan.current_after.at[0, "追加列"], "保持")

    def test_non_target_rows_are_unchanged_and_not_sorted(self):
        current = pd.DataFrame(
            [
                current_row("Z", "RZ", customer="別会社", balance="300"),
                current_row("A", "RA", balance="1000"),
            ],
            columns=CURRENT_RECEIVABLE_COLUMNS,
        )
        before_non_target = current.iloc[0].copy()
        plan = self.plan(current=current)
        pd.testing.assert_series_equal(plan.current_after.iloc[0], before_non_target)
        self.assertEqual(list(plan.current_after["コード"]), ["Z", "A"])

    def test_serialized_after_bytes_have_bom_and_formal_headers(self):
        plan = self.plan()
        self.assertTrue(plan.current_after_bytes.startswith(b"\xef\xbb\xbf"))
        self.assertTrue(plan.history_after_bytes.startswith(b"\xef\xbb\xbf"))
        current_header = plan.current_after_bytes.decode("utf-8-sig").splitlines()[0]
        history_header = plan.history_after_bytes.decode("utf-8-sig").splitlines()[0]
        self.assertEqual(current_header.split(","), CURRENT_RECEIVABLE_COLUMNS)
        self.assertEqual(history_header.split(","), HISTORY_COLUMNS)

    def test_settlement_dto_has_required_fields_and_is_json_safe(self):
        plan = self.plan()
        self.assertEqual(
            set(plan.settlement),
            {
                "settlement_id", "settlement_date", "customer_name",
                "payment_amount", "target_total", "difference",
                "source_candidates", "rows", "created_at",
            },
        )
        json.dumps(plan.settlement, allow_nan=False)

    def test_dates_are_normalized_and_created_at_is_injected(self):
        created = datetime(2026, 8, 30, 3, 4, tzinfo=timezone.utc)
        plan = self.plan(settlement_date="2026/08/30", created_at=created)
        self.assertEqual(plan.settlement["settlement_date"], "2026-08-30")
        self.assertEqual(plan.settlement["created_at"], created.isoformat())
        self.assertEqual(plan.history_after.iloc[-1]["消込日"], "2026/08/30")

    def test_settlement_dto_is_compatible_with_receivable_report_service(self):
        rows = build_receivable_check_rows([self.plan().settlement])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["取引先"], "A商事")
        self.assertEqual(rows[0]["消込額"], 1000)

    def test_legacy_apply_parity_for_current_and_history(self):
        plan = self.plan(payment_amount=400, mode=PARTIAL_SETTLEMENT_MODE)
        captured_current = []
        captured_history = []

        def capture_csv(frame, *args, **kwargs):
            captured_current.append(frame.copy(deep=True))

        with patch.object(
            receivable_engine, "load_receivables", return_value=self.current.copy(deep=True)
        ), patch.object(
            receivable_engine, "save_receivable_history", side_effect=captured_history.append
        ), patch.object(pd.DataFrame, "to_csv", capture_csv):
            receivable_engine.apply_receivable_candidates(
                plan.settlement["source_candidates"],
                date(2026, 8, 30),
                "settlement-fixed",
            )

        assert_frame_equal(captured_current[0], plan.current_after)
        legacy_history = receivable_engine.normalize_receivable_history(
            pd.DataFrame(captured_history[0])
        )
        assert_frame_equal(legacy_history, plan.history_after, check_dtype=False)

    def test_legacy_code_fallback_is_used_only_for_empty_id(self):
        current = pd.DataFrame(
            [current_row("LEGACY", "", balance="1000")],
            columns=CURRENT_RECEIVABLE_COLUMNS,
        )
        plan = self.plan(current=current)
        self.assertEqual(plan.current_after.at[0, "残高"], "0")

    def test_nonempty_id_is_preferred_when_codes_are_duplicated(self):
        current = pd.DataFrame(
            [
                current_row("SAME", "R1", balance="400"),
                current_row("SAME", "R2", balance="600"),
            ],
            columns=CURRENT_RECEIVABLE_COLUMNS,
        )
        plan = self.plan(current=current)
        self.assertEqual(list(plan.current_after["残高"]), ["0", "0"])

    def test_duplicate_receivable_id_is_a_conflict(self):
        current = pd.DataFrame(
            [
                current_row("C1", "DUP", balance="400"),
                current_row("C2", "DUP", balance="600"),
            ],
            columns=CURRENT_RECEIVABLE_COLUMNS,
        )
        with self.assertRaises(service.ReceivableSettlementConflictError):
            self.plan(current=current)

    def test_empty_customer_is_invalid(self):
        with self.assertRaises(service.ReceivableSettlementValidationError):
            self.plan(customer_name="")

    def test_customer_without_receivables_is_invalid(self):
        with self.assertRaises(service.ReceivableSettlementValidationError):
            self.plan(customer_name="不存在")

    def test_zero_balance_or_completed_rows_are_not_targets(self):
        for row in (
            current_row(balance="0"),
            current_row(status="完了"),
        ):
            with self.subTest(row=row):
                with self.assertRaises(service.ReceivableSettlementValidationError):
                    self.plan(current=pd.DataFrame([row], columns=CURRENT_RECEIVABLE_COLUMNS))

    def test_invalid_payment_amount_is_rejected(self):
        for value in (0, -1, "abc", None):
            with self.subTest(value=value):
                with self.assertRaises(service.ReceivableSettlementValidationError):
                    self.plan(payment_amount=value)

    def test_invalid_mode_is_rejected(self):
        with self.assertRaises(service.ReceivableSettlementValidationError):
            self.plan(payment_amount=900, mode="invalid")

    def test_empty_receipt_account_is_rejected(self):
        with self.assertRaises(service.ReceivableSettlementValidationError):
            self.plan(receipt_account="")

    def test_difference_account_is_required_for_difference_mode(self):
        with self.assertRaises(service.ReceivableSettlementValidationError):
            self.plan(payment_amount=900, mode=DIFFERENCE_ACCOUNT_MODE)

    def test_optional_account_master_validates_selected_accounts(self):
        plan = self.plan(account_master_snapshot=["普通預金", "未収金"])
        self.assertEqual(plan.settlement["payment_amount"], 1000)
        with self.assertRaises(service.ReceivableSettlementValidationError):
            self.plan(account_master_snapshot=["未収金"])

    def test_overscheduled_candidate_is_rejected_during_current_recheck(self):
        fake_preview = {
            "payment_amount": 1001,
            "target_total": 1001,
            "difference": 0,
            "source_candidates": [
                {
                    "コード": "C001", "未収ID": "R001", "消込予定": 1001,
                    "未収科目": "未収金", "未収補助": "", "部門": "",
                    "請求日": "2026-08-01", "請求額": 1000, "残高": 1000,
                    "取引先": "A商事", "摘要": "8月請求",
                }
            ],
            "rows": [],
        }
        with patch.object(service, "build_receivable_preview", return_value=fake_preview):
            with self.assertRaises(service.ReceivableSettlementConflictError):
                self.plan(payment_amount=1001)

    def test_nan_in_dto_is_rejected(self):
        current = self.current.copy()
        current.at[0, "摘要"] = float("nan")
        with self.assertRaises(service.ReceivableSettlementValidationError):
            self.plan(current=current)

    def test_missing_current_column_is_rejected(self):
        with self.assertRaises(service.ReceivableSettlementValidationError):
            self.plan(current=self.current.drop(columns=["未収ID"]))

    def test_missing_history_column_is_rejected(self):
        history = pd.DataFrame([history_row()]).drop(columns=["消込ID"])
        with self.assertRaises(service.ReceivableSettlementValidationError):
            self.plan(history=history)

    def test_settlement_id_and_created_at_are_not_generated(self):
        source = inspect.getsource(service)
        self.assertNotIn("uuid.uuid4", source)
        self.assertNotIn("datetime.now", source)
        with self.assertRaises(service.ReceivableSettlementValidationError):
            self.plan(settlement_id="")
        with self.assertRaises(service.ReceivableSettlementValidationError):
            self.plan(created_at=None)

    def test_service_has_no_filesystem_or_persistence_dependency(self):
        source = inspect.getsource(service)
        self.assertNotIn("receivable_persistence_service", source)
        self.assertNotIn("read_csv", source)
        self.assertNotIn("to_csv(", source.replace("dataframe.to_csv(", ""))
        self.assertNotIn("os.replace", source)


if __name__ == "__main__":
    unittest.main()

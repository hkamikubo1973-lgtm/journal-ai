import copy
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import receivable_persistence_service as persistence  # noqa: E402
import receivable_preview_application_service as application  # noqa: E402
from receivable_account_validation_service import (  # noqa: E402
    ReceivableSettlementMasterValidationError,
)
from receivable_engine import CURRENT_RECEIVABLE_COLUMNS, HISTORY_COLUMNS  # noqa: E402


def current_row(
    code="C001",
    receivable_id="R001",
    *,
    customer="A商事",
    billing_date="2026-08-01",
    balance="1000",
    status="未処理",
):
    return {
        "コード": code,
        "未収ID": receivable_id,
        "得意先名": customer,
        "請求日": billing_date,
        "入金予定日": "2026-08-31",
        "未収科目": "未収運賃",
        "未収補助": customer,
        "部門": "営業部",
        "摘要": "8月請求",
        "請求金額": balance,
        "入金済額": "0",
        "残高": balance,
        "ステータス": status,
    }


class ReceivablePreviewApplicationServiceTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        self.paths = persistence.resolve_receivable_ledger_paths(self.directory)
        self.write_current([current_row()])
        self.write_history([])
        self.revision = persistence.calculate_current_revision(
            self.paths.current_path.read_bytes()
        )
        self.master = {
            "accounts": [
                {"code": "111", "name": "普通預金", "category": "資産"},
                {"code": "751", "name": "支払手数料", "category": "費用"},
                {"code": "251", "name": "仮受金", "category": "負債"},
                {"code": "999", "name": "未収運賃", "category": "資産"},
            ]
        }

    def tearDown(self):
        self.temporary_directory.cleanup()

    def write_current(self, rows):
        pd.DataFrame(rows, columns=CURRENT_RECEIVABLE_COLUMNS).to_csv(
            self.paths.current_path,
            index=False,
            encoding="utf-8-sig",
        )

    def write_history(self, rows):
        pd.DataFrame(rows, columns=HISTORY_COLUMNS).to_csv(
            self.paths.history_path,
            index=False,
            encoding="utf-8-sig",
        )

    def preview(self, **overrides):
        arguments = {
            "ledger_revision": self.revision,
            "customer_name": "A商事",
            "settlement_date": date(2026, 8, 30),
            "payment_amount": 1000,
            "receipt_account": "普通預金",
            "mode": None,
            "difference_account": None,
            "difference_summary": None,
            "account_master_snapshot": self.master,
            "transactions_snapshot": [],
            "lock_timeout_seconds": 0.5,
            "lock_poll_interval_seconds": 0.01,
        }
        arguments.update(overrides)
        return application.build_receivable_preview_application_result(
            self.directory,
            **arguments,
        )

    def test_exact_match_is_complete_with_wire_dto(self):
        result = self.preview()

        self.assertEqual(result["pattern"], "exact_match")
        self.assertIsNone(result["mode"])
        self.assertEqual(result["available_modes"], [])
        self.assertEqual(result["original_difference"], 0)
        self.assertEqual(result["difference"], 0)
        self.assertTrue(result["preview_complete"])
        self.assertFalse(result["difference_account_required"])
        self.assertEqual(result["recommended_difference_accounts"], [])

    def test_shortage_none_preserves_partial_complete_behavior(self):
        result = self.preview(payment_amount=900)

        self.assertEqual(result["pattern"], "partial_settlement")
        self.assertEqual(result["mode"], "partial")
        self.assertEqual(result["available_modes"], [
            "partial", "difference_account"
        ])
        self.assertEqual(result["original_difference"], -100)
        self.assertEqual(result["difference"], 0)
        self.assertTrue(result["preview_complete"])
        self.assertEqual(
            result["recommended_difference_accounts"],
            [{"code": "751", "name": "支払手数料"}],
        )

    def test_explicit_partial_ignores_irrelevant_difference_account(self):
        result = self.preview(
            payment_amount=900,
            mode="partial",
            difference_account="存在しない科目",
        )

        self.assertEqual(result["pattern"], "partial_settlement")
        self.assertEqual(result["difference"], 0)

    def test_shortage_difference_mode_requires_account(self):
        with self.assertRaisesRegex(
            ReceivableSettlementMasterValidationError,
            "difference account is required",
        ):
            self.preview(payment_amount=900, mode="difference_account")

    def test_shortage_difference_is_complete_and_uses_default_summary(self):
        result = self.preview(
            payment_amount=900,
            mode="difference_account",
            difference_account="支払手数料",
        )

        self.assertEqual(result["pattern"], "shortage_difference")
        self.assertEqual(result["mode"], "difference_account")
        self.assertEqual(result["original_difference"], -100)
        self.assertEqual(result["difference"], -100)
        self.assertTrue(result["difference_account_required"])
        difference_row = next(
            row for row in result["rows"]
            if row["debit_account"] == "支払手数料"
        )
        self.assertEqual(difference_row["summary"], "A商事 差額調整")

    def test_overpayment_none_returns_incomplete_analysis(self):
        result = self.preview(payment_amount=1200)

        self.assertEqual(result["pattern"], "overpayment")
        self.assertIsNone(result["mode"])
        self.assertEqual(result["available_modes"], ["difference_account"])
        self.assertEqual(result["total_receivable_balance"], 1000)
        self.assertEqual(result["original_difference"], 200)
        self.assertEqual(result["target_total"], 1000)
        self.assertEqual(result["difference"], 200)
        self.assertTrue(result["difference_account_required"])
        self.assertFalse(result["preview_complete"])
        self.assertEqual(result["rows"], [])
        self.assertEqual(
            result["recommended_difference_accounts"],
            [{"code": "251", "name": "仮受金"}],
        )

    def test_overpayment_difference_mode_is_complete_with_default_summary(self):
        result = self.preview(
            payment_amount=1200,
            mode="difference_account",
            difference_account="仮受金",
        )

        self.assertTrue(result["preview_complete"])
        self.assertEqual(result["pattern"], "overpayment")
        self.assertEqual(result["mode"], "difference_account")
        self.assertEqual(result["rows"][-1], {
            "debit_account": "普通預金",
            "credit_account": "仮受金",
            "credit_sub_account": "",
            "department": "",
            "amount": 200,
            "summary": "A商事 過入金調整",
        })

    def test_overpayment_explicit_difference_mode_without_account_is_invalid(self):
        with self.assertRaisesRegex(
            ReceivableSettlementMasterValidationError,
            "difference account is required",
        ):
            self.preview(payment_amount=1200, mode="difference_account")

    def test_invalid_wire_mode_is_application_validation_error(self):
        with self.assertRaisesRegex(
            application.ReceivablePreviewValidationError,
            "mode is invalid",
        ):
            self.preview(payment_amount=900, mode="部分消込")

    def test_missing_receipt_account_is_shared_master_error(self):
        with self.assertRaisesRegex(
            ReceivableSettlementMasterValidationError,
            "receipt account does not exist",
        ):
            self.preview(receipt_account="当座預金")

    def test_ambiguous_receipt_account_is_shared_master_error(self):
        self.master["accounts"].append(
            {"code": "112", "name": "普通預金", "category": "資産"}
        )
        with self.assertRaisesRegex(
            ReceivableSettlementMasterValidationError,
            "receipt account is ambiguous",
        ):
            self.preview()

    def test_customer_without_active_receivables_has_dedicated_error(self):
        with self.assertRaises(
            application.ReceivablePreviewCustomerNotFoundError
        ):
            self.preview(customer_name="B商事")

    def test_inactive_only_customer_is_not_found(self):
        self.write_current([current_row(status="完了")])
        self.revision = persistence.calculate_current_revision(
            self.paths.current_path.read_bytes()
        )
        with self.assertRaises(
            application.ReceivablePreviewCustomerNotFoundError
        ):
            self.preview()

    def test_revision_conflict_precedes_master_fifo_and_recommendation(self):
        with patch.object(
            application, "validate_receivable_settlement_accounts"
        ) as validator, patch.object(
            application, "build_receivable_fifo_candidates"
        ) as fifo, patch.object(
            application, "_recommendations"
        ) as recommendations:
            with self.assertRaises(persistence.ReceivableLedgerConflictError):
                self.preview(
                    ledger_revision="0" * 64,
                    account_master_snapshot={"accounts": []},
                )

        validator.assert_not_called()
        fifo.assert_not_called()
        recommendations.assert_not_called()

    def test_missing_history_is_not_created(self):
        self.paths.history_path.unlink()
        result = self.preview()
        self.assertTrue(result["preview_complete"])
        self.assertFalse(self.paths.history_path.exists())

    def test_ambiguous_recommendation_is_filtered_by_safe_resolver(self):
        self.master["accounts"].append(
            {"code": "252", "name": "仮受金", "category": "負債"}
        )
        result = self.preview(payment_amount=1200)
        self.assertEqual(result["recommended_difference_accounts"], [])

    def test_candidate_and_journal_dto_fields_are_exact_and_json_safe(self):
        result = self.preview()
        self.assertEqual(list(result["source_candidates"][0]), [
            "code", "receivable_id", "billing_date", "billed_amount",
            "balance", "scheduled_amount", "receivable_account",
            "receivable_sub_account", "department", "customer_name",
            "summary",
        ])
        self.assertEqual(list(result["rows"][0]), [
            "debit_account", "credit_account", "credit_sub_account",
            "department", "amount", "summary",
        ])
        self.assertIsInstance(result["source_candidates"][0]["balance"], int)
        self.assertIsInstance(result["rows"][0]["amount"], int)
        json.dumps(result, ensure_ascii=False, allow_nan=False)

    def test_master_and_transactions_inputs_are_not_mutated(self):
        master = copy.deepcopy(self.master)
        transactions = [{"rows": [{"借方科目": "雑費", "摘要": "A商事"}]}]
        before_master = copy.deepcopy(master)
        before_transactions = copy.deepcopy(transactions)

        self.preview(
            payment_amount=900,
            account_master_snapshot=master,
            transactions_snapshot=transactions,
        )

        self.assertEqual(master, before_master)
        self.assertEqual(transactions, before_transactions)

    def test_production_recommendation_uses_b1_safe_wrapper(self):
        safe_result = {
            "recommended_difference_accounts": [
                {"code": "751", "name": "支払手数料"}
            ]
        }
        with patch.object(
            application,
            "load_safe_receivable_difference_options",
            return_value=safe_result,
        ) as loader:
            result = self.preview(
                payment_amount=900,
                transactions_snapshot=None,
            )

        loader.assert_called_once()
        self.assertEqual(
            result["recommended_difference_accounts"],
            safe_result["recommended_difference_accounts"],
        )


if __name__ == "__main__":
    unittest.main()

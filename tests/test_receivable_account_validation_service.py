import copy
import sys
import unittest
from pathlib import Path

import pandas as pd


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import receivable_account_validation_service as validation  # noqa: E402
import receivable_settlement_execute_service as execute  # noqa: E402


class ReceivableAccountValidationServiceTest(unittest.TestCase):
    def setUp(self):
        self.master = {
            "accounts": [
                {"code": "111", "name": "普通預金", "category": "資産"},
                {"code": "751", "name": "支払手数料", "category": "費用"},
            ]
        }

    def validate(self, **overrides):
        arguments = {
            "receipt_account": "普通預金",
            "difference_account": None,
            "difference_required": False,
        }
        arguments.update(overrides)
        return validation.validate_receivable_settlement_accounts(
            self.master,
            **arguments,
        )

    def test_receipt_account_resolves_to_one_code(self):
        self.validate()

    def test_receipt_account_is_always_required(self):
        with self.assertRaisesRegex(
            validation.ReceivableSettlementMasterValidationError,
            "receipt account is required",
        ):
            self.validate(receipt_account="")

    def test_missing_receipt_account_is_rejected(self):
        with self.assertRaisesRegex(
            validation.ReceivableSettlementMasterValidationError,
            "receipt account does not exist",
        ):
            self.validate(receipt_account="当座預金")

    def test_distinct_codes_for_same_receipt_name_are_ambiguous(self):
        self.master["accounts"].append({"code": "112", "name": "普通預金"})
        with self.assertRaisesRegex(
            validation.ReceivableSettlementMasterValidationError,
            "receipt account is ambiguous",
        ):
            self.validate()

    def test_required_difference_account_resolves(self):
        self.validate(
            difference_account="支払手数料",
            difference_required=True,
        )

    def test_missing_required_difference_account_is_rejected(self):
        with self.assertRaisesRegex(
            validation.ReceivableSettlementMasterValidationError,
            "difference account does not exist",
        ):
            self.validate(
                difference_account="雑費",
                difference_required=True,
            )

    def test_ambiguous_required_difference_account_is_rejected(self):
        self.master["accounts"].append(
            {"code": "752", "name": "支払手数料"}
        )
        with self.assertRaisesRegex(
            validation.ReceivableSettlementMasterValidationError,
            "difference account is ambiguous",
        ):
            self.validate(
                difference_account="支払手数料",
                difference_required=True,
            )

    def test_malformed_master_snapshot_is_rejected(self):
        for snapshot in (
            None,
            {"accounts": "bad"},
            {"accounts": [{"code": "111", "name": ""}]},
            {"accounts": ["bad-row"]},
        ):
            with self.subTest(snapshot=snapshot):
                with self.assertRaises(
                    validation.ReceivableSettlementMasterValidationError
                ):
                    validation.validate_receivable_settlement_accounts(
                        snapshot,
                        receipt_account="普通預金",
                        difference_account=None,
                        difference_required=False,
                    )

    def test_dataframe_snapshot_is_not_mutated(self):
        snapshot = pd.DataFrame(self.master["accounts"])
        before = snapshot.copy(deep=True)
        validation.validate_receivable_settlement_accounts(
            snapshot,
            receipt_account="普通預金",
            difference_account="支払手数料",
            difference_required=True,
        )
        pd.testing.assert_frame_equal(snapshot, before)

    def test_rows_snapshot_is_not_mutated(self):
        before = copy.deepcopy(self.master)
        self.validate(
            difference_account="支払手数料",
            difference_required=True,
        )
        self.assertEqual(self.master, before)

    def test_execute_module_reexports_existing_public_symbols(self):
        self.assertIs(
            execute.validate_receivable_settlement_accounts,
            validation.validate_receivable_settlement_accounts,
        )
        self.assertIs(
            execute.ReceivableSettlementMasterValidationError,
            validation.ReceivableSettlementMasterValidationError,
        )


if __name__ == "__main__":
    unittest.main()

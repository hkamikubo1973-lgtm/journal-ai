import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import receivable_registration_handoff_application_service as service  # noqa: E402
from receivable_registration_handoff_service import (  # noqa: E402
    ReceivableRegistrationHandoffValidationError,
)


class ReceivableRegistrationHandoffApplicationServiceTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path("unused-receivables")
        self.receipt_ref = "a" * 64
        self.settlement = {
            "settlement_id": "settlement-001",
            "settlement_date": "2026-09-07",
            "customer_name": "A商事",
            "rows": [{"trusted": "row"}],
        }
        self.receipt = SimpleNamespace(
            receipt_ref=self.receipt_ref,
            settlement_id="settlement-001",
            settlement=self.settlement,
        )
        self.masters = {
            "accounts": [{"code": "100", "name": "普通預金"}],
            "departments": [],
            "sub_account_relations": [],
        }
        self.items = [{"source_type": "receivable_settlement"}]

    def build(self):
        return service.build_receivable_registration_handoff_application_result(
            self.directory,
            settlement_id="settlement-001",
            receipt_ref=self.receipt_ref,
            journal_master_snapshot=self.masters,
        )

    def test_secure_receipt_loader_receives_only_reference_and_expected_id(self):
        with patch.object(
            service,
            "read_receivable_settlement_receipt",
            return_value=self.receipt,
        ) as loader, patch.object(
            service,
            "build_receivable_registration_handoff_items",
            return_value=self.items,
        ):
            self.build()

        loader.assert_called_once_with(
            self.directory,
            self.receipt_ref,
            expected_settlement_id="settlement-001",
        )

    def test_current_master_snapshot_and_trusted_settlement_reach_converter(self):
        with patch.object(
            service,
            "read_receivable_settlement_receipt",
            return_value=self.receipt,
        ), patch.object(
            service,
            "build_receivable_registration_handoff_items",
            return_value=self.items,
        ) as converter:
            self.build()

        converter.assert_called_once_with(
            self.settlement,
            self.receipt_ref,
            account_master_snapshot=self.masters,
            sub_account_relation_snapshot=self.masters,
            department_master_snapshot=self.masters,
        )

    def test_response_metadata_comes_from_validated_receipt(self):
        with patch.object(
            service,
            "read_receivable_settlement_receipt",
            return_value=self.receipt,
        ), patch.object(
            service,
            "build_receivable_registration_handoff_items",
            return_value=self.items,
        ):
            result = self.build()

        self.assertEqual(result, {
            "settlement_id": "settlement-001",
            "receipt_ref": self.receipt_ref,
            "settlement_date": "2026-09-07",
            "customer_name": "A商事",
            "row_count": 1,
            "items": self.items,
        })

    def test_converter_failure_returns_no_partial_application_result(self):
        with patch.object(
            service,
            "read_receivable_settlement_receipt",
            return_value=self.receipt,
        ), patch.object(
            service,
            "build_receivable_registration_handoff_items",
            side_effect=ReceivableRegistrationHandoffValidationError(
                "row 2 invalid"
            ),
        ), self.assertRaises(ReceivableRegistrationHandoffValidationError):
            self.build()


if __name__ == "__main__":
    unittest.main()

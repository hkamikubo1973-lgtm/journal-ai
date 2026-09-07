import builtins
import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from receivable_registration_handoff_service import (  # noqa: E402
    ReceivableRegistrationHandoffValidationError,
    build_receivable_registration_handoff_items,
)


class ReceivableRegistrationHandoffServiceTest(unittest.TestCase):
    def setUp(self):
        self.receipt_ref = "a" * 64
        self.settlement = {
            "settlement_id": "settlement-001",
            "settlement_date": "2026-09-07",
            "rows": [self.row()],
        }
        self.accounts = {
            "accounts": [
                {"code": "100", "name": "普通預金"},
                {"code": "200", "name": "売掛金"},
                {"code": "201", "name": "未収金"},
            ]
        }
        self.relations = {
            "sub_account_relations": [
                {
                    "account_code": "200",
                    "sub_code": "01",
                    "sub_name": "A商事",
                },
                {
                    "account_code": "201",
                    "sub_code": "02",
                    "sub_name": "B商事",
                },
            ]
        }
        self.departments = {
            "departments": [
                {"code": "10", "name": "営業部"},
                {"code": "20", "name": "管理部"},
            ]
        }

    @staticmethod
    def row(**overrides):
        result = {
            "借方科目": "普通預金",
            "貸方科目": "売掛金",
            "貸方補助": "A商事",
            "部門": "営業部",
            "金額": 1000,
            "摘要": "8月分入金",
        }
        result.update(overrides)
        return result

    def build(self, **overrides):
        arguments = {
            "settlement": self.settlement,
            "receipt_ref": self.receipt_ref,
            "account_master_snapshot": self.accounts,
            "sub_account_relation_snapshot": self.relations,
            "department_master_snapshot": self.departments,
        }
        arguments.update(overrides)
        return build_receivable_registration_handoff_items(**arguments)

    def test_single_row_is_converted_to_prepared_journal(self):
        before = copy.deepcopy(self.settlement)

        item = self.build()[0]

        self.assertEqual(item["source_type"], "receivable_settlement")
        self.assertEqual(item["prepared_journal"]["amount"], 1000)
        self.assertEqual(item["prepared_journal"]["summary"], "8月分入金")
        self.assertEqual(item["prepared_journal"]["source_debit_amount"], "1000")
        self.assertEqual(item["prepared_journal"]["source_credit_amount"], "1000")
        self.assertEqual(self.settlement, before)

    def test_multiple_rows_preserve_receipt_order(self):
        self.settlement["rows"] = [
            self.row(摘要="first", 金額=100),
            self.row(摘要="second", 金額=200),
            self.row(摘要="third", 金額=300),
        ]

        items = self.build()

        self.assertEqual(
            [item["prepared_journal"]["summary"] for item in items],
            ["first", "second", "third"],
        )

    def test_settlement_row_id_is_deterministic_canonical_sha256(self):
        first = self.build()[0]["provenance"]["settlement_row_id"]
        second = self.build()[0]["provenance"]["settlement_row_id"]
        material = [
            "receivable-settlement-row-v1",
            self.receipt_ref,
            "settlement-001",
            0,
        ]
        expected = hashlib.sha256(json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")).hexdigest()

        self.assertEqual(first, second)
        self.assertEqual(first, expected)
        self.assertRegex(first, r"^[0-9a-f]{64}$")

    def test_row_index_is_zero_based(self):
        self.settlement["rows"] = [self.row(金額=1), self.row(金額=2)]

        items = self.build()

        self.assertEqual(
            [item["provenance"]["row_index"] for item in items],
            [0, 1],
        )

    def test_row_count_is_total_on_every_item(self):
        self.settlement["rows"] = [self.row(金額=1), self.row(金額=2)]

        items = self.build()

        self.assertEqual(
            [item["provenance"]["row_count"] for item in items],
            [2, 2],
        )

    def test_voucher_date_is_yyyymmdd(self):
        self.assertEqual(
            self.build()[0]["prepared_journal"]["voucher_date"],
            "20260907",
        )

    def test_voucher_no_is_settlement_id(self):
        item = self.build()[0]
        self.assertEqual(item["prepared_journal"]["voucher_no"], "settlement-001")
        self.assertEqual(item["provenance"]["settlement_id"], "settlement-001")
        self.assertEqual(item["provenance"]["receipt_ref"], self.receipt_ref)

    def test_voucher_summary_is_empty(self):
        self.assertEqual(
            self.build()[0]["prepared_journal"]["voucher_summary"],
            "",
        )

    def test_debit_department_and_sub_account_are_empty(self):
        journal = self.build()[0]["prepared_journal"]
        self.assertEqual(journal["debit_sub_code"], "")
        self.assertEqual(journal["debit_sub_name"], "")
        self.assertEqual(journal["debit_dept_code"], "")
        self.assertEqual(journal["debit_dept_name"], "")

    def test_credit_department_name_resolves_uniquely(self):
        journal = self.build()[0]["prepared_journal"]
        self.assertEqual(journal["credit_dept_code"], "10")
        self.assertEqual(journal["credit_dept_name"], "営業部")

    def test_credit_sub_account_resolves_with_parent_relation(self):
        journal = self.build()[0]["prepared_journal"]
        self.assertEqual(journal["debit_account_code"], "100")
        self.assertEqual(journal["credit_account_code"], "200")
        self.assertEqual(journal["credit_sub_code"], "01")
        self.assertEqual(journal["credit_sub_name"], "A商事")

    def test_empty_credit_sub_account_has_empty_code_and_name(self):
        self.settlement["rows"] = [self.row(貸方補助="")]

        journal = self.build()[0]["prepared_journal"]

        self.assertEqual(journal["credit_sub_code"], "")
        self.assertEqual(journal["credit_sub_name"], "")

    def test_missing_account_fails_entire_settlement(self):
        self.settlement["rows"] = [self.row(貸方科目="不存在")]

        with self.assertRaisesRegex(
            ReceivableRegistrationHandoffValidationError,
            "does not exist in the account master",
        ):
            self.build()

    def test_ambiguous_account_name_fails(self):
        self.accounts["accounts"].append(
            {"code": "101", "name": "普通預金"}
        )

        with self.assertRaisesRegex(
            ReceivableRegistrationHandoffValidationError,
            "ambiguous in the account master",
        ):
            self.build()

    def test_credit_sub_account_parent_mismatch_fails(self):
        self.relations["sub_account_relations"][0]["account_code"] = "201"

        with self.assertRaisesRegex(
            ReceivableRegistrationHandoffValidationError,
            "does not belong to account 200",
        ):
            self.build()

    def test_ambiguous_credit_sub_account_name_fails(self):
        self.relations["sub_account_relations"].append({
            "account_code": "200",
            "sub_code": "09",
            "sub_name": "A商事",
        })

        with self.assertRaisesRegex(
            ReceivableRegistrationHandoffValidationError,
            "ambiguous for account 200",
        ):
            self.build()

    def test_missing_department_fails(self):
        self.settlement["rows"] = [self.row(部門="不存在")]

        with self.assertRaisesRegex(
            ReceivableRegistrationHandoffValidationError,
            "does not exist in the department master",
        ):
            self.build()

    def test_ambiguous_department_name_fails(self):
        self.departments["departments"].append(
            {"code": "99", "name": "営業部"}
        )

        with self.assertRaisesRegex(
            ReceivableRegistrationHandoffValidationError,
            "ambiguous in the department master",
        ):
            self.build()

    def test_empty_department_has_empty_code_and_name(self):
        self.settlement["rows"] = [self.row(部門="")]

        journal = self.build()[0]["prepared_journal"]

        self.assertEqual(journal["credit_dept_code"], "")
        self.assertEqual(journal["credit_dept_name"], "")

    def test_invalid_second_row_blocks_all_items(self):
        self.settlement["rows"] = [
            self.row(摘要="valid"),
            self.row(貸方科目="不存在"),
        ]

        with self.assertRaises(ReceivableRegistrationHandoffValidationError):
            self.build()

    def test_malformed_row_fails_with_dedicated_error(self):
        self.settlement["rows"] = [{"借方科目": "普通預金"}]

        with self.assertRaisesRegex(
            ReceivableRegistrationHandoffValidationError,
            "is missing fields",
        ):
            self.build()

    def test_epson_capability_needs_template(self):
        self.assertEqual(
            self.build()[0]["epson_capability"],
            {"status": "needs_template"},
        )

    def test_output_has_print_metadata_and_warning(self):
        item = self.build()[0]
        self.assertEqual(item["print_metadata"], {"print_category": ""})
        self.assertEqual(item["print_warnings"], ["伝票摘要なし"])

    def test_output_has_no_epson_rows_or_registration_id(self):
        item = self.build()[0]
        self.assertNotIn("epson_base_row", item)
        self.assertNotIn("epson_preview_row", item)
        self.assertNotIn("registration_id", item)

    def test_converter_performs_no_filesystem_io(self):
        with patch.object(
            builtins, "open", side_effect=AssertionError("unexpected open")
        ), patch.object(
            Path, "open", side_effect=AssertionError("unexpected Path.open")
        ):
            items = self.build()

        self.assertEqual(len(items), 1)


if __name__ == "__main__":
    unittest.main()

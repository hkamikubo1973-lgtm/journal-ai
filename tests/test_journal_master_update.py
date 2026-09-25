import csv
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from columns import EPSON_COLUMNS
import journal_master_update_service as service
from journal_master_update_service import SCHEMAS, MasterUpdateError, add_account, update_masters
from api.journal import app
from api.journal_master_update import get_master_directory
from fastapi.testclient import TestClient
from receivable_options_service import build_receipt_account_options


class MasterUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for kind in SCHEMAS:
            self.write(kind, [])
        self.transactions([])

    def write(self, kind, rows):
        name, fields = SCHEMAS[kind]
        with (self.root / name).open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader(); writer.writerows(rows)

    def read(self, kind):
        with (self.root / SCHEMAS[kind][0]).open(encoding="utf-8-sig", newline="") as file:
            return list(csv.DictReader(file))

    def transactions(self, rows):
        with (self.root / "transactions.csv").open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=EPSON_COLUMNS)
            writer.writeheader(); writer.writerows(rows)

    def snapshot(self):
        return {p.name: p.read_bytes() for p in self.root.iterdir()}

    def update(self, kind="account", execute=True):
        return update_masters(self.root, kind, execute=execute)

    def test_account_initial_both_sides_trim_and_blank_filter(self):
        self.transactions([{"借方科目": " 413 ", "借方科目名": " 法定福利費 ", "貸方科目": "504", "貸方科目名": "法定福利費"}, {"借方科目": "999"}])
        r = self.update()
        self.assertEqual(r["added_count"], 2)
        self.assertEqual(self.read("account"), [{"code": "413", "name": "法定福利費", "category": ""}, {"code": "504", "name": "法定福利費", "category": ""}])

    def test_account_incremental_category_manual_and_order_preserved(self):
        before = [{"code": "999", "name": "手動", "category": "資産"}, {"code": "413", "name": "法定福利費", "category": "費用"}]
        self.write("account", before)
        self.transactions([{"借方科目": "413", "借方科目名": "法定福利費", "貸方科目": "504", "貸方科目名": "法定福利費"}])
        self.assertEqual(self.update()["added_count"], 1)
        self.assertEqual(self.read("account")[:2], before)

    def test_account_noop_bytes(self):
        self.transactions([{"借方科目": "1", "借方科目名": "現金"}])
        self.update(); before = self.snapshot()
        self.assertEqual(self.update()["unchanged_count"], 1)
        self.assertEqual(before, self.snapshot())

    def test_account_code_conflict_blocks_all(self):
        self.write("account", [{"code": "1", "name": "現金", "category": "資産"}])
        self.transactions([{"借方科目": "1", "借方科目名": "別名", "貸方科目": "2", "貸方科目名": "新規"}])
        before = self.snapshot()
        self.assertEqual(self.update()["conflict_count"], 1)
        self.assertEqual(before, self.snapshot())

    def test_incoming_account_code_conflict(self):
        self.transactions([{"借方科目": "1", "借方科目名": "A", "貸方科目": "1", "貸方科目名": "B"}])
        self.assertFalse(self.update()["applied"])
        self.assertEqual(self.read("account"), [])

    def test_department_initial_same_name_different_code(self):
        self.transactions([{"借方部門": "10", "借方部門名": "部門", "貸方部門": "20", "貸方部門名": "部門"}])
        self.assertEqual(self.update("department")["added_count"], 2)

    def test_department_delta_retains_existing(self):
        self.write("department", [{"code": "90", "name": "手動"}])
        self.transactions([{"借方部門": "10", "借方部門名": "営業"}])
        self.update("department")
        self.assertEqual(self.read("department"), [{"code": "90", "name": "手動"}, {"code": "10", "name": "営業"}])

    def test_department_conflict(self):
        self.write("department", [{"code": "10", "name": "営業"}])
        self.transactions([{"借方部門": "10", "借方部門名": "別名"}])
        before = self.snapshot()
        self.assertEqual(self.update("department")["conflict_count"], 1)
        self.assertEqual(before, self.snapshot())

    def sub_fixture(self):
        self.write("account", [{"code": "114", "name": "預金", "category": "資産"}, {"code": "208", "name": "預り金", "category": "負債"}])
        self.transactions([{"借方科目": "114", "借方補助": "1", "借方補助科目名": "銀行", "貸方科目": "208", "貸方補助": "1", "貸方補助科目名": "社会保険料"}])

    def test_sub_initial_same_code_different_parents(self):
        self.sub_fixture()
        result = self.update("sub")
        self.assertEqual((result["sub_added_count"], result["relation_added_count"]), (2, 2))
        self.assertEqual(len(self.read("sub")), 2)
        self.assertEqual(len(self.read("relation")), 2)

    def test_sub_delta_retains_manual_relations(self):
        self.sub_fixture()
        old = {"account_code": "114", "sub_code": "9", "sub_name": "手動"}
        self.write("sub", [{"code": "9", "name": "手動"}]); self.write("relation", [old])
        self.update("sub")
        self.assertEqual(self.read("relation")[0], old)
        self.assertEqual(self.read("sub")[0]["name"], "手動")

    def test_relation_only_addition(self):
        self.sub_fixture()
        self.write("sub", [{"code": "1", "name": "銀行"}, {"code": "1", "name": "社会保険料"}])
        result = self.update("sub")
        self.assertEqual((result["sub_added_count"], result["relation_added_count"]), (0, 2))

    def test_missing_sub_for_existing_relation_added(self):
        self.sub_fixture()
        self.write("relation", [{"account_code": "114", "sub_code": "1", "sub_name": "銀行"}])
        result = self.update("sub")
        self.assertEqual((result["sub_added_count"], result["relation_added_count"]), (2, 1))

    def test_no_orphans_created(self):
        self.sub_fixture(); self.update("sub")
        pairs = {(r["code"], r["name"]) for r in self.read("sub")}
        self.assertTrue(all((r["sub_code"], r["sub_name"]) in pairs for r in self.read("relation")))

    def test_missing_parent_blocks_without_writes(self):
        self.sub_fixture(); self.write("account", [])
        before = self.snapshot()
        self.assertEqual(self.update("sub")["conflict_count"], 2)
        self.assertEqual(before, self.snapshot())

    def test_same_parent_sub_code_different_name_conflict(self):
        self.sub_fixture()
        self.write("relation", [{"account_code": "114", "sub_code": "1", "sub_name": "旧名"}])
        before = self.snapshot()
        self.assertFalse(self.update("sub")["applied"])
        self.assertEqual(before, self.snapshot())

    def fail_second_write(self, action, after_replace=False):
        before = self.snapshot(); original = service.atomic_write_bytes; calls = []
        def failing(path, content):
            calls.append(path)
            if len(calls) == 2:
                if after_replace: original(path, content)
                raise OSError("injected failure")
            return original(path, content)
        with patch.object(service, "atomic_write_bytes", side_effect=failing):
            with self.assertRaises(OSError): action()
        self.assertEqual(before, self.snapshot())

    def test_sub_partial_write_rollback(self):
        self.sub_fixture(); self.fail_second_write(lambda: self.update("sub"))

    def test_sub_post_replace_failure_rollback(self):
        self.sub_fixture(); self.fail_second_write(lambda: self.update("sub"), True)

    def test_new_account_category(self):
        result = add_account(self.root, "123", "新科目", "資産")
        self.assertTrue(result["account_added"])
        self.assertEqual(self.read("account")[0]["category"], "資産")

    def test_manual_existing_noop_keeps_category(self):
        add_account(self.root, "123", "新科目", "資産")
        before = self.snapshot()
        self.assertFalse(add_account(self.root, "123", "新科目", "費用")["account_added"])
        self.assertEqual(before, self.snapshot())

    def test_manual_same_code_other_name_rejected(self):
        add_account(self.root, "123", "新科目", "資産")
        before = self.snapshot()
        with self.assertRaises(MasterUpdateError): add_account(self.root, "123", "別名", "資産")
        self.assertEqual(before, self.snapshot())

    def test_manual_same_name_different_code_and_second_existing(self):
        add_account(self.root, "413", "法定福利費", "費用")
        self.assertTrue(add_account(self.root, "504", "法定福利費", "費用")["account_added"])
        self.assertFalse(add_account(self.root, "504", "法定福利費", "資産")["account_added"])

    def test_payment_add(self):
        r = add_account(self.root, "123", "新科目", "資産", add_to_payment=True)
        self.assertTrue(r["payment_added"])
        self.assertEqual(self.read("payment"), [{"科目": "新科目"}])

    def test_payment_checkbox_off_untouched(self):
        before = (self.root / "payment_accounts.csv").read_bytes()
        add_account(self.root, "123", "新科目", "資産")
        self.assertEqual(before, (self.root / "payment_accounts.csv").read_bytes())

    def test_ambiguous_payment_blocks_account_too(self):
        add_account(self.root, "413", "法定福利費", "費用")
        before = self.snapshot()
        with self.assertRaises(MasterUpdateError): add_account(self.root, "504", "法定福利費", "費用", add_to_payment=True)
        self.assertEqual(before, self.snapshot())

    def test_existing_ambiguous_payment_rejected(self):
        for code in ("413", "504"): add_account(self.root, code, "法定福利費", "費用")
        before = self.snapshot()
        with self.assertRaises(MasterUpdateError): add_account(self.root, "504", "法定福利費", "費用", add_to_payment=True)
        self.assertEqual(before, self.snapshot())

    def test_account_payment_partial_rollback(self):
        self.fail_second_write(lambda: add_account(self.root, "123", "新科目", "資産", add_to_payment=True))

    def test_account_payment_post_replace_rollback(self):
        self.fail_second_write(lambda: add_account(self.root, "123", "新科目", "資産", add_to_payment=True), True)

    def test_preview_no_writes_all_kinds(self):
        self.sub_fixture(); before = self.snapshot()
        for kind in ("account", "department", "sub"): self.update(kind, False)
        self.assertEqual(before, self.snapshot())

    def test_execute_recalculates_database_and_master(self):
        self.transactions([{"借方科目": "1", "借方科目名": "A"}])
        self.assertEqual(self.update(execute=False)["added_count"], 1)
        add_account(self.root, "1", "A", "資産")
        self.transactions([{"借方科目": "1", "借方科目名": "A", "貸方科目": "2", "貸方科目名": "B"}])
        result = self.update()
        self.assertEqual(result["added_items"], [{"code": "2", "name": "B", "category": ""}])
        self.assertEqual(self.read("account")[0]["category"], "資産")

    def test_conflict_added_after_preview_blocks_execute(self):
        self.transactions([{"借方科目": "1", "借方科目名": "A"}])
        self.assertEqual(self.update(execute=False)["conflict_count"], 0)
        add_account(self.root, "1", "B", "資産")
        before = self.snapshot()
        self.assertFalse(self.update()["applied"])
        self.assertEqual(before, self.snapshot())

    def test_rollback_removes_new_files_if_pair_write_fails(self):
        self.sub_fixture()
        (self.root / "sub_master.csv").unlink()
        (self.root / "sub_account_relations.csv").unlink()
        self.fail_second_write(lambda: self.update("sub"), True)

    def test_extra_columns_and_existing_spacing_preserved(self):
        (self.root / "account_master.csv").write_text("code,name,category,note\n999, 手動 ,資産,keep\n", encoding="utf-8-sig")
        self.transactions([{"借方科目": "1", "借方科目名": "A"}])
        self.update()
        self.assertEqual(self.read("account")[0], {"code": "999", "name": " 手動 ", "category": "資産", "note": "keep"})

    def test_transactions_unchanged(self):
        self.sub_fixture(); before = (self.root / "transactions.csv").read_bytes()
        for kind in ("account", "department", "sub"): self.update(kind)
        add_account(self.root, "999", "手動", "費用", add_to_payment=True)
        self.assertEqual(before, (self.root / "transactions.csv").read_bytes())

    def test_existing_invalid_payment_retained_and_diagnosed(self):
        self.write("payment", [{"科目": "電子記録債権"}])
        add_account(self.root, "123", "現金", "資産", add_to_payment=True)
        result = build_receipt_account_options([r["科目"] for r in self.read("payment")], {"accounts": self.read("account")})
        self.assertEqual(result["invalid_receipt_account_names"], ["電子記録債権"])

    def test_missing_master_initial_creation(self):
        (self.root / "account_master.csv").unlink()
        self.transactions([{"借方科目": "1", "借方科目名": "A"}])
        self.assertEqual(self.update()["added_count"], 1)

    def test_empty_file_initial_creation(self):
        (self.root / "account_master.csv").write_bytes(b"")
        self.transactions([{"借方科目": "1", "借方科目名": "A"}])
        self.assertEqual(self.update()["added_count"], 1)

    def test_api_preview_execute_manual_and_untrusted_fields(self):
        app.dependency_overrides[get_master_directory] = lambda: self.root
        self.addCleanup(app.dependency_overrides.pop, get_master_directory)
        client = TestClient(app)
        self.transactions([{"借方科目": "1", "借方科目名": "A"}])
        before = self.snapshot()
        response = client.post("/api/journal/masters/update-preview", json={"kind": "account"})
        self.assertEqual(response.json()["added_count"], 1)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(client.post("/api/journal/masters/update", json={"kind": "account", "added_items": []}).status_code, 422)
        self.assertTrue(client.post("/api/journal/masters/update", json={"kind": "account"}).json()["applied"])
        self.assertTrue(client.post("/api/journal/masters/accounts/add", json={"code": "2", "name": "A", "category": "費用"}).json()["account_added"])
        response = client.post("/api/journal/masters/accounts/add", json={"code": "2", "name": "A", "category": "費用", "add_to_payment": True})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.read("payment"), [])


if __name__ == "__main__":
    unittest.main()

import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from columns import EPSON_COLUMNS
from past_journal_import_service import import_past_journal_csv, journal_import_key
from api.journal import app
from api.journal_import import get_transactions_path


def row(summary="既存", date="20260925", **values):
    result = dict.fromkeys(EPSON_COLUMNS, "")
    result.update({"伝票日付": date, "借方科目": "001", "借方科目名": "普通預金",
                   "貸方科目": "131", "貸方科目名": "未収運賃", "借方金額": "100", "摘要": summary})
    result.update(values)
    return result


def content(rows, columns=EPSON_COLUMNS, encoding="utf-8-sig"):
    return pd.DataFrame(rows, columns=columns).to_csv(index=False).encode(encoding)


class PastJournalImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "transactions.csv"
        self.path.write_bytes(content([row()]))

    def call(self, rows, execute=False, **kwargs):
        return import_past_journal_csv(content(rows, **kwargs), self.path, execute=execute)

    def test_utf8(self):
        self.assertEqual(self.call([row("新規")])["detected_encoding"], "utf-8-sig")

    def test_cp932(self):
        result = self.call([row("新規")], encoding="cp932")
        self.assertEqual(result["detected_encoding"], "cp932")
        self.assertEqual(result["preview_rows"][0]["摘要"], "新規")

    def test_wrong_column_count(self):
        self.assertIn("45列CSVではありません", self.call([row()], columns=EPSON_COLUMNS[:-1])["errors"][0])

    def test_wrong_column_name(self):
        columns = EPSON_COLUMNS[:-1] + ["別名"]
        self.assertTrue(self.call([row()], columns=columns)["errors"])

    def test_wrong_column_order(self):
        self.assertTrue(self.call([row()], columns=list(reversed(EPSON_COLUMNS)))["errors"])

    def test_header_trim(self):
        upload = content([row("新規")]).decode("utf-8-sig").splitlines()
        upload[0] = ",".join(" " + name + " " for name in EPSON_COLUMNS)
        result = import_past_journal_csv("\n".join(upload).encode("utf-8-sig"), self.path)
        self.assertEqual(result["new_count"], 1)

    def test_blank_rows_filtered_but_preview_keeps_upload(self):
        blank = dict.fromkeys(EPSON_COLUMNS, " ")
        result = self.call([blank, row("新規")])
        self.assertEqual(result["uploaded_count"], 2)
        self.assertEqual(result["new_count"], 1)
        self.assertEqual(len(result["preview_rows"]), 2)

    def test_existing_duplicate(self):
        result = self.call([row()])
        self.assertEqual((result["new_count"], result["duplicate_count"]), (0, 1))

    def test_upload_duplicate_first_wins(self):
        self.call([row("新規", 貸方補助="first"), row("新規", 貸方補助="second")], execute=True)
        saved = pd.read_csv(self.path, dtype=str).fillna("")
        self.assertEqual(saved.iloc[-1]["貸方補助"], "first")
        self.assertEqual(len(saved), 2)

    def test_duplicate_normalization_only_key(self):
        a = row(" A  B ", "2026/09/25", 借方金額="1,000")
        b = row("A B", "2026-09-25", 借方金額="1000", 借方部門="different")
        self.assertEqual(journal_import_key(a), journal_import_key(b))
        self.call([a, b], execute=True)
        saved = pd.read_csv(self.path, dtype=str).fillna("")
        self.assertEqual(saved.iloc[-1]["摘要"], " A  B ")
        self.assertEqual(saved.iloc[-1]["借方金額"], "1,000")
        self.assertEqual(saved.iloc[-1]["伝票日付"], "2026/09/25")

    def test_credit_amount_fallback(self):
        self.assertEqual(journal_import_key(row(借方金額="", 貸方金額="100")), journal_import_key(row()))
        self.assertEqual(journal_import_key(row(貸方金額="900")), journal_import_key(row()))

    def test_preview_does_not_write(self):
        before = self.path.read_bytes()
        self.call([row("新規")])
        self.assertEqual(before, self.path.read_bytes())

    def test_execute_rechecks_current_database(self):
        upload = [row("競合"), row("追加")]
        self.assertEqual(self.call(upload)["new_count"], 2)
        self.path.write_bytes(content([row("競合"), row()]))
        result = self.call(upload, execute=True)
        self.assertEqual((result["imported_count"], result["duplicate_count"]), (1, 1))
        self.assertEqual(pd.read_csv(self.path)["摘要"].tolist(), ["競合", "既存", "追加"])

    def test_tail_append_preserves_four_year_order_and_values_without_sort(self):
        existing = [row("当期", "20260101"), row("前期", "20251231"), row("2期前", "20241231"), row("3期前", "20231231")]
        existing[2]["借方科目名"] = "  元の名称  "
        uploaded = [row("upload1", "20221231"), row("upload2", "20251111")]
        self.path.write_bytes(content(existing))
        self.call(uploaded, execute=True)
        saved = pd.read_csv(self.path, dtype=str).fillna("").to_dict("records")
        self.assertEqual(saved, existing + uploaded)

    def test_bom_and_header(self):
        self.call([row("新規")], execute=True)
        output = self.path.read_bytes()
        self.assertTrue(output.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(output.decode("utf-8-sig").splitlines()[0], ",".join(EPSON_COLUMNS))

    def test_no_new_rows_no_rewrite(self):
        before = self.path.read_bytes()
        self.assertEqual(self.call([row()], execute=True)["imported_count"], 0)
        self.assertEqual(before, self.path.read_bytes())

    def test_no_new_business_validation(self):
        unusual = row("不正日付も旧仕様では取込", "not-a-date", 借方科目="unknown", 貸方金額="999", 借方消費税コード="bad")
        self.assertEqual(self.call([unusual], execute=True)["imported_count"], 1)

    def test_master_files_untouched(self):
        names = ["account_master.csv", "department_master.csv", "sub_master.csv", "sub_account_relations.csv", "payment_accounts.csv"]
        for name in names:
            (self.path.parent / name).write_bytes(b"unchanged\r\n")
        before = {p.name: p.read_bytes() for p in self.path.parent.iterdir() if p != self.path}
        self.call([row("新規")], execute=True)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.path.parent.iterdir() if p != self.path})

    def test_preview_limit_and_columns(self):
        result = self.call([row(str(i)) for i in range(55)])
        self.assertEqual(result["new_count"], 55)
        self.assertEqual(len(result["preview_rows"]), 50)
        self.assertEqual(list(result["preview_rows"][0]), ["伝票日付", "借方科目", "借方科目名", "貸方科目", "貸方科目名", "借方金額", "摘要"])

    def test_api_preview_execute_and_missing_db(self):
        app.dependency_overrides[get_transactions_path] = lambda: self.path
        self.addCleanup(app.dependency_overrides.pop, get_transactions_path)
        client = TestClient(app)
        upload = {"file": ("past.csv", content([row("新規")]), "text/csv")}
        before = self.path.read_bytes()
        response = client.post("/api/journal/import/preview", files=upload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["new_count"], 1)
        self.assertEqual(self.path.read_bytes(), before)
        response = client.post("/api/journal/import/execute", files=upload)
        self.assertEqual(response.json()["imported_count"], 1)
        self.path.unlink()
        for action in ("preview", "execute"):
            response = client.post("/api/journal/import/" + action, files=upload)
            self.assertEqual(response.status_code, 400)
            self.assertNotIn(str(self.path), response.text)
            self.assertFalse(self.path.exists())

    def test_invalid_upload_never_writes(self):
        before = self.path.read_bytes()
        result = import_past_journal_csv(b"", self.path, execute=True)
        self.assertTrue(result["errors"])
        self.assertEqual(before, self.path.read_bytes())


if __name__ == "__main__":
    unittest.main()

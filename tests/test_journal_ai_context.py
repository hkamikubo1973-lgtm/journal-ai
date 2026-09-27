"""Journal AI context stays a read-only projection of the formal Web search."""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import engine  # noqa: E402
import journal_ai_context_provider as provider  # noqa: E402
from api.journal import app  # noqa: E402
from columns import EPSON_COLUMNS  # noqa: E402
from journal_search_service import search_journals  # noqa: E402


FIXED_NOW = datetime(2026, 9, 28, 10, 30, tzinfo=timezone(timedelta(hours=9)))
METADATA_MARKERS = ("SECRET_USER", "SECRET_COMPANY", "SECRET_TAX")


def journal_row(*, date="2026/09/28", debit_code="114", debit="普通預金",
                credit_code="600", credit="売上", debit_amount="1200",
                credit_amount="1200", summary="月次処理", department="営業部"):
    row = dict.fromkeys(EPSON_COLUMNS, "")
    row.update({
        "伝票日付": date, "借方科目": debit_code, "借方科目名": debit,
        "借方補助": "01", "借方補助科目名": "補助A",
        "借方部門": "10", "借方部門名": department,
        "貸方科目": credit_code, "貸方科目名": credit,
        "貸方補助": "02", "貸方補助科目名": "補助B",
        "貸方部門": "20", "貸方部門名": "経理部",
        "借方金額": debit_amount, "貸方金額": credit_amount,
        "摘要": summary, "伝票摘要": "伝票説明",
        "入力ユーザ": METADATA_MARKERS[0],
        "入力会社": METADATA_MARKERS[1],
        "借方消費税税率": METADATA_MARKERS[2],
    })
    return row


def draft():
    return {
        "voucherDate": "2026/09/29", "voucherNo": "V-1",
        "voucherSummary": "編集した伝票", "debitAccountCode": "114",
        "debitAccountName": "普通預金", "debitSubCode": "01",
        "debitSubName": "補助A", "debitDeptCode": "10",
        "debitDeptName": "営業部", "creditAccountCode": "600",
        "creditAccountName": "売上", "creditSubCode": "02",
        "creditSubName": "補助B", "creditDeptCode": "20",
        "creditDeptName": "経理部", "amount": "9999",
        "debitAmount": "9999", "creditAmount": "9999", "summary": "利用者が編集",
    }


class JournalAiContextTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.csv_path = self.root / "transactions.csv"
        rows = [journal_row()]
        rows.extend([
            journal_row(debit_code="310", credit_code="", credit="",
                        debit_amount="300", credit_amount="0", summary="複数行資料"),
            journal_row(debit_code="", debit="", credit_code="601", credit="売上A",
                        debit_amount="0", credit_amount="100", summary="複数行資料"),
            journal_row(debit_code="", debit="", credit_code="602", credit="売上B",
                        debit_amount="0", credit_amount="200", summary="複数行資料"),
        ])
        rows.extend(
            journal_row(debit_code=str(1000 + index), credit_code="700",
                        summary="共通検索", debit_amount=str(1000 + index),
                        credit_amount=str(1000 + index))
            for index in range(25)
        )
        with self.csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=EPSON_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        path_patch = patch.object(engine, "DATA_PATH", str(self.csv_path))
        path_patch.start()
        self.addCleanup(path_patch.stop)
        self.client = TestClient(app)

    def context(self, **changes):
        params = {"keyword": "月次処理", "department": None,
                  "amount": None, "limit": 5, "now": FIXED_NOW}
        params.update(changes)
        return provider.build_journal_ai_context(**params)

    def test_keyword_only_matches_formal_search_without_changing_rank_or_score(self):
        records, _, frequency = engine.load_data()
        expected = search_journals(records, frequency, keyword="月次処理")
        data = self.context()["data"]
        self.assertEqual(data["query"], expected["query"])
        self.assertEqual(data["candidate_count"], expected["count"])
        self.assertEqual(
            [(item["rank"], item["score"], item["search_reason"])
             for item in data["candidates"]],
            [(item["rank"], item["score"], item["search_reason"])
             for item in expected["candidates"]],
        )

    def test_department_and_amount_reach_existing_search_unchanged(self):
        records, _, frequency = engine.load_data()
        for changes in ({"department": "営業部"}, {"amount": 1200},
                        {"department": "営業部", "amount": 1200}):
            with self.subTest(changes=changes):
                expected = search_journals(records, frequency, keyword="月次処理", **changes)
                actual = self.context(**changes)["data"]
                self.assertEqual(actual["query"], expected["query"])
                self.assertEqual(
                    [(item["rank"], item["score"]) for item in actual["candidates"]],
                    [(item["rank"], item["score"]) for item in expected["candidates"]],
                )

    def test_limit_5_10_and_20_preserve_search_order(self):
        records, _, frequency = engine.load_data()
        for limit in (5, 10, 20):
            with self.subTest(limit=limit):
                expected = search_journals(records, frequency, keyword="共通検索", limit=limit)
                actual = self.context(keyword="共通検索", limit=limit)["data"]
                self.assertEqual(actual["candidate_count"], limit)
                self.assertEqual(
                    [item["rows"][0]["debit_account_code"] for item in actual["candidates"]],
                    [item["source_rows"][0]["借方科目"] for item in expected["candidates"]],
                )

    def test_single_row_projection_has_all_required_journal_fields(self):
        candidate = self.context()["data"]["candidates"][0]
        self.assertEqual(candidate["row_count"], 1)
        self.assertFalse(candidate["is_multi_line"])
        row = candidate["rows"][0]
        self.assertEqual((row["date"], row["amount"], row["summary"], row["voucher_summary"]),
                         ("2026/09/28", "1200", "月次処理", "伝票説明"))
        for side in ("debit", "credit"):
            for field in ("account_code", "account_name", "sub_code", "sub_name",
                          "department_code", "department_name", "amount"):
                self.assertIn(f"{side}_{field}", row)

    def test_multi_line_voucher_keeps_each_source_row_in_order(self):
        candidate = self.context(keyword="複数行資料")["data"]["candidates"][0]
        self.assertEqual(candidate["row_count"], 3)
        self.assertTrue(candidate["is_multi_line"])
        self.assertTrue(candidate["is_complex"])
        self.assertEqual([row["amount"] for row in candidate["rows"]], ["300", "100", "200"])
        self.assertEqual([row["credit_account_code"] for row in candidate["rows"]],
                         ["", "601", "602"])

    def test_matched_amount_is_compact_and_not_a_raw_row(self):
        candidate = self.context(amount=1200)["data"]["candidates"][0]
        matched = candidate["matched_amount"]
        self.assertIsNotNone(matched)
        self.assertEqual(matched["amount"], 1200)
        self.assertEqual(matched["debit_account_code"], "114")
        self.assertNotIn("入力ユーザ", matched)

    def test_projection_omits_raw_rows_cart_and_metadata(self):
        data = self.context()["data"]
        encoded = json.dumps(data, ensure_ascii=False)
        for marker in METADATA_MARKERS:
            self.assertNotIn(marker, encoded)
        for field in ("source_rows", "editable_rows", "block_rows", "registrationCart",
                      "pattern_key", "pattern_rank", "入力ユーザ", "入力会社"):
            self.assertNotIn(field, encoded)
        self.assertEqual(set(data), {"query", "current_draft", "candidate_count", "candidates"})

    def test_draft_absent_is_null(self):
        self.assertIsNone(self.context()["data"]["current_draft"])

    def test_draft_is_labeled_unregistered_and_does_not_change_search(self):
        without = self.context()["data"]
        with_draft = self.context(draft=draft())["data"]
        self.assertEqual(with_draft["candidates"], without["candidates"])
        self.assertEqual(with_draft["query"], without["query"])
        self.assertEqual(with_draft["current_draft"]["source"], "frontend_unregistered")
        self.assertEqual(with_draft["current_draft"]["amount"], "9999")
        self.assertEqual(with_draft["current_draft"]["summary"], "利用者が編集")
        self.assertNotIn("source_rows", with_draft["current_draft"])

    def test_envelope_uses_common_schema_and_one_server_timestamp(self):
        context = self.context()
        self.assertEqual(set(context), {"schema_version", "source", "generated_at", "as_of", "data"})
        self.assertEqual((context["schema_version"], context["source"]), (1, "journal"))
        self.assertEqual((context["generated_at"], context["as_of"]),
                         (FIXED_NOW.isoformat(), "2026-09-28"))

    def test_zero_results_are_an_empty_context(self):
        data = self.context(keyword="該当しない固有語")["data"]
        self.assertEqual(data["candidate_count"], 0)
        self.assertEqual(data["candidates"], [])

    def test_api_researches_backend_without_accepting_candidate_list(self):
        response = self.client.post("/api/journal/ai-context", json={"keyword": "月次処理"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["source"], "journal")
        self.assertEqual(response.json()["data"]["candidate_count"], 1)
        rejected = self.client.post("/api/journal/ai-context", json={
            "keyword": "月次処理", "candidates": [{"score": 999999}],
        })
        self.assertEqual(rejected.status_code, 422)

    def test_api_accepts_frontend_edit_form_and_keeps_it_unregistered(self):
        response = self.client.post("/api/journal/ai-context", json={
            "keyword": "月次処理", "draft": draft(),
        })
        self.assertEqual(response.status_code, 200, response.text)
        current = response.json()["data"]["current_draft"]
        self.assertEqual(current["source"], "frontend_unregistered")
        self.assertEqual(current["voucher_date"], "2026/09/29")
        self.assertEqual(current["credit_department_name"], "経理部")

    def test_api_rejects_invalid_limit_and_draft_without_changing_csv(self):
        before = self.csv_path.read_bytes()
        for body in ({"keyword": "月次処理", "limit": 7},
                     {"keyword": "月次処理", "draft": {"summary": "部分入力"}},
                     {"keyword": ""}, {}):
            with self.subTest(body=body):
                response = self.client.post("/api/journal/ai-context", json=body)
                self.assertEqual(response.status_code, 422)
                self.assertNotIn(str(self.csv_path), response.text)
        self.assertEqual(self.csv_path.read_bytes(), before)

    def test_backend_read_failure_has_safe_error(self):
        with patch.object(provider, "load_data", side_effect=OSError("private/data/path")):
            response = self.client.post("/api/journal/ai-context", json={"keyword": "月次処理"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "仕訳のContextを生成できませんでした")
        self.assertNotIn("private/data/path", response.text)

    def test_context_is_read_only_for_db_and_other_business_files(self):
        protected = [self.csv_path]
        for name in ("account_master.csv", "sub_master.csv", "department_master.csv",
                     "receivables/current.csv", "receivables/receivable_history.csv",
                     "events.csv", "config/settings.json"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"protected:{name}".encode())
            protected.append(path)
        before = {path: path.read_bytes() for path in protected}
        names = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        response = self.client.post("/api/journal/ai-context", json={"keyword": "月次処理"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual({path: path.read_bytes() for path in protected}, before)
        self.assertEqual(sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*")), names)


if __name__ == "__main__":
    unittest.main()

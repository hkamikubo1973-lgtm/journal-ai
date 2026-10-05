"""Optional company facts must not alter journal search or expose the CSV."""

from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import company_knowledge_provider as knowledge  # noqa: E402


def entry(**overrides):
    item = {
        "namespace": "internal_code", "match_field": "summary", "key": "17",
        "label": "仮の名称", "description": "テスト用の説明",
        "source": "テスト資料", "active": "true",
    }
    item.update(overrides)
    return item


def journal_context(*rows):
    return {"data": {"candidates": [
        {"rank": 3, "score": 81, "search_reason": ["fixture"], "rows": list(rows)},
    ]}}


class CompanyKnowledgeProviderTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "company_knowledge" / "entries.csv"

    def write(self, rows, *, columns=knowledge.KNOWLEDGE_COLUMNS):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

    def build(self, *rows):
        return knowledge.build_company_knowledge_context(
            journal_context(*rows), path=self.path,
        )

    def test_missing_directory_and_file_are_read_only_empty_contexts(self):
        context = self.build({"summary": "17"})
        self.assertEqual(context["source"], "company_knowledge")
        self.assertEqual(context["schema_version"], 1)
        self.assertEqual(context["data"], {"matches": [], "truncated": False})
        self.assertFalse(self.path.parent.exists())
        self.path.parent.mkdir()
        self.assertEqual(self.build({"summary": "17"})["data"]["matches"], [])
        self.assertFalse(self.path.exists())

    def test_empty_csv_and_header_only_are_valid(self):
        self.path.parent.mkdir()
        self.path.write_bytes(b"")
        self.assertEqual(self.build({"summary": "17"})["data"]["matches"], [])
        self.write([])
        before = self.path.read_bytes()
        self.assertEqual(self.build({"summary": "17"})["data"]["matches"], [])
        self.assertEqual(self.path.read_bytes(), before)

    def test_active_match_is_candidate_scoped_and_keeps_rank_and_source_private(self):
        self.write([
            entry(), entry(key="88", label="候補外の秘密"),
            entry(key="19", label="停止項目", active="false"),
        ])
        source = journal_context({"summary": "架空銀行 17"})
        before = repr(source)
        result = knowledge.build_company_knowledge_context(source, path=self.path)
        self.assertEqual(source["data"]["candidates"][0]["rank"], 3)
        self.assertEqual(source["data"]["candidates"][0]["score"], 81)
        self.assertEqual(repr(source), before)
        self.assertEqual(len(result["data"]["matches"]), 1)
        matched = result["data"]["matches"][0]
        self.assertEqual((matched["candidate_rank"], matched["row_number"]), (3, 1))
        self.assertEqual(matched["key"], "17")
        self.assertEqual(set(matched), {
            "candidate_rank", "row_number", "namespace", "match_field",
            "key", "label", "description", "source",
        })
        self.assertNotIn(str(self.path), repr(result))
        self.assertNotIn("候補外の秘密", repr(result))

    def test_structured_fields_use_normalized_exact_match(self):
        self.write([entry(match_field="debit_sub_name", key="ＡＢＣ", label="補助説明")])
        self.assertEqual(len(self.build({"debit_sub_name": "abc"})["data"]["matches"]), 1)
        self.assertEqual(self.build({"debit_sub_name": "abc支店"})["data"]["matches"], [])

    def test_summary_code_boundaries(self):
        self.write([entry()])
        for text in ("架空銀行 17", "架空銀行１７", "架空銀行(17)"):
            with self.subTest(text=text):
                self.assertEqual(len(self.build({"summary": text})["data"]["matches"]), 1)
        for text in ("117", "170", "A17B", "17A", "B17"):
            with self.subTest(text=text):
                self.assertEqual(self.build({"summary": text})["data"]["matches"], [])

    def test_voucher_summary_and_multi_row_order(self):
        self.write([entry(match_field="voucher_summary")])
        result = self.build(
            {"voucher_summary": "該当なし"},
            {"voucher_summary": "コード 17"},
        )
        self.assertEqual(result["data"]["matches"][0]["row_number"], 2)

    def test_identical_rows_dedupe_and_conflicting_rows_are_suppressed(self):
        same = entry()
        self.write([same, same.copy()])
        self.assertEqual(len(self.build({"summary": "17"})["data"]["matches"]), 1)
        self.write([same, entry(label="矛盾"), same.copy()])
        self.assertEqual(self.build({"summary": "17"})["data"]["matches"], [])

    def test_invalid_csv_is_unavailable_without_stopping_assist(self):
        self.write([entry()], columns=knowledge.KNOWLEDGE_COLUMNS)
        self.path.write_text("namespace,wrong_header\nsecret,bad\n", encoding="utf-8")
        with self.assertLogs(knowledge.__name__, level="WARNING") as logs:
            result = self.build({"summary": "17"})
        self.assertEqual(result["data"]["matches"], [])
        self.assertNotIn(str(self.path), repr(logs.output))
        self.write([entry(match_field="__class__")])
        with self.assertLogs(knowledge.__name__, level="WARNING"):
            self.assertEqual(self.build({"summary": "17"})["data"]["matches"], [])
        self.path.write_text(
            ",".join(knowledge.KNOWLEDGE_COLUMNS) + "\n\"unterminated",
            encoding="utf-8",
        )
        with self.assertLogs(knowledge.__name__, level="WARNING"):
            self.assertEqual(self.build({"summary": "17"})["data"]["matches"], [])

    def test_match_limit_is_explicit_and_does_not_change_candidates(self):
        self.write([entry()])
        source = journal_context(*[{"summary": "17"} for _ in range(4)])
        with patch.object(knowledge, "MAX_MATCHES", 2):
            result = knowledge.build_company_knowledge_context(source, path=self.path)
        self.assertEqual(len(result["data"]["matches"]), 2)
        self.assertTrue(result["data"]["truncated"])
        self.assertEqual(len(source["data"]["candidates"][0]["rows"]), 4)

    def test_context_character_limit_prevents_oversized_prompt(self):
        self.write([entry(description="x" * 400)])
        with patch.object(knowledge, "MAX_CONTEXT_CHARACTERS", 1000):
            result = self.build({"summary": "17"}, {"summary": "17"})
        self.assertEqual(len(result["data"]["matches"]), 1)
        self.assertTrue(result["data"]["truncated"])


if __name__ == "__main__":
    unittest.main()

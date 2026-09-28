"""Receivable AI context reads only current through the Web ledger gate."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import api.receivable as receivable_api  # noqa: E402
import receivable_ai_context_provider as provider  # noqa: E402
import receivable_persistence_service as persistence  # noqa: E402
from api.journal import app  # noqa: E402
from receivable_cleanup_service import (  # noqa: E402
    partition_receivable_cleanup_rows,
    summarize_receivable_cleanup_rows,
)
from receivable_engine import CURRENT_RECEIVABLE_COLUMNS  # noqa: E402
from receivable_query_service import build_receivable_summary  # noqa: E402


FIXED_NOW = datetime(2026, 9, 28, 10, 30, tzinfo=timezone(timedelta(hours=9)))


def row(code, customer, invoice, paid, balance, *, status="未処理",
        invoice_date="2026-09-01", due_date="2026-09-30"):
    values = dict.fromkeys(CURRENT_RECEIVABLE_COLUMNS, "")
    values.update({
        "コード": code, "未収ID": f"SECRET_ID_{code}", "得意先名": customer,
        "請求日": invoice_date, "入金予定日": due_date,
        "未収科目": "未収運賃", "未収補助": f"{customer}補助",
        "部門": "営業部", "摘要": f"{customer}請求",
        "請求金額": invoice, "入金済額": paid, "残高": balance,
        "ステータス": status,
    })
    return values


class ReceivableAiContextTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.current = self.root / "current.csv"
        self.history = self.root / "receivable_history.csv"
        self.history.write_bytes(b"SECRET_HISTORY")
        self.receipt = self.root / ".settlements" / "receipt.json"
        self.receipt.parent.mkdir()
        self.receipt.write_bytes(b"SECRET_RECEIPT")
        self.archive = self.root / "receivable_import_archive.csv"
        self.archive.write_bytes(b"SECRET_ARCHIVE")
        self.write_current([
            row("A", "得意先A", "1,000", "200", "800", due_date="2026-10-05"),
            row("B", "得意先B", "2,000", "500", "1,500", invoice_date="2026-09-03"),
            row("A", "得意先A", "3,000", "1,000", "2,000", invoice_date="2026-09-04"),
            row("C", "整理済み", "400", "300", "100", status=" 完了 "),
            row("D", "残高なし", "500", "500", "0"),
            row("E", "マイナス", "600", "700", "-100"),
        ])
        app.dependency_overrides[receivable_api.get_receivables_directory] = lambda: self.root
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)

    def write_current(self, rows):
        pd.DataFrame(rows, columns=CURRENT_RECEIVABLE_COLUMNS).to_csv(
            self.current, index=False, encoding="utf-8-sig",
        )

    def context(self):
        return provider.build_receivable_ai_context(self.root, now=FIXED_NOW)

    def test_common_envelope_has_receivable_source_and_fixed_time(self):
        result = self.context()
        self.assertEqual(set(result), {"schema_version", "source", "generated_at", "as_of", "data"})
        self.assertEqual((result["schema_version"], result["source"]), (1, "receivable"))
        self.assertEqual((result["generated_at"], result["as_of"]),
                         (FIXED_NOW.isoformat(), "2026-09-28"))

    def test_counts_and_totals_cover_current_without_mixing_cleanup_items(self):
        data = self.context()["data"]
        self.assertEqual((data["total_count"], data["outstanding_count"],
                          data["cleanup_target_count"]), (6, 3, 3))
        self.assertEqual((data["total_invoice_amount"], data["total_paid_amount"],
                          data["total_balance"]), (7500, 3200, 4300))
        self.assertEqual(len(data["items"]), 3)

    def test_cleanup_target_count_uses_existing_service(self):
        current = persistence.load_current_receivables_read_only(self.current).dataframe
        expected = summarize_receivable_cleanup_rows(current)
        self.assertEqual(self.context()["data"]["cleanup_target_count"],
                         expected["cleanup_target_count"])
        retained, _ = partition_receivable_cleanup_rows(current)
        self.assertEqual(self.context()["data"]["outstanding_count"],
                         build_receivable_summary(retained)["outstanding_count"])

    def test_completed_zero_and_negative_rows_are_not_outstanding(self):
        names = [item["customer_name"] for item in self.context()["data"]["items"]]
        self.assertEqual(names, ["得意先A", "得意先B", "得意先A"])
        self.assertNotIn("整理済み", names)
        self.assertNotIn("残高なし", names)
        self.assertNotIn("マイナス", names)

    def test_items_preserve_current_order_and_project_business_fields(self):
        items = self.context()["data"]["items"]
        self.assertEqual([item["customer_code"] for item in items], ["A", "B", "A"])
        first = items[0]
        self.assertEqual((first["invoice_date"], first["payment_due_date"]),
                         ("2026-09-01", "2026-10-05"))
        self.assertEqual((first["invoice_amount"], first["paid_amount"], first["balance"]),
                         (1000, 200, 800))
        self.assertEqual((first["receivable_account"], first["receivable_subaccount"],
                          first["department"], first["summary"], first["status"]),
                         ("未収運賃", "得意先A補助", "営業部", "得意先A請求", "未処理"))

    def test_customer_aggregation_keeps_first_appearance_not_balance_ranking(self):
        customers = self.context()["data"]["customers"]
        self.assertEqual([item["customer_name"] for item in customers],
                         ["得意先A", "得意先B"])
        self.assertEqual(customers[0], {
            "customer_name": "得意先A", "item_count": 2,
            "invoice_amount_total": 4000, "paid_amount_total": 1200,
            "balance_total": 2800,
        })
        self.assertEqual(customers[1]["balance_total"], 1500)

    def test_invoice_and_due_dates_are_returned_without_new_judgment(self):
        rows = [row("X", "得意先X", "100", "0", "100",
                    invoice_date="original-date", due_date="original-due")]
        self.write_current(rows)
        item = self.context()["data"]["items"][0]
        self.assertEqual((item["invoice_date"], item["payment_due_date"]),
                         ("original-date", "original-due"))
        self.assertNotIn("days_until_payment_due", item)
        self.assertNotIn("risk", item)

    def test_non_numeric_values_follow_existing_web_conversion(self):
        self.write_current([row("X", "得意先X", "bad", "bad", "100")])
        item = self.context()["data"]["items"][0]
        self.assertEqual((item["invoice_amount"], item["paid_amount"], item["balance"]),
                         (0, 0, 100))

    def test_header_only_current_is_empty_context(self):
        self.write_current([])
        self.assertEqual(self.context()["data"], {
            "total_count": 0, "outstanding_count": 0, "cleanup_target_count": 0,
            "total_invoice_amount": 0, "total_paid_amount": 0,
            "total_balance": 0, "customers": [], "items": [],
        })

    def test_missing_current_uses_existing_safe_error_and_does_not_create_file(self):
        self.current.unlink()
        response = self.client.get("/api/receivables/ai-context")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "未収台帳を安全に読み込めません。")
        self.assertFalse(self.current.exists())

    def test_invalid_current_has_safe_error_and_preserves_bytes(self):
        self.current.write_bytes(b"wrong,columns\n1,2\n")
        before = self.current.read_bytes()
        response = self.client.get("/api/receivables/ai-context")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(str(self.root), response.text)
        self.assertEqual(self.current.read_bytes(), before)

    def test_history_receipt_and_archive_are_not_loaded(self):
        with patch.object(persistence, "load_receivable_history_read_only",
                          side_effect=AssertionError("history was read")), \
             patch.object(persistence, "read_settlement_receipt",
                          side_effect=AssertionError("receipt was read")):
            result = self.context()
        self.assertEqual(result["data"]["outstanding_count"], 3)
        self.assertEqual(self.history.read_bytes(), b"SECRET_HISTORY")
        self.assertEqual(self.receipt.read_bytes(), b"SECRET_RECEIPT")
        self.assertEqual(self.archive.read_bytes(), b"SECRET_ARCHIVE")

    def test_context_excludes_ids_history_receipts_cart_and_internal_paths(self):
        encoded = json.dumps(self.context(), ensure_ascii=False)
        for marker in ("SECRET_ID", "SECRET_HISTORY", "SECRET_RECEIPT",
                       "SECRET_ARCHIVE", "registrationCart", "settlement",
                       "receipt_ref", "epson_base_row"):
            self.assertNotIn(marker, encoded)
        self.assertNotIn(str(self.root), encoded)

    def test_api_is_read_only_for_all_business_files(self):
        protected = (self.current, self.history, self.receipt, self.archive)
        before = {path: path.read_bytes() for path in protected}
        response = self.client.get("/api/receivables/ai-context")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["source"], "receivable")
        self.assertEqual({path: path.read_bytes() for path in protected}, before)
        self.assertFalse((self.root / ".transactions").exists())

    def test_lock_timeout_and_recovery_are_safe(self):
        for error, expected_code in (
            (persistence.ReceivableLedgerLockTimeout("SECRET_PATH"), 423),
            (persistence.ReceivableLedgerRecoveryRequired("SECRET_PATH"), 503),
        ):
            with self.subTest(expected_code=expected_code):
                with patch.object(provider, "read_receivable_current_when_ready",
                                  side_effect=error):
                    response = self.client.get("/api/receivables/ai-context")
                self.assertEqual(response.status_code, expected_code)
                self.assertNotIn("SECRET_PATH", response.text)

    def test_existing_readiness_gate_blocks_nonready_ledger(self):
        before = self.current.read_bytes()
        health = persistence.ReceivableLedgerHealth(
            status=persistence.LEDGER_HEALTH_RECOVERY_REQUIRED,
            transaction_count=1,
            nonterminal_count=1,
        )
        with patch.object(persistence, "_inspect_receivable_ledger_health_locked",
                          return_value=health):
            response = self.client.get("/api/receivables/ai-context")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "未収台帳の復旧確認が必要です。")
        self.assertEqual(self.current.read_bytes(), before)

    def test_nonterminal_transaction_does_not_read_recovery_history_or_receipt(self):
        workspace = self.root / ".transactions" / "pending"
        workspace.mkdir(parents=True)
        paths = SimpleNamespace(marker_path=workspace / "marker.json")
        with patch.object(persistence, "resolve_receivable_transaction_paths",
                          return_value=paths), \
             patch.object(persistence, "read_transaction_marker",
                          return_value={"state": "PREPARING"}), \
             patch.object(persistence, "_validate_transaction_recovery_paths"), \
             patch.object(persistence, "_preparing_workspace_is_recoverable_read_only",
                          side_effect=AssertionError("recovery artifacts were read")), \
             patch.object(persistence, "load_receivable_history_read_only",
                          side_effect=AssertionError("history was read")), \
             patch.object(persistence, "read_settlement_receipt",
                          side_effect=AssertionError("receipt was read")):
            response = self.client.get("/api/receivables/ai-context")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "未収台帳の復旧確認が必要です。")

    def test_unexpected_error_is_safe(self):
        with patch.object(provider, "read_receivable_current_when_ready",
                          side_effect=RuntimeError("SECRET_PATH")):
            response = self.client.get("/api/receivables/ai-context")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "未収情報のContextを生成できませんでした。")
        self.assertNotIn("SECRET_PATH", response.text)


if __name__ == "__main__":
    unittest.main()

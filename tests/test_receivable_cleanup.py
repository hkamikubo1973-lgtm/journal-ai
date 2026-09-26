import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import api.receivable as api  # noqa: E402
import receivable_cleanup_service as service  # noqa: E402
from api.journal import app  # noqa: E402
from receivable_engine import CURRENT_RECEIVABLE_COLUMNS  # noqa: E402
from receivable_persistence_service import (  # noqa: E402
    ReceivableLedgerLockTimeout,
    ReceivableLedgerWriteError,
    receivable_ledger_lock,
)


def row(code, status="未処理", balance="1,000", **extra):
    value = {column: "" for column in CURRENT_RECEIVABLE_COLUMNS}
    value.update({
        "コード": code, "未収ID": code, "得意先名": f"{code}社",
        "請求日": "2026-09-01", "請求金額": "1,000",
        "残高": balance, "ステータス": status,
    })
    value.update(extra)
    return value


class ReceivableCleanupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.current = self.directory / "current.csv"
        self.history = self.directory / "receivable_history.csv"
        self.history.write_bytes(b"history sentinel")
        self.receipt = self.directory / ".settlements" / "receipt.json"
        self.receipt.parent.mkdir()
        self.receipt.write_bytes(b"receipt sentinel")
        self.transactions = self.directory / "transactions.csv"
        self.transactions.write_bytes(b"transactions sentinel")
        self.master = self.directory / "account_master.csv"
        self.master.write_bytes(b"master sentinel")
        app.dependency_overrides[api.get_receivables_directory] = lambda: self.directory
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)

    def write_current(self, rows, columns=None):
        pd.DataFrame(rows, columns=columns or CURRENT_RECEIVABLE_COLUMNS).to_csv(
            self.current, index=False, encoding="utf-8-sig"
        )

    def test_legacy_predicates_overlap_order_values_and_extra_columns(self):
        columns = CURRENT_RECEIVABLE_COLUMNS + ["追加列"]
        rows = [
            row("keep1", balance="1,000", 追加列=" A  "),
            row("done", status=" 完了 ", balance="1,000", 追加列="x"),
            row("zero", balance="0", 追加列="y"),
            row("both", status="完了", balance="0", 追加列="z"),
            row("negative", balance="-1", 追加列="n"),
            row("keep2", balance="2,500", 摘要="元の値,\n二行", 追加列="B"),
        ]
        self.write_current(rows, columns)
        before = self.current.read_bytes()
        summary = self.client.get("/api/receivables/cleanup-summary")
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json(), {
            "current_count": 6, "cleanup_target_count": 4, "remaining_count": 2,
        })
        self.assertEqual(self.current.read_bytes(), before)
        result = self.client.post("/api/receivables/cleanup")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), summary.json())
        after = pd.read_csv(self.current, dtype=str, keep_default_na=False)
        self.assertEqual(after.columns.tolist(), columns)
        self.assertEqual(after.to_dict("records"), [rows[0], rows[5]])

    def test_zero_target_does_not_write_current(self):
        self.write_current([row("keep")])
        before = self.current.read_bytes()
        with patch.object(service, "atomic_write_bytes") as writer:
            self.assertEqual(service.execute_receivable_cleanup(self.directory)["cleanup_target_count"], 0)
        writer.assert_not_called()
        self.assertEqual(self.current.read_bytes(), before)

    def test_execute_rechecks_after_summary(self):
        self.write_current([row("keep")])
        self.assertEqual(service.summarize_receivable_cleanup(self.directory)["cleanup_target_count"], 0)
        self.write_current([row("keep"), row("new", status="完了")])
        self.assertEqual(service.execute_receivable_cleanup(self.directory)["cleanup_target_count"], 1)
        self.assertEqual(pd.read_csv(self.current, dtype=str)["コード"].tolist(), ["keep"])

    def test_missing_current_does_not_create_it(self):
        for method, route in (("get", "/api/receivables/cleanup-summary"), ("post", "/api/receivables/cleanup")):
            with self.subTest(route=route):
                response = getattr(self.client, method)(route)
                self.assertEqual(response.status_code, 503)
                self.assertFalse(self.current.exists())
                self.assertNotIn(str(self.directory), response.text)

    def test_invalid_current_is_rejected_without_write(self):
        self.current.write_bytes(b"\xef\xbb\xbfwrong\nvalue\n")
        before = self.current.read_bytes()
        self.assertEqual(self.client.get("/api/receivables/cleanup-summary").status_code, 503)
        self.assertEqual(self.client.post("/api/receivables/cleanup").status_code, 503)
        self.assertEqual(self.current.read_bytes(), before)

    def test_lock_timeout_does_not_write(self):
        self.write_current([row("done", status="完了")])
        before = self.current.read_bytes()
        with receivable_ledger_lock(self.directory):
            with self.assertRaises(ReceivableLedgerLockTimeout):
                service.execute_receivable_cleanup(
                    self.directory, lock_timeout_seconds=0.01,
                    lock_poll_interval_seconds=0.001,
                )
        self.assertEqual(self.current.read_bytes(), before)

    def test_atomic_write_failure_preserves_current_and_private_errors(self):
        self.write_current([row("done", status="完了")])
        before = self.current.read_bytes()
        with patch.object(service, "atomic_write_bytes", side_effect=ReceivableLedgerWriteError("PRIVATE_PATH")):
            response = self.client.post("/api/receivables/cleanup")
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("PRIVATE_PATH", response.text)
        self.assertEqual(self.current.read_bytes(), before)

    def test_only_current_changes(self):
        self.write_current([row("done", status="完了")])
        protected = (self.history, self.receipt, self.transactions, self.master)
        before = {path: path.read_bytes() for path in protected}
        self.assertEqual(self.client.post("/api/receivables/cleanup").status_code, 200)
        self.assertEqual({path: path.read_bytes() for path in protected}, before)


if __name__ == "__main__":
    unittest.main()

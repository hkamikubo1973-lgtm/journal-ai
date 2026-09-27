"""Maintenance AI facts come from existing readers and disclose no business rows."""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import api.events as events_api  # noqa: E402
import api.receivable as receivable_api  # noqa: E402
import events_engine  # noqa: E402
import journal_master_service as master_service  # noqa: E402
import maintenance_ai_context_provider as provider  # noqa: E402
import system_settings  # noqa: E402
from api.journal import app  # noqa: E402
from receivable_engine import CURRENT_RECEIVABLE_COLUMNS  # noqa: E402
from receivable_persistence_service import (  # noqa: E402
    ReceivableLedgerLockTimeout,
    ReceivableLedgerRecoveryRequired,
)


FIXED_NOW = datetime(2026, 9, 28, 10, 30, tzinfo=timezone(timedelta(hours=9)))


def write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def receivable_row(code, *, status="未処理", balance="1,000"):
    row = dict.fromkeys(CURRENT_RECEIVABLE_COLUMNS, "")
    row.update({
        "未収ID": code, "コード": code, "得意先名": f"SECRET_CUSTOMER_{code}",
        "請求日": "2026-09-01", "請求金額": "1,000", "残高": balance,
        "ステータス": status,
    })
    return row


def event(*, title, stopped="False"):
    return {
        "month": "", "day": "30", "title": title, "memo": "SECRET_EVENT_NOTE",
        "notify_days": "3", "cycle": "monthly", "status": "pending",
        "type": "other", "stop": stopped, "last_executed": "",
    }


class MaintenanceAiContextTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.receivables = self.root / "receivables"
        self.receivables.mkdir()
        self.current = self.receivables / "current.csv"
        pd.DataFrame([
            receivable_row("open"),
            receivable_row("done", status="完了", balance="0"),
        ], columns=CURRENT_RECEIVABLE_COLUMNS).to_csv(
            self.current, index=False, encoding="utf-8-sig",
        )
        self.events = self.root / "events.csv"
        events_engine.save_events([
            event(title="SECRET_EVENT_ACTIVE"),
            event(title="SECRET_EVENT_STOPPED", stopped="True"),
        ], self.events)
        self.account = self.root / "account_master.csv"
        self.sub = self.root / "sub_master.csv"
        self.department = self.root / "department_master.csv"
        self.relation = self.root / "sub_account_relations.csv"
        write_csv(self.account, ["code", "name", "category"], [
            {"code": "114", "name": "普通預金", "category": "資産"},
            {"code": "115", "name": "普通預金", "category": "資産"},
        ])
        write_csv(self.sub, ["code", "name"], [{"code": "01", "name": "SECRET_SUB"}])
        write_csv(self.department, ["code", "name"], [{"code": "10", "name": "営業部"}])
        write_csv(self.relation, ["account_code", "sub_code", "sub_name"], [
            {"account_code": "114", "sub_code": "01", "sub_name": "SECRET_SUB"},
        ])
        self.settings = self.root / "settings.json"
        self.settings.write_text(json.dumps({
            "company_name": "SECRET_COMPANY",
            "csv_export_dir": "C:/SECRET_OUTPUT_PATH",
            "fiscal_year_start_month": 2,
        }), encoding="utf-8")
        for attr, path in (
            ("ACCOUNT_MASTER_PATH", self.account),
            ("SUB_ACCOUNT_MASTER_PATH", self.sub),
            ("DEPARTMENT_MASTER_PATH", self.department),
            ("SUB_ACCOUNT_RELATION_MASTER_PATH", self.relation),
        ):
            patcher = patch.object(master_service, attr, path)
            patcher.start()
            self.addCleanup(patcher.stop)
        settings_patch = patch.object(system_settings, "SETTINGS_PATH", self.settings)
        settings_patch.start()
        self.addCleanup(settings_patch.stop)
        app.dependency_overrides[receivable_api.get_receivables_directory] = lambda: self.receivables
        app.dependency_overrides[events_api.get_events_path] = lambda: self.events
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)

    def context(self):
        return provider.build_maintenance_ai_context(
            receivables_directory=self.receivables,
            events_path=self.events,
            now=FIXED_NOW,
        )

    def test_envelope_reuses_common_source_and_timestamp(self):
        result = self.context()
        self.assertEqual(set(result), {"schema_version", "source", "generated_at", "as_of", "data"})
        self.assertEqual((result["schema_version"], result["source"]), (1, "maintenance"))
        self.assertEqual((result["generated_at"], result["as_of"]),
                         (FIXED_NOW.isoformat(), "2026-09-28"))

    def test_master_context_uses_existing_diagnostic_counts(self):
        expected = master_service.load_journal_masters()["diagnostics"]
        actual = self.context()["data"]["masters"]
        self.assertEqual(actual["status"], "ready")
        self.assertEqual(actual["account_count"], expected["account_count"])
        self.assertEqual(actual["sub_account_relation_count"], expected["sub_account_relation_count"])
        self.assertEqual(actual["duplicate_account_name_count"], len(expected["duplicate_account_names"]))
        self.assertNotIn("accounts", actual)
        self.assertNotIn("duplicate_account_names", actual)

    def test_receivable_context_uses_one_snapshot_for_summary_and_cleanup(self):
        expected_summary = self.client.get("/api/receivables/summary").json()
        expected_cleanup = self.client.get("/api/receivables/cleanup-summary").json()
        actual = self.context()["data"]["receivables"]
        for key in ("customer_count", "outstanding_count", "outstanding_balance"):
            self.assertEqual(actual[key], expected_summary[key])
        for key in ("current_count", "cleanup_target_count", "remaining_count"):
            self.assertEqual(actual[key], expected_cleanup[key])
        self.assertEqual(actual["status"], "ready")
        self.assertNotIn("customers", actual)
        self.assertNotIn("ledger_revision", actual)

    def test_schedule_counts_reuse_schedule_context(self):
        from schedule_ai_context_provider import build_schedule_ai_context

        expected = build_schedule_ai_context(self.events, now=FIXED_NOW)["data"]
        actual = self.context()["data"]["schedule"]
        self.assertEqual(actual, {
            "status": "ready", "total_count": expected["total_count"],
            "active_count": expected["active_count"],
            "stopped_count": expected["stopped_count"],
            "notification_count": expected["notification_count"],
        })

    def test_output_folder_is_only_a_configured_flag(self):
        self.assertTrue(self.context()["data"]["output_folder_configured"])
        self.settings.write_text("{}", encoding="utf-8")
        self.assertFalse(self.context()["data"]["output_folder_configured"])

    def test_context_excludes_customer_names_event_text_paths_and_metadata(self):
        encoded = json.dumps(self.context(), ensure_ascii=False)
        for marker in ("SECRET_CUSTOMER", "SECRET_EVENT", "SECRET_SUB",
                       "SECRET_COMPANY", "SECRET_OUTPUT_PATH"):
            self.assertNotIn(marker, encoded)
        self.assertNotIn(str(self.root), encoded)

    def test_missing_current_is_reported_without_creating_it(self):
        self.current.unlink()
        result = self.context()["data"]
        self.assertEqual(result["receivables"], {"status": "missing"})
        self.assertFalse(self.current.exists())
        self.assertEqual(result["schedule"]["status"], "ready")

    def test_invalid_current_does_not_expose_internal_error(self):
        self.current.write_text("wrong,columns\n1,2\n", encoding="utf-8")
        self.assertEqual(self.context()["data"]["receivables"], {"status": "invalid"})

    def test_busy_and_recovery_states_are_safe(self):
        for error, status in (
            (ReceivableLedgerLockTimeout("SECRET_PATH"), "busy"),
            (ReceivableLedgerRecoveryRequired("SECRET_PATH"), "recovery_required"),
        ):
            with self.subTest(status=status):
                with patch.object(provider, "read_receivable_current_snapshot_when_ready",
                                  side_effect=error):
                    self.assertEqual(self.context()["data"]["receivables"], {"status": status})

    def test_missing_events_returns_empty_schedule_without_creating_file(self):
        self.events.unlink()
        actual = self.context()["data"]["schedule"]
        self.assertEqual(actual, {
            "status": "ready", "total_count": 0, "active_count": 0,
            "stopped_count": 0, "notification_count": 0,
        })
        self.assertFalse(self.events.exists())

    def test_invalid_events_are_reported_without_raw_error(self):
        self.events.write_text("wrong,columns\n1,2\n", encoding="utf-8")
        self.assertEqual(self.context()["data"]["schedule"], {"status": "unavailable"})

    def test_unavailable_master_does_not_block_other_sections(self):
        self.account.unlink()
        result = self.context()["data"]
        self.assertEqual(result["masters"], {"status": "unavailable"})
        self.assertEqual(result["receivables"]["status"], "ready")
        self.assertEqual(result["schedule"]["status"], "ready")

    def test_api_leaves_business_files_byte_identical(self):
        protected = (self.current, self.events, self.account, self.sub,
                     self.department, self.relation, self.settings)
        before = {path: path.read_bytes() for path in protected}
        response = self.client.get("/api/maintenance/ai-context")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["source"], "maintenance")
        self.assertEqual({path: path.read_bytes() for path in protected}, before)
        self.assertFalse((self.receivables / ".transactions").exists())

    def test_api_masks_unexpected_errors(self):
        with patch.object(provider, "load_system_settings", side_effect=OSError("SECRET_PATH")):
            response = self.client.get("/api/maintenance/ai-context")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "保守情報のContextを生成できませんでした")
        self.assertNotIn("SECRET_PATH", response.text)


if __name__ == "__main__":
    unittest.main()

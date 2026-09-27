"""Schedule AI context uses only the read-only event projection and temp data."""

from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import api.events as events_api  # noqa: E402
import events_application_service as events_service  # noqa: E402
import events_engine as events_engine  # noqa: E402
import schedule_ai_context_provider as provider  # noqa: E402
from api.journal import app  # noqa: E402


FIXED_NOW = datetime(2026, 9, 28, 10, 30, tzinfo=timezone(timedelta(hours=9)))


def event(**changes):
    values = {
        "month": "", "day": "30", "title": "月次処理", "memo": "備考",
        "notify_days": "3", "cycle": "monthly", "status": "pending",
        "type": "other", "stop": "False", "last_executed": "",
    }
    values.update(changes)
    return values


class ScheduleAiContextTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.path = self.directory / "events.csv"
        app.dependency_overrides[events_api.get_events_path] = lambda: self.path
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)

    def context(self):
        return provider.build_schedule_ai_context(self.path, now=FIXED_NOW)

    def save(self, *events):
        events_engine.save_events(list(events), self.path)

    def test_missing_file_returns_empty_envelope(self):
        context = self.context()
        self.assertEqual(context["schema_version"], 1)
        self.assertEqual(context["source"], "schedule")
        self.assertEqual(context["data"], {
            "total_count": 0, "active_count": 0, "stopped_count": 0,
            "notification_count": 0, "notifications": [], "upcoming": [], "stopped": [],
        })

    def test_missing_get_creates_neither_csv_nor_lock(self):
        response = self.client.get("/api/events/ai-context")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["total_count"], 0)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_existing_csv_bytes_and_directory_are_unchanged(self):
        self.save(event())
        before = self.path.read_bytes()
        names = sorted(path.name for path in self.directory.iterdir())
        self.context()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(sorted(path.name for path in self.directory.iterdir()), names)

    def test_legacy_header_is_normalized_only_in_memory(self):
        with self.path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=events_engine.EVENT_COLUMNS)
            writer.writeheader()
            writer.writerow(event())
        before = self.path.read_bytes()
        self.assertEqual(self.context()["data"]["total_count"], 1)
        self.assertEqual(self.path.read_bytes(), before)

    def test_envelope_uses_one_injected_server_timestamp(self):
        context = self.context()
        self.assertEqual(set(context), {"schema_version", "source", "generated_at", "as_of", "data"})
        self.assertEqual(context["generated_at"], FIXED_NOW.isoformat())
        self.assertEqual(context["as_of"], "2026-09-28")

    def test_total_active_and_stopped_counts(self):
        self.save(event(title="稼働中"), event(title="停止中", stop="True"))
        data = self.context()["data"]
        self.assertEqual((data["total_count"], data["active_count"], data["stopped_count"]), (2, 1, 1))

    def test_notification_count_comes_from_web_projection(self):
        self.save(event(title="対象"), event(title="対象外", day="15", notify_days="0"))
        expected = events_service.list_events_for_web(self.path, today=FIXED_NOW.date())
        self.assertEqual(self.context()["data"]["notification_count"], expected["notification_count"])

    def test_notifications_only_include_engine_targets(self):
        self.save(event(title="対象"), event(title="対象外", day="15", notify_days="0"))
        notifications = self.context()["data"]["notifications"]
        self.assertEqual([item["event_name"] for item in notifications], ["対象"])

    def test_upcoming_uses_existing_next_date_order(self):
        self.save(event(title="後", day="15", notify_days="0"), event(title="先", day="30"))
        self.assertEqual([item["event_name"] for item in self.context()["data"]["upcoming"]], ["先", "後"])

    def test_notifications_may_also_appear_in_upcoming(self):
        self.save(event(title="共通"))
        data = self.context()["data"]
        self.assertEqual(data["notifications"][0]["event_name"], "共通")
        self.assertEqual(data["upcoming"][0]["event_name"], "共通")

    def test_stopped_events_are_extracted_without_notification(self):
        self.save(event(title="停止", stop="True", day="30"))
        data = self.context()["data"]
        self.assertEqual([item["event_name"] for item in data["stopped"]], ["停止"])
        self.assertEqual(data["notification_count"], 0)
        self.assertEqual(data["upcoming"], [])

    def test_monthly_event_preserves_structured_fields(self):
        self.save(event(type="tax", memo="提出", notify_days="3"))
        item = self.context()["data"]["upcoming"][0]
        self.assertEqual((item["cycle"], item["month"], item["day"]), ("monthly", "", "30"))
        self.assertEqual((item["category"], item["notification_days"], item["note"]), ("税金", 3, "提出"))

    def test_yearly_event_preserves_month_and_backend_date(self):
        self.save(event(title="年次", cycle="yearly", month="2", day="29", notify_days="0"))
        item = self.context()["data"]["upcoming"][0]
        expected = events_service.list_events_for_web(self.path, today=FIXED_NOW.date())["events"][0]
        self.assertEqual((item["cycle"], item["month"], item["day"]), ("yearly", "2", "29"))
        self.assertEqual(item["next_date"], expected["next_date"])

    def test_next_date_days_until_and_effective_status_match_backend(self):
        self.save(event())
        item = self.context()["data"]["upcoming"][0]
        expected = events_service.list_events_for_web(self.path, today=FIXED_NOW.date())["events"][0]
        self.assertEqual((item["next_date"], item["days_until"], item["effective_status"]),
                         (expected["next_date"], expected["days_remaining"], expected["effective_status"]))

    def test_done_after_previous_cycle_is_effectively_pending(self):
        self.save(event(status="done", last_executed="2026-08-30"))
        item = self.context()["data"]["upcoming"][0]
        self.assertEqual((item["stored_status"], item["effective_status"]), ("done", "pending"))

    def test_skip_after_previous_cycle_is_effectively_pending(self):
        self.save(event(status="skip", last_executed="2026-08-30"))
        item = self.context()["data"]["upcoming"][0]
        self.assertEqual((item["stored_status"], item["effective_status"]), ("skip", "pending"))

    def test_empty_note_and_last_processed_date_are_preserved(self):
        self.save(event(memo="", last_executed=""))
        item = self.context()["data"]["upcoming"][0]
        self.assertEqual((item["note"], item["last_processed_date"]), ("", ""))

    def test_context_does_not_expose_row_index_or_snapshot(self):
        self.save(event())
        data = self.context()["data"]
        for collection in ("notifications", "upcoming", "stopped"):
            for item in data[collection]:
                self.assertNotIn("index", item)
                self.assertNotIn("expected_event", item)

    def test_provider_calls_existing_read_only_projection_once(self):
        projection = {"events": [], "notification_events": [], "notification_count": 0}
        with patch.object(provider, "list_events_for_web", return_value=projection) as mocked:
            self.context()
        mocked.assert_called_once_with(self.path, today=FIXED_NOW.date())

    def test_api_get_uses_provider_and_does_not_write(self):
        self.save(event())
        before = self.path.read_bytes()
        response = self.client.get("/api/events/ai-context")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["source"], "schedule")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((self.directory / ".events.lock").exists())

    def test_invalid_csv_has_existing_safe_error_and_is_unchanged(self):
        self.path.write_bytes("列,違い\n1,2\n".encode("utf-8-sig"))
        before = self.path.read_bytes()
        response = self.client.get("/api/events/ai-context")
        ordinary = self.client.get("/api/events")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], ordinary.json()["detail"])
        self.assertNotIn(str(self.path), response.text)
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

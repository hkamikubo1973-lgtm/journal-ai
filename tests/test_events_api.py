"""Schedule Web parity using temporary CSVs only."""

from __future__ import annotations

import csv
import io
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import api.events as events_api  # noqa: E402
import events_application_service as service  # noqa: E402
import events_engine as engine  # noqa: E402
from api.journal import app  # noqa: E402
from receivable_persistence_service import ReceivableLedgerWriteError  # noqa: E402


def event(**changes):
    result = {
        "month": "", "day": "15", "title": "月次処理", "memo": "備考",
        "notify_days": "7", "cycle": "monthly", "status": "pending",
        "type": "other", "stop": "False", "last_executed": "",
    }
    result.update(changes)
    return result


class EventsApiTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.path = self.directory / "events.csv"
        app.dependency_overrides[events_api.get_events_path] = lambda: self.path
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)

    def current(self):
        response = self.client.get("/api/events")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def create(self, **changes):
        values = {
            "title": "月次処理", "cycle": "monthly", "month": "",
            "day": "15", "notify_days": "7", "type": "other", "memo": "備考",
        }
        values.update(changes)
        response = self.client.post("/api/events", json=values)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["event"]

    def expected(self, index=0):
        return next(
            item["expected_event"] for item in self.current()["events"]
            if item["index"] == index
        )

    def test_missing_get_is_completely_read_only(self):
        self.assertEqual(self.current(), {
            "events": [], "notification_events": [], "notification_count": 0,
        })
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_current_format_get_does_not_write(self):
        engine.save_events([event()], self.path)
        before = self.path.read_bytes()
        result = self.current()
        self.assertEqual(result["events"][0]["title"], "月次処理")
        self.assertEqual(result["events"][0]["expected_event"], event())
        self.assertEqual(self.path.read_bytes(), before)

    def test_legacy_header_get_normalizes_only_in_memory(self):
        with self.path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=engine.EVENT_COLUMNS)
            writer.writeheader()
            writer.writerow(event())
        before = self.path.read_bytes()
        self.assertEqual(self.current()["events"][0]["cycle"], "monthly")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(before.startswith(b"\xef\xbb\xbfmonth,"))

    def test_invalid_header_is_rejected_without_change(self):
        self.path.write_bytes("列,違い\n1,2\n".encode("utf-8-sig"))
        before = self.path.read_bytes()
        response = self.client.get("/api/events")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(str(self.path), response.text)
        self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_row_is_rejected_without_change(self):
        engine.save_events([event()], self.path)
        raw = self.path.read_bytes().replace("月次処理".encode(), b"")
        self.path.write_bytes(raw)
        response = self.client.get("/api/events")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.path.read_bytes(), raw)

    def test_create_defaults_and_japanese_csv_contract(self):
        created = self.create(month="12", day="31")
        self.assertEqual(created["status"], "pending")
        self.assertEqual(created["stop"], "False")
        self.assertEqual(created["last_executed"], "")
        self.assertEqual(created["month"], "")
        raw = self.path.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
        self.assertEqual(list(rows[0]), engine.CSV_COLUMNS)
        self.assertEqual(rows[0]["周期"], "月次")
        self.assertEqual(rows[0]["状態"], "未処理")
        self.assertEqual(rows[0]["停止"], "FALSE")
        self.assertNotIn("id", rows[0])

    def test_create_appends_in_csv_order(self):
        self.create(title="first")
        self.create(title="second")
        self.assertEqual([item["title"] for item in engine.load_events_read_only(self.path)], ["first", "second"])

    def test_create_invalid_does_not_change_existing_file(self):
        self.create()
        before = self.path.read_bytes()
        response = self.client.post("/api/events", json={
            "title": "bad", "cycle": "weekly", "day": "15",
            "notify_days": "7", "type": "other",
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.path.read_bytes(), before)

    def test_edit_updates_legacy_fields_and_status_date_via_engine(self):
        self.create()
        expected = self.expected()
        response = self.client.put("/api/events/0", json={
            "expected_event": expected, "title": "年次処理", "cycle": "yearly",
            "month": "2", "day": "29", "notify_days": "3", "type": "tax",
            "memo": "更新", "status": "done",
        })
        self.assertEqual(response.status_code, 200, response.text)
        saved = engine.load_events_read_only(self.path)[0]
        self.assertEqual((saved["title"], saved["cycle"], saved["month"], saved["type"]),
                         ("年次処理", "yearly", "2", "tax"))
        self.assertEqual(saved["status"], "done")
        self.assertEqual(saved["last_executed"], engine.calculate_next_target_date(
            event(month="2", day="29", cycle="yearly"), date.today()
        ).isoformat())

    def test_edit_rejects_stale_snapshot(self):
        self.create()
        stale = self.expected()
        service.edit_event_for_web(0, stale, {"title": "changed"}, self.path)
        before = self.path.read_bytes()
        response = self.client.put("/api/events/0", json={
            "expected_event": stale, "title": "overwrite", "cycle": "monthly",
            "day": "15", "notify_days": "7", "type": "other", "memo": "備考",
            "status": "pending",
        })
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "イベント一覧が更新されています。再読み込みしてください")
        self.assertEqual(self.path.read_bytes(), before)

    def test_all_actions_reject_stale_snapshot(self):
        self.create()
        stale = self.expected()
        service.edit_event_for_web(0, stale, {"memo": "changed"}, self.path)
        before = self.path.read_bytes()
        for action in ("complete", "skip", "stop", "resume"):
            response = self.client.post(f"/api/events/0/{action}", json={"expected_event": stale})
            self.assertEqual(response.status_code, 409, action)
        self.assertEqual(self.path.read_bytes(), before)

    def test_index_shift_rejects_wrong_row(self):
        self.create(title="first")
        self.create(title="second")
        stale_second = self.expected(1)
        first = self.expected(0)
        self.assertEqual(self.client.request("DELETE", "/api/events/0", json={"expected_event": first}).status_code, 200)
        before = self.path.read_bytes()
        response = self.client.post("/api/events/1/complete", json={"expected_event": stale_second})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.path.read_bytes(), before)

    def test_complete_records_target_date_and_advances(self):
        self.create()
        expected = self.expected()
        target = engine.calculate_next_target_date(expected, date.today())
        response = self.client.post("/api/events/0/complete", json={"expected_event": expected})
        self.assertEqual(response.status_code, 200)
        saved = engine.load_events_read_only(self.path)[0]
        self.assertEqual(saved["status"], "done")
        self.assertEqual(saved["last_executed"], target.isoformat())
        self.assertEqual(engine.calculate_next_target_date(saved, date.today()),
                         engine.calculate_following_run_date(saved, target))

    def test_skip_records_target_date_and_advances(self):
        self.create()
        expected = self.expected()
        target = engine.calculate_next_target_date(expected, date.today())
        response = self.client.post("/api/events/0/skip", json={"expected_event": expected})
        self.assertEqual(response.status_code, 200)
        saved = engine.load_events_read_only(self.path)[0]
        self.assertEqual(saved["status"], "skip")
        self.assertEqual(saved["last_executed"], target.isoformat())

    def test_stop_resume_preserve_status_and_last_executed(self):
        self.create()
        first = self.expected()
        self.client.post("/api/events/0/complete", json={"expected_event": first})
        processed = self.expected()
        stopped = self.client.post("/api/events/0/stop", json={"expected_event": processed})
        self.assertEqual(stopped.status_code, 200)
        self.assertEqual(stopped.json()["event"]["stop"], "True")
        self.assertEqual(stopped.json()["event"]["status"], processed["status"])
        self.assertEqual(stopped.json()["event"]["last_executed"], processed["last_executed"])
        resumed = self.client.post("/api/events/0/resume", json={"expected_event": self.expected()})
        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(resumed.json()["event"]["stop"], "False")
        self.assertEqual(resumed.json()["event"]["last_executed"], processed["last_executed"])

    def test_delete_rechecks_content_and_preserves_other_row(self):
        self.create(title="first")
        self.create(title="second")
        expected = self.expected(0)
        response = self.client.request("DELETE", "/api/events/0", json={"expected_event": expected})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["title"] for row in engine.load_events_read_only(self.path)], ["second"])
        self.assertEqual(self.client.request("DELETE", "/api/events/0", json={"expected_event": expected}).status_code, 409)

    def test_atomic_write_failure_preserves_original_bytes(self):
        self.create()
        before = self.path.read_bytes()
        with patch.object(service, "atomic_write_bytes", side_effect=ReceivableLedgerWriteError("PRIVATE_PATH")):
            response = self.client.post("/api/events/0/stop", json={"expected_event": self.expected()})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("PRIVATE_PATH", response.text)
        self.assertEqual(self.path.read_bytes(), before)

    def test_lock_contention_rejects_without_writing(self):
        self.create()
        before = self.path.read_bytes()
        with service.event_file_lock(self.path):
            with self.assertRaises(service.EventLockTimeout):
                with service.event_file_lock(self.path, timeout_seconds=0.01):
                    pass
        self.assertEqual(self.path.read_bytes(), before)

    def test_external_write_between_snapshot_and_save_is_not_overwritten(self):
        self.create()
        expected = self.expected()
        external = engine.save_events

        def mutate_staged(staged):
            updated = engine.update_event(0, {"title": "Web change"}, staged)
            external([event(title="External change")], self.path)
            return updated

        with self.assertRaises(service.EventConflictError):
            service._mutate(self.path, mutate_staged, index=0, expected_event=expected)
        self.assertEqual(engine.load_events_read_only(self.path)[0]["title"], "External change")

    def test_notification_fields_are_computed_by_backend(self):
        today = date(2026, 3, 10)
        rows = [event(title="today", day="10", notify_days="0"),
                event(title="soon", day="12", notify_days="2"),
                event(title="later", day="20", notify_days="2"),
                event(title="stopped", day="10", notify_days="2", stop="True")]
        engine.save_events(rows, self.path)
        result = service.list_events_for_web(self.path, today=today)
        self.assertEqual([item["title"] for item in result["notification_events"]], ["today", "soon"])
        self.assertEqual(result["notification_count"], 2)
        self.assertEqual([item["days_remaining"] for item in result["notification_events"]], [0, 2])
        self.assertFalse(next(item for item in result["events"] if item["title"] == "stopped")["notification_target"])


class EventsEngineParityTest(unittest.TestCase):
    def test_monthly_and_annual_next_dates(self):
        self.assertEqual(engine.calculate_next_run_date(event(day="15"), date(2026, 3, 16)), date(2026, 4, 15))
        self.assertEqual(engine.calculate_next_run_date(event(cycle="yearly", month="4", day="1"), date(2026, 4, 2)), date(2027, 4, 1))

    def test_short_month_and_february_29_clamp(self):
        self.assertEqual(engine.calculate_next_run_date(event(day="31"), date(2026, 2, 1)), date(2026, 2, 28))
        self.assertEqual(engine.calculate_next_run_date(event(cycle="yearly", month="2", day="29"), date(2025, 1, 1)), date(2025, 2, 28))
        self.assertEqual(engine.calculate_next_run_date(event(cycle="yearly", month="2", day="29"), date(2024, 1, 1)), date(2024, 2, 29))

    def test_notification_window_today_and_stop(self):
        item = event(day="15", notify_days="3")
        self.assertFalse(engine.is_notification_target(item, date(2026, 3, 11)))
        self.assertTrue(engine.is_notification_target(item, date(2026, 3, 12)))
        self.assertTrue(engine.is_notification_target(item, date(2026, 3, 15)))
        self.assertFalse(engine.is_notification_target(event(day="15", notify_days="3", stop="True"), date(2026, 3, 15)))

    def test_done_and_skip_become_effectively_pending_next_cycle(self):
        for status in ("done", "skip"):
            item = event(status=status, last_executed="2026-03-15")
            self.assertEqual(engine.get_effective_status(item, date(2026, 3, 15)), status)
            self.assertEqual(engine.get_effective_status(item, date(2026, 4, 1)), "pending")
            self.assertEqual(engine.calculate_next_target_date(item, date(2026, 4, 1)), date(2026, 4, 15))


if __name__ == "__main__":
    unittest.main()

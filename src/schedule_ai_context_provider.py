"""Read-only schedule facts for future AI payloads.

Notification events also appear in upcoming; the two lists serve different views.
No CSV row index or mutation identifier is exposed.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ai_context_envelope import ContextEnvelope, make_context_envelope
from events_application_service import list_events_for_web
from events_engine import EVENTS_PATH, TYPE_LABELS


def _context_event(event: dict) -> dict:
    return {
        "event_name": event["title"],
        "note": event["memo"],
        "cycle": event["cycle"],
        "month": event["month"],
        "day": event["day"],
        "category": TYPE_LABELS[event["type"]],
        "notification_days": int(event["notify_days"]),
        "stored_status": event["status"],
        "effective_status": event["effective_status"],
        "next_date": event["next_date"],
        "days_until": event["days_remaining"],
        "last_processed_date": event["last_executed"],
        "stopped": event["stopped"],
    }


def build_schedule_ai_context(
    path: Path = EVENTS_PATH, *, now: datetime | None = None,
) -> ContextEnvelope:
    """Build facts from the existing read-only Web projection at one server date."""
    now = now if now is not None else datetime.now().astimezone()
    snapshot = list_events_for_web(path, today=now.date())
    active = [event for event in snapshot["events"] if not event["stopped"]]
    stopped = [event for event in snapshot["events"] if event["stopped"]]
    return make_context_envelope("schedule", {
        "total_count": len(snapshot["events"]),
        "active_count": len(active),
        "stopped_count": len(stopped),
        "notification_count": snapshot["notification_count"],
        "notifications": [_context_event(event) for event in snapshot["notification_events"]],
        "upcoming": [_context_event(event) for event in active],
        "stopped": [_context_event(event) for event in stopped],
    }, now=now)

"""Safe Web access to the existing Streamlit event engine."""

from __future__ import annotations

import tempfile
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from events_engine import (
    EVENTS_PATH,
    EVENT_COLUMNS,
    add_event,
    complete_event,
    delete_event,
    get_effective_status,
    get_notification_events,
    load_events_read_only,
    parse_events_csv_read_only,
    resume_event,
    skip_event,
    sort_events_for_display,
    stop_event,
    update_event,
)
from receivable_persistence_service import (
    _process_lock_for,
    _release_os_lock,
    _try_acquire_os_lock,
    atomic_write_bytes,
)


class EventConflictError(Exception):
    """The row index no longer identifies the event shown to the user."""


class EventLockTimeout(Exception):
    """Another event mutation still holds the CSV lock."""


@contextmanager
def event_file_lock(path: Path, *, timeout_seconds: float = 5.0):
    """Use a dedicated event lock with the existing cross-process lock helpers."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(".events.lock")
    process_lock = _process_lock_for(lock_path)
    deadline = time.monotonic() + timeout_seconds
    if not process_lock.acquire(timeout=timeout_seconds):
        raise EventLockTimeout()
    lock_file = None
    locked = False
    try:
        lock_file = lock_path.open("a+b")
        while not _try_acquire_os_lock(lock_file):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise EventLockTimeout()
            time.sleep(min(0.05, remaining))
        locked = True
        yield
    finally:
        try:
            if locked:
                _release_os_lock(lock_file)
        finally:
            if lock_file is not None:
                lock_file.close()
            process_lock.release()


def list_events_for_web(path: Path = EVENTS_PATH, *, today: date | None = None):
    """Calculate every display field in the engine without creating a file."""
    today = today or date.today()
    events = load_events_read_only(path)
    notifications = get_notification_events(events, today)
    notification_indexes = {event["index"] for event in notifications}
    displayed = []
    for item in sort_events_for_display(events, today):
        event = {column: item[column] for column in EVENT_COLUMNS}
        next_date = item["next_date"]
        displayed.append({
            **event,
            "index": item["index"],
            "expected_event": dict(event),
            "effective_status": get_effective_status(event, today),
            "stopped": event["stop"] == "True",
            "next_date": next_date.isoformat(),
            "days_remaining": (next_date - today).days,
            "notification_target": item["index"] in notification_indexes,
        })
    by_index = {item["index"]: item for item in displayed}
    return {
        "events": displayed,
        "notification_events": [by_index[item["index"]] for item in notifications],
        "notification_count": len(notifications),
    }


def _mutate(path: Path, operation, *, index=None, expected_event=None):
    path = Path(path)
    with event_file_lock(path):
        try:
            before = path.read_bytes()
        except FileNotFoundError:
            before = None
        events = [] if before is None else parse_events_csv_read_only(before)
        if index is not None:
            if not 0 <= index < len(events) or events[index] != expected_event:
                raise EventConflictError()
        with tempfile.TemporaryDirectory(prefix="journal-events-") as directory:
            staged_path = Path(directory) / "events.csv"
            if before is not None:
                staged_path.write_bytes(before)
            result = operation(staged_path)
            try:
                latest = path.read_bytes()
            except FileNotFoundError:
                latest = None
            if latest != before:
                raise EventConflictError()
            atomic_write_bytes(path, staged_path.read_bytes())
        return result


def create_event_for_web(event, path: Path = EVENTS_PATH):
    return _mutate(path, lambda staged: add_event(event, staged))


def edit_event_for_web(index, expected_event, updates, path: Path = EVENTS_PATH):
    return _mutate(
        path, lambda staged: update_event(index, updates, staged),
        index=index, expected_event=expected_event,
    )


def act_on_event_for_web(
    index, expected_event, action, path: Path = EVENTS_PATH, *, today: date | None = None,
):
    actions = {
        "complete": complete_event,
        "skip": skip_event,
        "stop": stop_event,
        "resume": resume_event,
    }
    if action not in actions:
        raise ValueError("イベント操作が不正です")
    def apply(staged):
        if action in {"complete", "skip"}:
            return actions[action](index, today=today, path=staged)
        return actions[action](index, path=staged)

    return _mutate(
        path, apply,
        index=index, expected_event=expected_event,
    )


def delete_event_for_web(index, expected_event, path: Path = EVENTS_PATH):
    return _mutate(
        path, lambda staged: delete_event(index, expected_event, staged),
        index=index, expected_event=expected_event,
    )

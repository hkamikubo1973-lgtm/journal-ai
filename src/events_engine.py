"""イベント管理ページ用の保存・日付・状態管理ロジック。"""

import calendar
import csv
from datetime import date, datetime, timedelta
from pathlib import Path


EVENT_COLUMNS = [
    "month",
    "day",
    "title",
    "memo",
    "notify_days",
    "cycle",
    "status",
    "type",
    "stop",
    "last_executed",
]

EVENT_TYPES = {"tax", "payment", "card", "other"}
EVENT_CYCLES = {"monthly", "yearly"}
EVENT_STATUSES = {"pending", "notified", "done", "skip"}
EVENTS_PATH = Path(__file__).resolve().parent.parent / "data" / "events.csv"


def ensure_events_csv(path=EVENTS_PATH):
    """events.csv がなければ、ヘッダだけのファイルを作成する。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open("w", encoding="utf-8-sig", newline="") as file:
            csv.DictWriter(file, fieldnames=EVENT_COLUMNS).writeheader()
    return path


def _as_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "on"}


def _normalize_event(event):
    normalized = {
        column: str(event.get(column, "") or "").strip()
        for column in EVENT_COLUMNS
    }
    normalized["cycle"] = normalized["cycle"] or "monthly"
    normalized["status"] = normalized["status"] or "pending"
    normalized["type"] = normalized["type"] or "other"
    normalized["stop"] = "True" if _as_bool(event.get("stop")) else "False"
    return normalized


def load_events(path=EVENTS_PATH):
    """保存済みイベントをCSVの並び順で読み込む。"""
    path = ensure_events_csv(path)
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return [_normalize_event(row) for row in csv.DictReader(file)]


def save_events(events, path=EVENTS_PATH):
    """イベント一覧を指定された固定カラムで保存する。"""
    path = ensure_events_csv(path)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=EVENT_COLUMNS)
        writer.writeheader()
        writer.writerows(_normalize_event(event) for event in events)


def _value_as_int(value, field_name, minimum, maximum):
    try:
        result = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"{field_name}は数値で入力してください")
    if not minimum <= result <= maximum:
        raise ValueError(
            f"{field_name}は{minimum}から{maximum}の範囲で入力してください"
        )
    return result


def _event_date(year, month, day):
    """31日や2月29日は、その月の末日に補正する。"""
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last_day))


def calculate_next_run_date(event, today=None):
    """基準日以降に来る月次または年次イベントの日付を返す。"""
    today = today or date.today()
    day = _value_as_int(event.get("day"), "日", 1, 31)
    cycle = str(event.get("cycle", "")).strip()

    if cycle == "monthly":
        candidate = _event_date(today.year, today.month, day)
        if candidate >= today:
            return candidate
        next_month = 1 if today.month == 12 else today.month + 1
        next_year = today.year + 1 if today.month == 12 else today.year
        return _event_date(next_year, next_month, day)

    if cycle == "yearly":
        month = _value_as_int(event.get("month"), "月", 1, 12)
        candidate = _event_date(today.year, month, day)
        if candidate >= today:
            return candidate
        return _event_date(today.year + 1, month, day)

    raise ValueError("周期はmonthlyまたはyearlyを指定してください")


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        return None


def get_effective_status(event, today=None):
    """完了・スキップ後に次周期へ進んでいれば pending として扱う。"""
    today = today or date.today()
    status = str(event.get("status", "pending") or "pending")
    if status not in {"done", "skip"}:
        return status

    last_executed = _parse_date(event.get("last_executed"))
    if last_executed is None:
        return status

    if calculate_next_run_date(event, today) != calculate_next_run_date(
        event,
        last_executed,
    ):
        return "pending"
    return status


def is_notification_target(event, today=None):
    """イベントが現在の通知期間内か判定する。"""
    today = today or date.today()
    if _as_bool(event.get("stop")):
        return False
    if get_effective_status(event, today) not in {"pending", "notified"}:
        return False

    notify_days = _value_as_int(
        event.get("notify_days", 0),
        "通知日前",
        0,
        365,
    )
    next_date = calculate_next_run_date(event, today)
    return next_date - timedelta(days=notify_days) <= today <= next_date


def get_notification_events(events=None, today=None, path=EVENTS_PATH):
    """通知対象イベントに表示用の日付情報とCSV上の行番号を付ける。"""
    today = today or date.today()
    events = load_events(path) if events is None else events
    results = []
    for index, event in enumerate(events):
        if not is_notification_target(event, today):
            continue
        item = dict(event)
        item["index"] = index
        item["next_date"] = calculate_next_run_date(event, today)
        item["days_remaining"] = (item["next_date"] - today).days
        item["effective_status"] = get_effective_status(event, today)
        results.append(item)
    return sorted(results, key=lambda item: (item["next_date"], item["title"]))


def validate_event(event):
    title = str(event.get("title", "") or "").strip()
    if not title:
        raise ValueError("イベント名を入力してください")

    cycle = str(event.get("cycle", "") or "").strip()
    if cycle not in EVENT_CYCLES:
        raise ValueError("周期はmonthlyまたはyearlyを指定してください")
    _value_as_int(event.get("day"), "日", 1, 31)
    if cycle == "yearly":
        _value_as_int(event.get("month"), "月", 1, 12)

    _value_as_int(event.get("notify_days", 0), "通知日前", 0, 365)
    if str(event.get("type", "") or "") not in EVENT_TYPES:
        raise ValueError("種別が不正です")
    status = str(event.get("status", "pending") or "pending")
    if status not in EVENT_STATUSES:
        raise ValueError("状態が不正です")


def add_event(event, path=EVENTS_PATH):
    """新しいイベントを末尾へ追加する。"""
    new_event = _normalize_event(event)
    new_event["month"] = (
        new_event["month"] if new_event["cycle"] == "yearly" else ""
    )
    new_event["status"] = "pending"
    new_event["stop"] = "False"
    new_event["last_executed"] = ""
    validate_event(new_event)
    events = load_events(path)
    events.append(new_event)
    save_events(events, path)
    return new_event


def update_event(index, updates, path=EVENTS_PATH):
    """CSV上の行番号を使ってイベントを簡易更新する。"""
    events = load_events(path)
    if not 0 <= index < len(events):
        raise IndexError("更新対象のイベントが見つかりません")
    previous_status = events[index]["status"]
    updated = dict(events[index])
    updated.update(updates)
    updated = _normalize_event(updated)
    if (
        "status" in updates
        and updated["status"] in {"done", "skip"}
        and updated["status"] != previous_status
        and "last_executed" not in updates
    ):
        updated["last_executed"] = date.today().isoformat()
    if updated["cycle"] == "monthly":
        updated["month"] = ""
    validate_event(updated)
    events[index] = updated
    save_events(events, path)
    return updated


def _set_status(index, status, today=None, path=EVENTS_PATH):
    today = today or date.today()
    return update_event(
        index,
        {"status": status, "last_executed": today.isoformat()},
        path,
    )


def complete_event(index, today=None, path=EVENTS_PATH):
    return _set_status(index, "done", today, path)


def skip_event(index, today=None, path=EVENTS_PATH):
    return _set_status(index, "skip", today, path)


def stop_event(index, path=EVENTS_PATH):
    return update_event(index, {"stop": "True"}, path)


def resume_event(index, path=EVENTS_PATH):
    return update_event(index, {"stop": "False"}, path)

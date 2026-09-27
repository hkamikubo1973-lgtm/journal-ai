"""Schedule API backed by the existing event engine."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from events_application_service import (
    EventConflictError,
    EventLockTimeout,
    act_on_event_for_web,
    create_event_for_web,
    delete_event_for_web,
    edit_event_for_web,
    list_events_for_web,
)
from events_engine import EVENTS_PATH, InvalidEventsCsv
from schedule_ai_context_provider import build_schedule_ai_context


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/events", tags=["events"])


def get_events_path() -> Path:
    return EVENTS_PATH


class EventSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    month: str
    day: str
    title: str
    memo: str
    notify_days: str
    cycle: str
    status: str
    type: str
    stop: str
    last_executed: str


class EventCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    cycle: str
    month: str | int = ""
    day: str | int
    notify_days: str | int
    type: str
    memo: str = ""


class EventEditRequest(EventCreateRequest):
    status: str
    expected_event: EventSnapshot


class EventActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_event: EventSnapshot


def _safe_response(operation):
    try:
        return operation()
    except EventConflictError as error:
        raise HTTPException(
            status_code=409,
            detail="イベント一覧が更新されています。再読み込みしてください",
        ) from error
    except EventLockTimeout as error:
        raise HTTPException(
            status_code=423,
            detail="イベント一覧をほかの処理が使用中です。",
        ) from error
    except InvalidEventsCsv as error:
        raise HTTPException(
            status_code=503,
            detail="イベントCSVを読み込めません。内容を確認してください。",
        ) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        logger.exception("Event API operation failed")
        raise HTTPException(
            status_code=500,
            detail="イベントの処理に失敗しました。再読み込みして確認してください。",
        ) from error


@router.get("")
def list_events(path: Path = Depends(get_events_path)):
    return _safe_response(lambda: list_events_for_web(path))


@router.get("/ai-context")
def get_schedule_ai_context(path: Path = Depends(get_events_path)):
    return _safe_response(lambda: build_schedule_ai_context(path))


@router.post("")
def create_event(request: EventCreateRequest, path: Path = Depends(get_events_path)):
    return _safe_response(lambda: {
        "event": create_event_for_web(request.model_dump(), path),
    })


@router.put("/{index}")
def edit_event(index: int, request: EventEditRequest, path: Path = Depends(get_events_path)):
    values = request.model_dump(exclude={"expected_event"})
    return _safe_response(lambda: {
        "event": edit_event_for_web(
            index, request.expected_event.model_dump(), values, path,
        ),
    })


def _action(index: int, request: EventActionRequest, action: str, path: Path):
    return _safe_response(lambda: {
        "event": act_on_event_for_web(
            index, request.expected_event.model_dump(), action, path,
        ),
    })


@router.post("/{index}/complete")
def complete_event(index: int, request: EventActionRequest, path: Path = Depends(get_events_path)):
    return _action(index, request, "complete", path)


@router.post("/{index}/skip")
def skip_event(index: int, request: EventActionRequest, path: Path = Depends(get_events_path)):
    return _action(index, request, "skip", path)


@router.post("/{index}/stop")
def stop_event(index: int, request: EventActionRequest, path: Path = Depends(get_events_path)):
    return _action(index, request, "stop", path)


@router.post("/{index}/resume")
def resume_event(index: int, request: EventActionRequest, path: Path = Depends(get_events_path)):
    return _action(index, request, "resume", path)


@router.delete("/{index}")
def delete_event(index: int, request: EventActionRequest, path: Path = Depends(get_events_path)):
    return _safe_response(lambda: {
        "event": delete_event_for_web(
            index, request.expected_event.model_dump(), path,
        ),
    })

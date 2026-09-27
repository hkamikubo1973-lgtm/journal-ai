"""Small versioned envelope for read-only business context."""

from __future__ import annotations

from datetime import datetime
from typing import Any, TypedDict


class ContextEnvelope(TypedDict):
    schema_version: int
    source: str
    generated_at: str
    as_of: str
    data: dict[str, Any]


def make_context_envelope(source: str, data: dict[str, Any], *, now: datetime) -> ContextEnvelope:
    return {
        "schema_version": 1,
        "source": source,
        "generated_at": now.isoformat(),
        "as_of": now.date().isoformat(),
        "data": data,
    }

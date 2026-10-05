"""Read-only, candidate-scoped company facts for Journal AI Assist."""

from __future__ import annotations

import csv
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from ai_context_envelope import ContextEnvelope, make_context_envelope
from engine import normalize


DEFAULT_KNOWLEDGE_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "company_knowledge" / "entries.csv"
)
KNOWLEDGE_COLUMNS = (
    "namespace", "match_field", "key", "label", "description", "source", "active",
)
TEXT_FIELDS = frozenset({"summary", "voucher_summary"})
EXACT_FIELDS = frozenset({
    "debit_account_code", "debit_account_name",
    "debit_sub_code", "debit_sub_name",
    "debit_department_code", "debit_department_name",
    "credit_account_code", "credit_account_name",
    "credit_sub_code", "credit_sub_name",
    "credit_department_code", "credit_department_name",
})
ALLOWED_MATCH_FIELDS = TEXT_FIELDS | EXACT_FIELDS
MAX_MATCHES = 100
MAX_CONTEXT_CHARACTERS = 16000
MAX_CELL_LENGTH = 500


class InvalidCompanyKnowledge(ValueError):
    """The optional CSV cannot safely be used for this request."""


def _load_entries(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        if reader.fieldnames is None:
            return []  # A zero-byte file is an empty, optional knowledge base.
        if len(reader.fieldnames) != len(KNOWLEDGE_COLUMNS) or set(reader.fieldnames) != set(KNOWLEDGE_COLUMNS):
            raise InvalidCompanyKnowledge("Invalid columns")

        entries: dict[tuple[str, str, str], dict[str, str]] = {}
        ambiguous: set[tuple[str, str, str]] = set()
        for row in reader:
            if set(row) != set(KNOWLEDGE_COLUMNS) or any(
                not isinstance(value, str) or len(value) > MAX_CELL_LENGTH
                for value in row.values()
            ):
                raise InvalidCompanyKnowledge("Invalid row")
            item = {column: row[column].strip() for column in KNOWLEDGE_COLUMNS}
            active = item["active"].casefold()
            if active not in {"true", "false"}:
                raise InvalidCompanyKnowledge("Invalid active flag")
            if active == "false":
                continue
            if (
                not item["namespace"] or not item["key"] or not item["label"]
                or not item["source"] or item["match_field"] not in ALLOWED_MATCH_FIELDS
            ):
                raise InvalidCompanyKnowledge("Invalid active entry")
            identity = (
                normalize(item["namespace"]), item["match_field"], normalize(item["key"]),
            )
            if not all(identity):
                raise InvalidCompanyKnowledge("Invalid key")
            previous = entries.get(identity)
            if previous is not None and any(
                previous[field] != item[field]
                for field in ("label", "description", "source")
            ):
                ambiguous.add(identity)
            elif previous is None:
                entries[identity] = item
        return [item for identity, item in entries.items() if identity not in ambiguous]


def _matches(value: Any, key: str, field: str) -> bool:
    text = normalize(value)
    needle = normalize(key)
    if not text or not needle:
        return False
    if field in EXACT_FIELDS:
        return text == needle
    if needle.isascii() and needle.isalnum():
        # Python's \b treats Japanese letters as word characters. Only ASCII
        # letters/digits are relevant to the boundary of these company codes.
        return re.search(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])", text) is not None
    # For names/phrases, match a whole whitespace-or-punctuation-delimited
    # token; never accept an arbitrary substring of another name.
    return any(needle == token for token in re.split(r"[\s、。，．・/／:：;；()（）\[\]「」『』]+", text))


def build_company_knowledge_context(
    journal_context: Mapping[str, Any],
    *,
    path: Path = DEFAULT_KNOWLEDGE_PATH,
    now: datetime | None = None,
) -> ContextEnvelope:
    """Match only the projected rows in this search, without changing them."""
    now = now if now is not None else datetime.now().astimezone()
    try:
        entries = _load_entries(Path(path))
    except (OSError, UnicodeError, csv.Error, InvalidCompanyKnowledge):
        logging.getLogger(__name__).warning("Company Knowledge is unavailable; AI Assist will continue without it")
        entries = []

    matches: list[dict[str, Any]] = []
    truncated = False
    context_characters = 0
    for candidate in journal_context["data"]["candidates"]:
        for row_number, row in enumerate(candidate["rows"], start=1):
            for entry in entries:
                field = entry["match_field"]
                if _matches(row.get(field, ""), entry["key"], field):
                    matched = {
                        "candidate_rank": candidate["rank"],
                        "row_number": row_number,
                        "namespace": entry["namespace"],
                        "match_field": field,
                        "key": entry["key"],
                        "label": entry["label"],
                        "description": entry["description"],
                        "source": entry["source"],
                    }
                    matched_characters = len(json.dumps(matched, ensure_ascii=False))
                    if (
                        len(matches) >= MAX_MATCHES
                        or context_characters + matched_characters > MAX_CONTEXT_CHARACTERS
                    ):
                        truncated = True
                        break
                    matches.append(matched)
                    context_characters += matched_characters
            if truncated:
                break
        if truncated:
            break
    return make_context_envelope("company_knowledge", {
        "matches": matches,
        "truncated": truncated,
    }, now=now)

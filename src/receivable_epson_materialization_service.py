"""Pure EPSON template resolution for trusted receivable journal items."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from hashlib import sha256
import json
import re
import unicodedata
from typing import Any

from columns import EPSON_COLUMNS
from journal_registration_service import (
    EDIT_FORM_FIELDS,
    build_epson_edit_values,
    build_registration_id,
    extract_epson_source_row,
)


TEMPLATE_NOT_FOUND = "template_not_found"
TEMPLATE_AMBIGUOUS = "template_ambiguous"
TEMPLATE_INVALID = "template_invalid"
PREPARED_JOURNAL_INVALID = "prepared_journal_invalid"

STRUCTURAL_MATCH_COLUMNS = (
    ("debit_account_code", "借方科目"),
    ("credit_account_code", "貸方科目"),
    ("debit_sub_code", "借方補助"),
    ("credit_sub_code", "貸方補助"),
    ("debit_dept_code", "借方部門"),
    ("credit_dept_code", "貸方部門"),
)

CRITICAL_METADATA_COLUMNS = (
    "月種別",
    "種類",
    "形式",
    "作成方法",
    "付箋",
    "伝票番号",
    "枝番",
    "借方消費税コード",
    "借方消費税業種",
    "借方消費税税率",
    "借方資金区分",
    "借方任意項目１",
    "借方任意項目２",
    "借方インボイス情報",
    "貸方消費税コード",
    "貸方消費税業種",
    "貸方消費税税率",
    "貸方資金区分",
    "貸方任意項目１",
    "貸方任意項目２",
    "貸方インボイス情報",
    "期日",
)

_COMPANY_DESIGNATORS = (
    "株式会社",
    "有限会社",
    "(株)",
    "（株）",
    "(有)",
    "（有）",
    "㈱",
    "㈲",
)


class ReceivableEpsonMaterializationError(ValueError):
    """Fail one complete materialization batch without returning partial rows."""

    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        item_number: int | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.item_number = item_number


def normalize_template_match_text(value: Any) -> str:
    """Normalize text for exact-only customer and summary ranking."""

    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    for designator in _COMPANY_DESIGNATORS:
        text = text.replace(
            unicodedata.normalize("NFKC", designator).lower(),
            "",
        )
    return re.sub(r"[\s・･.,，．、。()\[\]（）【】\-ー－]", "", text)


def _canonical_projection(
    row: Mapping[str, Any],
    columns: Sequence[str],
) -> str:
    try:
        return json.dumps(
            [[column, row[column]] for column in columns],
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ReceivableEpsonMaterializationError(
            TEMPLATE_INVALID,
            "EPSON template contains invalid values.",
        ) from exc


def build_template_row_hash(row: Mapping[str, Any]) -> str:
    """Hash one complete template in EPSON column order for a stable tie-break."""

    return sha256(
        _canonical_projection(row, EPSON_COLUMNS).encode("utf-8")
    ).hexdigest()


def build_critical_metadata_signature(row: Mapping[str, Any]) -> str:
    """Return a type-preserving signature of metadata not overlaid later."""

    return _canonical_projection(row, CRITICAL_METADATA_COLUMNS)


def _validated_prepared_journal(
    item: Mapping[str, Any],
    item_number: int,
) -> dict[str, Any]:
    prepared = item.get("prepared_journal")
    if not isinstance(prepared, Mapping):
        raise ReceivableEpsonMaterializationError(
            PREPARED_JOURNAL_INVALID,
            f"{item_number}件目のprepared_journalがありません。",
            item_number=item_number,
        )
    missing = [field for field in EDIT_FORM_FIELDS if field not in prepared]
    if missing:
        raise ReceivableEpsonMaterializationError(
            PREPARED_JOURNAL_INVALID,
            f"{item_number}件目のprepared_journalに必要な項目が不足しています。",
            item_number=item_number,
        )
    return {field: prepared[field] for field in EDIT_FORM_FIELDS}


def _validated_template_snapshot(
    transactions_snapshot: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(transactions_snapshot, Sequence) or isinstance(
        transactions_snapshot, (str, bytes)
    ):
        raise ReceivableEpsonMaterializationError(
            TEMPLATE_INVALID,
            "EPSON template snapshot is invalid.",
        )

    rows: list[dict[str, Any]] = []
    for source_row in transactions_snapshot:
        template, error = extract_epson_source_row(source_row)
        if error or template is None:
            raise ReceivableEpsonMaterializationError(
                TEMPLATE_INVALID,
                "EPSON template snapshot contains an invalid 45-column row.",
            )
        try:
            build_template_row_hash(template)
        except ReceivableEpsonMaterializationError:
            raise
        rows.append(template)
    return rows


def _matches_structure(
    prepared_journal: Mapping[str, Any],
    template: Mapping[str, Any],
) -> bool:
    return all(
        prepared_journal[field] == template[column]
        for field, column in STRUCTURAL_MATCH_COLUMNS
    )


def _has_exact_normalized_match(
    expected: Any,
    template: Mapping[str, Any],
) -> bool:
    normalized = normalize_template_match_text(expected)
    if not normalized:
        return False
    return any(
        normalize_template_match_text(template[column]) == normalized
        for column in (
            "摘要",
            "伝票摘要",
            "借方補助科目名",
            "貸方補助科目名",
        )
    )


def _template_date_ordinal(template: Mapping[str, Any]) -> int:
    value = str(template.get("伝票日付", "") or "").strip()
    for date_format in ("%Y%m%d", "%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, date_format).toordinal()
        except ValueError:
            continue
    return datetime.min.toordinal()


def resolve_receivable_epson_template(
    prepared_journal: Mapping[str, Any],
    transactions_snapshot: Sequence[Mapping[str, Any]],
    *,
    customer_name: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve one template without fuzzy matching or input-order tie-breaks."""

    candidates = [
        row for row in transactions_snapshot
        if _matches_structure(prepared_journal, row)
    ]
    if not candidates:
        raise ReceivableEpsonMaterializationError(
            TEMPLATE_NOT_FOUND,
            "一致するEPSON templateがありません。",
        )

    signatures = {
        build_critical_metadata_signature(candidate)
        for candidate in candidates
    }
    if len(signatures) != 1:
        raise ReceivableEpsonMaterializationError(
            TEMPLATE_AMBIGUOUS,
            "一致するEPSON templateのmetadataが一意ではありません。",
        )

    def rank(candidate: Mapping[str, Any]) -> tuple[int, int, int, str]:
        return (
            -int(_has_exact_normalized_match(customer_name, candidate)),
            -int(_has_exact_normalized_match(
                prepared_journal.get("summary", ""), candidate
            )),
            -_template_date_ordinal(candidate),
            build_template_row_hash(candidate),
        )

    selected = min(candidates, key=rank)
    selected_hash = build_template_row_hash(selected)
    return dict(selected), {
        "candidate_count": len(candidates),
        "critical_signature": next(iter(signatures)),
        "selected_template_hash": selected_hash,
        "selected_template_date": selected["伝票日付"],
        "customer_exact_match": _has_exact_normalized_match(
            customer_name, selected
        ),
        "summary_exact_match": _has_exact_normalized_match(
            prepared_journal.get("summary", ""), selected
        ),
    }


def materialize_receivable_epson_items(
    items: Sequence[Mapping[str, Any]],
    transactions_snapshot: Sequence[Mapping[str, Any]],
    *,
    customer_name: str = "",
    settlement_date: str = "",
) -> list[dict[str, Any]]:
    """Materialize a complete batch, returning nothing unless every row succeeds."""

    del settlement_date  # Settlement date never participates in selection.
    if not items or isinstance(items, (str, bytes)):
        raise ReceivableEpsonMaterializationError(
            PREPARED_JOURNAL_INVALID,
            "EPSON materializationの対象がありません。",
        )

    templates = _validated_template_snapshot(transactions_snapshot)
    materialized: list[dict[str, Any]] = []
    for item_number, item in enumerate(items, start=1):
        if not isinstance(item, Mapping):
            raise ReceivableEpsonMaterializationError(
                PREPARED_JOURNAL_INVALID,
                f"{item_number}件目の登録予定が不正です。",
                item_number=item_number,
            )
        prepared = _validated_prepared_journal(item, item_number)
        try:
            template, diagnostics = resolve_receivable_epson_template(
                prepared,
                templates,
                customer_name=customer_name,
            )
        except ReceivableEpsonMaterializationError as exc:
            if exc.item_number is None:
                exc.item_number = item_number
            raise

        epson_base_row = dict(template)
        epson_base_row.update(build_epson_edit_values(prepared))
        materialized.append({
            "prepared_journal": prepared,
            "epson_base_row": epson_base_row,
            "registration_id": build_registration_id(
                prepared,
                epson_base_row,
            ),
            "template_diagnostics": diagnostics,
        })
    return materialized

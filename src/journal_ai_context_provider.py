"""Read-only projection of the current journal search results for future AI use."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from ai_context_envelope import ContextEnvelope, make_context_envelope
from engine import load_data, to_int
from journal_search_service import search_journals


_ROW_FIELDS = {
    "date": "伝票日付",
    "debit_account_code": "借方科目",
    "debit_account_name": "借方科目名",
    "debit_sub_code": "借方補助",
    "debit_sub_name": "借方補助科目名",
    "debit_department_code": "借方部門",
    "debit_department_name": "借方部門名",
    "credit_account_code": "貸方科目",
    "credit_account_name": "貸方科目名",
    "credit_sub_code": "貸方補助",
    "credit_sub_name": "貸方補助科目名",
    "credit_department_code": "貸方部門",
    "credit_department_name": "貸方部門名",
    "debit_amount": "借方金額",
    "credit_amount": "貸方金額",
    "summary": "摘要",
    "voucher_summary": "伝票摘要",
}

_DRAFT_FIELDS = {
    "voucher_date": "voucherDate",
    "voucher_no": "voucherNo",
    "voucher_summary": "voucherSummary",
    "debit_account_code": "debitAccountCode",
    "debit_account_name": "debitAccountName",
    "debit_sub_code": "debitSubCode",
    "debit_sub_name": "debitSubName",
    "debit_department_code": "debitDeptCode",
    "debit_department_name": "debitDeptName",
    "credit_account_code": "creditAccountCode",
    "credit_account_name": "creditAccountName",
    "credit_sub_code": "creditSubCode",
    "credit_sub_name": "creditSubName",
    "credit_department_code": "creditDeptCode",
    "credit_department_name": "creditDeptName",
    "amount": "amount",
    "debit_amount": "debitAmount",
    "credit_amount": "creditAmount",
    "summary": "summary",
}

_MATCHED_FIELDS = {
    "match_type": "match_type",
    "date": "date",
    "debit_account_code": "debit_code",
    "debit_account_name": "debit",
    "credit_account_code": "credit_code",
    "credit_account_name": "credit",
    "debit_sub_name": "debit_sub",
    "credit_sub_name": "credit_sub",
    "amount": "amount",
    "input_amount": "input_amount",
    "diff": "diff",
    "diff_rate": "diff_rate",
    "summary": "summary",
}


def _project_row(row: Mapping[str, Any]) -> dict[str, Any]:
    projected = {key: row.get(column, "") for key, column in _ROW_FIELDS.items()}
    projected["amount"] = (
        projected["debit_amount"] if to_int(projected["debit_amount"])
        else projected["credit_amount"] or projected["debit_amount"]
    )
    return projected


def _project_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    source_rows = candidate["source_rows"]
    matched = candidate.get("matched_amount_row")
    return {
        "rank": candidate["rank"],
        "score": candidate["score"],
        "search_reason": candidate["search_reason"],
        "is_multi_line": len(source_rows) > 1,
        "is_complex": candidate["is_complex"],
        "has_fukugo": candidate["has_fukugo"],
        "has_sundry": candidate["has_sundry"],
        "row_count": len(source_rows),
        "rows": [_project_row(row) for row in source_rows],
        "matched_amount": (
            {key: matched.get(field) for key, field in _MATCHED_FIELDS.items()}
            if matched else None
        ),
    }


def build_journal_ai_context(
    *,
    keyword: str,
    department: str | None = None,
    amount: int | None = None,
    limit: int = 5,
    draft: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> ContextEnvelope:
    """Re-search the current DB; the browser's candidates are never authoritative."""
    now = now if now is not None else datetime.now().astimezone()
    records, _, frequency = load_data()
    result = search_journals(
        records, frequency, keyword=keyword, department=department,
        amount=amount, limit=limit,
    )
    current_draft = (
        {"source": "frontend_unregistered", **{
            key: draft[field] for key, field in _DRAFT_FIELDS.items()
        }} if draft is not None else None
    )
    return make_context_envelope("journal", {
        "query": result["query"],
        "current_draft": current_draft,
        "candidate_count": result["count"],
        "candidates": [_project_candidate(item) for item in result["candidates"]],
    }, now=now)

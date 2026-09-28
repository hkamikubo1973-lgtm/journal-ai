"""Current receivables for future read-only AI explanation, never settlement."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from ai_context_envelope import ContextEnvelope, make_context_envelope
from receivable_cleanup_service import (
    partition_receivable_cleanup_rows,
    summarize_receivable_cleanup_rows,
)
from receivable_persistence_service import (
    DEFAULT_RECEIVABLES_DIRECTORY,
    read_receivable_current_when_ready,
)
from receivable_query_service import (
    extract_active_receivables,
    serialize_receivable_item,
)


def _context_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "customer_code": item["code"],
        "customer_name": item["customer_name"],
        "invoice_date": item["billing_date"],
        "payment_due_date": item["planned_payment_date"],
        "receivable_account": item["receivable_account"],
        "receivable_subaccount": item["receivable_sub_account"],
        "department": item["department"],
        "summary": item["summary"],
        "invoice_amount": item["billed_amount"],
        "paid_amount": item["paid_amount"],
        "balance": item["balance"],
        "status": item["status"],
    }


def _customer_totals(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep first appearance in current order; do not assign priority."""
    customers: dict[str, dict[str, Any]] = {}
    for item in items:
        name = item["customer_name"]
        group = customers.setdefault(name, {
            "customer_name": name,
            "item_count": 0,
            "invoice_amount_total": 0,
            "paid_amount_total": 0,
            "balance_total": 0,
        })
        group["item_count"] += 1
        group["invoice_amount_total"] += item["invoice_amount"]
        group["paid_amount_total"] += item["paid_amount"]
        group["balance_total"] += item["balance"]
    return list(customers.values())


def build_receivable_ai_context(
    receivables_directory: Path = DEFAULT_RECEIVABLES_DIRECTORY,
    *,
    now: datetime | None = None,
) -> ContextEnvelope:
    """Project one readiness-checked current snapshot; history is not loaded."""
    now = now if now is not None else datetime.now().astimezone()
    loaded = read_receivable_current_when_ready(receivables_directory)
    current = loaded.dataframe
    retained, _ = partition_receivable_cleanup_rows(current)
    active = extract_active_receivables(retained)
    all_rows = [serialize_receivable_item(row) for row in current.to_dict("records")]
    items = [
        _context_item(serialize_receivable_item(row))
        for row in active.to_dict("records")
    ]
    cleanup = summarize_receivable_cleanup_rows(current)
    return make_context_envelope("receivable", {
        "total_count": cleanup["current_count"],
        "outstanding_count": len(items),
        "cleanup_target_count": cleanup["cleanup_target_count"],
        "total_invoice_amount": sum(row["billed_amount"] for row in all_rows),
        "total_paid_amount": sum(row["paid_amount"] for row in all_rows),
        "total_balance": sum(row["balance"] for row in all_rows),
        "customers": _customer_totals(items),
        "items": items,
    }, now=now)

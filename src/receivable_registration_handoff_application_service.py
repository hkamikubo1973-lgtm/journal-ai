"""Read-only orchestration for receivable registration handoff."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from receivable_receipt_service import read_receivable_settlement_receipt
from receivable_registration_handoff_service import (
    build_receivable_registration_handoff_items,
)


def build_receivable_registration_handoff_application_result(
    receivables_directory: str | Path,
    *,
    settlement_id: str,
    receipt_ref: str,
    journal_master_snapshot: Any,
) -> dict[str, Any]:
    """Load one trusted receipt and convert all rows using current masters."""

    receipt = read_receivable_settlement_receipt(
        receivables_directory,
        receipt_ref,
        expected_settlement_id=settlement_id,
    )
    items = build_receivable_registration_handoff_items(
        receipt.settlement,
        receipt.receipt_ref,
        account_master_snapshot=journal_master_snapshot,
        sub_account_relation_snapshot=journal_master_snapshot,
        department_master_snapshot=journal_master_snapshot,
    )
    return {
        "settlement_id": receipt.settlement_id,
        "receipt_ref": receipt.receipt_ref,
        "settlement_date": receipt.settlement["settlement_date"],
        "customer_name": receipt.settlement["customer_name"],
        "row_count": len(items),
        "items": items,
    }

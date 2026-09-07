"""Pure conversion from validated receivable settlements to journal handoff items."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
import json
from typing import Any

from receivable_account_validation_service import (
    ReceivableSettlementMasterValidationError,
    build_receivable_account_code_index,
    resolve_unique_receivable_account,
)


SETTLEMENT_ROW_ID_VERSION = "receivable-settlement-row-v1"


class ReceivableRegistrationHandoffValidationError(ValueError):
    """Raised when a complete settlement cannot be converted safely."""


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _snapshot_rows(
    snapshot: Any,
    *,
    collection_key: str,
    label: str,
) -> list[Mapping[str, Any]]:
    if isinstance(snapshot, Mapping):
        source = snapshot.get(collection_key)
    else:
        source = snapshot

    if not isinstance(source, Sequence) or isinstance(source, (str, bytes)):
        raise ReceivableRegistrationHandoffValidationError(
            f"{label} snapshot must contain a sequence of rows"
        )

    rows = list(source)
    if any(not isinstance(row, Mapping) for row in rows):
        raise ReceivableRegistrationHandoffValidationError(
            f"{label} snapshot contains a malformed row"
        )
    return rows


def _build_name_code_index(
    snapshot: Any,
    *,
    collection_key: str,
    label: str,
) -> dict[str, frozenset[str]]:
    codes_by_name: dict[str, set[str]] = {}
    for row in _snapshot_rows(
        snapshot,
        collection_key=collection_key,
        label=label,
    ):
        code = _text(row.get("code"))
        name = _text(row.get("name"))
        if not code or not name:
            raise ReceivableRegistrationHandoffValidationError(
                f"{label} row requires code and name"
            )
        codes_by_name.setdefault(name, set()).add(code)
    return {
        name: frozenset(codes)
        for name, codes in codes_by_name.items()
    }


def _build_sub_account_indexes(
    snapshot: Any,
) -> tuple[
    dict[tuple[str, str], frozenset[str]],
    dict[str, frozenset[str]],
]:
    codes_by_parent_and_name: dict[tuple[str, str], set[str]] = {}
    parents_by_name: dict[str, set[str]] = {}
    for row in _snapshot_rows(
        snapshot,
        collection_key="sub_account_relations",
        label="sub-account relation",
    ):
        account_code = _text(row.get("account_code"))
        sub_code = _text(row.get("sub_code"))
        sub_name = _text(row.get("sub_name"))
        if not account_code or not sub_code or not sub_name:
            raise ReceivableRegistrationHandoffValidationError(
                "sub-account relation row requires account_code, sub_code, "
                "and sub_name"
            )
        codes_by_parent_and_name.setdefault(
            (account_code, sub_name), set()
        ).add(sub_code)
        parents_by_name.setdefault(sub_name, set()).add(account_code)

    return (
        {
            key: frozenset(codes)
            for key, codes in codes_by_parent_and_name.items()
        },
        {
            name: frozenset(parent_codes)
            for name, parent_codes in parents_by_name.items()
        },
    )


def _resolve_account(
    codes_by_name: Mapping[str, frozenset[str]],
    value: Any,
    *,
    row_number: int,
    side: str,
) -> dict[str, str]:
    name = _text(value)
    resolved = resolve_unique_receivable_account(codes_by_name, name)
    if resolved is not None:
        return resolved

    codes = codes_by_name.get(name, frozenset())
    if not name:
        reason = "is required"
    elif not codes:
        reason = f"does not exist in the account master: {name}"
    else:
        reason = f"is ambiguous in the account master: {name}"
    raise ReceivableRegistrationHandoffValidationError(
        f"row {row_number} {side} account {reason}"
    )


def _resolve_department(
    codes_by_name: Mapping[str, frozenset[str]],
    value: Any,
    *,
    row_number: int,
) -> dict[str, str]:
    name = _text(value)
    if not name:
        return {"code": "", "name": ""}

    codes = codes_by_name.get(name, frozenset())
    if not codes:
        reason = f"does not exist in the department master: {name}"
    elif len(codes) != 1:
        reason = f"is ambiguous in the department master: {name}"
    else:
        return {"code": next(iter(codes)), "name": name}
    raise ReceivableRegistrationHandoffValidationError(
        f"row {row_number} department {reason}"
    )


def _resolve_credit_sub_account(
    codes_by_parent_and_name: Mapping[
        tuple[str, str], frozenset[str]
    ],
    parents_by_name: Mapping[str, frozenset[str]],
    value: Any,
    *,
    credit_account_code: str,
    row_number: int,
) -> dict[str, str]:
    name = _text(value)
    if not name:
        return {"code": "", "name": ""}

    codes = codes_by_parent_and_name.get(
        (credit_account_code, name), frozenset()
    )
    if len(codes) == 1:
        return {"code": next(iter(codes)), "name": name}
    if len(codes) > 1:
        reason = f"is ambiguous for account {credit_account_code}: {name}"
    elif name in parents_by_name:
        reason = (
            f"does not belong to account {credit_account_code}: {name}"
        )
    else:
        reason = f"does not exist in the relation master: {name}"
    raise ReceivableRegistrationHandoffValidationError(
        f"row {row_number} credit sub-account {reason}"
    )


def build_settlement_row_id(
    receipt_ref: str,
    settlement_id: str,
    row_index: int,
) -> str:
    """Build a deterministic lowercase SHA-256 ID from canonical JSON."""

    material = [
        SETTLEMENT_ROW_ID_VERSION,
        receipt_ref,
        settlement_id,
        row_index,
    ]
    canonical = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _build_handoff_item(
    row: Mapping[str, Any],
    *,
    row_index: int,
    row_count: int,
    settlement_id: str,
    voucher_date: str,
    receipt_ref: str,
    account_codes_by_name: Mapping[str, frozenset[str]],
    department_codes_by_name: Mapping[str, frozenset[str]],
    sub_codes_by_parent_and_name: Mapping[
        tuple[str, str], frozenset[str]
    ],
    sub_parents_by_name: Mapping[str, frozenset[str]],
) -> dict[str, Any]:
    row_number = row_index + 1
    required_fields = {
        "借方科目", "貸方科目", "貸方補助", "部門", "金額", "摘要"
    }
    missing_fields = sorted(required_fields.difference(row))
    if missing_fields:
        raise ReceivableRegistrationHandoffValidationError(
            f"row {row_number} is missing fields: "
            + ", ".join(missing_fields)
        )

    amount = row.get("金額")
    if type(amount) is not int or amount <= 0:
        raise ReceivableRegistrationHandoffValidationError(
            f"row {row_number} amount must be a positive integer"
        )
    summary = row.get("摘要")
    if not isinstance(summary, str):
        raise ReceivableRegistrationHandoffValidationError(
            f"row {row_number} summary must be a string"
        )

    debit_account = _resolve_account(
        account_codes_by_name,
        row.get("借方科目"),
        row_number=row_number,
        side="debit",
    )
    credit_account = _resolve_account(
        account_codes_by_name,
        row.get("貸方科目"),
        row_number=row_number,
        side="credit",
    )
    credit_sub = _resolve_credit_sub_account(
        sub_codes_by_parent_and_name,
        sub_parents_by_name,
        row.get("貸方補助"),
        credit_account_code=credit_account["code"],
        row_number=row_number,
    )
    credit_department = _resolve_department(
        department_codes_by_name,
        row.get("部門"),
        row_number=row_number,
    )

    prepared_journal = {
        "voucher_date": voucher_date,
        "voucher_no": settlement_id,
        "voucher_summary": "",
        "debit_account_code": debit_account["code"],
        "debit_account_name": debit_account["name"],
        "debit_sub_code": "",
        "debit_sub_name": "",
        "debit_dept_code": "",
        "debit_dept_name": "",
        "credit_account_code": credit_account["code"],
        "credit_account_name": credit_account["name"],
        "credit_sub_code": credit_sub["code"],
        "credit_sub_name": credit_sub["name"],
        "credit_dept_code": credit_department["code"],
        "credit_dept_name": credit_department["name"],
        "amount": amount,
        "summary": summary,
        "source_debit_amount": str(amount),
        "source_credit_amount": str(amount),
    }
    return {
        "source_type": "receivable_settlement",
        "prepared_journal": prepared_journal,
        "provenance": {
            "settlement_id": settlement_id,
            "receipt_ref": receipt_ref,
            "row_index": row_index,
            "row_count": row_count,
            "settlement_row_id": build_settlement_row_id(
                receipt_ref,
                settlement_id,
                row_index,
            ),
        },
        "print_metadata": {"print_category": ""},
        "print_warnings": ["伝票摘要なし"],
        "epson_capability": {"status": "needs_template"},
    }


def build_receivable_registration_handoff_items(
    settlement: Mapping[str, Any],
    receipt_ref: str,
    account_master_snapshot: Any,
    sub_account_relation_snapshot: Any,
    department_master_snapshot: Any,
) -> list[dict[str, Any]]:
    """Convert every receipt row in order, returning only after all validate."""

    if not isinstance(settlement, Mapping):
        raise ReceivableRegistrationHandoffValidationError(
            "settlement must be an object"
        )
    settlement_id = settlement.get("settlement_id")
    if not isinstance(settlement_id, str) or not settlement_id:
        raise ReceivableRegistrationHandoffValidationError(
            "settlement_id must be a non-empty string"
        )
    if not isinstance(receipt_ref, str) or not receipt_ref:
        raise ReceivableRegistrationHandoffValidationError(
            "receipt_ref must be a non-empty string"
        )

    settlement_date = settlement.get("settlement_date")
    if not isinstance(settlement_date, str):
        raise ReceivableRegistrationHandoffValidationError(
            "settlement_date must be an ISO date"
        )
    try:
        from datetime import date

        parsed_date = date.fromisoformat(settlement_date)
    except ValueError as exc:
        raise ReceivableRegistrationHandoffValidationError(
            "settlement_date must be an ISO date"
        ) from exc
    voucher_date = parsed_date.strftime("%Y%m%d")

    rows = settlement.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ReceivableRegistrationHandoffValidationError(
            "settlement rows must be a non-empty list"
        )
    if any(not isinstance(row, Mapping) for row in rows):
        raise ReceivableRegistrationHandoffValidationError(
            "settlement rows contain a malformed row"
        )

    try:
        account_codes_by_name = build_receivable_account_code_index(
            account_master_snapshot,
            strict=True,
        )
    except ReceivableSettlementMasterValidationError as exc:
        raise ReceivableRegistrationHandoffValidationError(str(exc)) from exc
    department_codes_by_name = _build_name_code_index(
        department_master_snapshot,
        collection_key="departments",
        label="department master",
    )
    (
        sub_codes_by_parent_and_name,
        sub_parents_by_name,
    ) = _build_sub_account_indexes(sub_account_relation_snapshot)

    row_count = len(rows)
    items = [
        _build_handoff_item(
            row,
            row_index=row_index,
            row_count=row_count,
            settlement_id=settlement_id,
            voucher_date=voucher_date,
            receipt_ref=receipt_ref,
            account_codes_by_name=account_codes_by_name,
            department_codes_by_name=department_codes_by_name,
            sub_codes_by_parent_and_name=sub_codes_by_parent_and_name,
            sub_parents_by_name=sub_parents_by_name,
        )
        for row_index, row in enumerate(rows)
    ]
    return items

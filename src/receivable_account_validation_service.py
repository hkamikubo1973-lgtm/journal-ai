"""Shared pure account-master resolution for receivable workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from receivable_settlement_service import ReceivableSettlementValidationError


class ReceivableSettlementMasterValidationError(
    ReceivableSettlementValidationError
):
    """Raised when a selected account is invalid in a supplied snapshot."""


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def normalize_receivable_account_master(
    account_master_snapshot: Any,
    *,
    strict: bool = True,
) -> list[dict[str, str]]:
    """Return detached code/name/category rows from supported master shapes."""

    if isinstance(account_master_snapshot, pd.DataFrame):
        source_rows: Any = account_master_snapshot.to_dict("records")
    elif isinstance(account_master_snapshot, Mapping):
        if "accounts" in account_master_snapshot:
            source_rows = account_master_snapshot.get("accounts")
        else:
            source_rows = [
                {"name": name, "code": code}
                for name, code in account_master_snapshot.items()
            ]
    elif isinstance(account_master_snapshot, Sequence) and not isinstance(
        account_master_snapshot, (str, bytes)
    ):
        source_rows = account_master_snapshot
    else:
        if strict:
            raise ReceivableSettlementMasterValidationError(
                "account master snapshot must contain account rows"
            )
        return []

    if not isinstance(source_rows, Sequence) or isinstance(
        source_rows, (str, bytes)
    ):
        if strict:
            raise ReceivableSettlementMasterValidationError(
                "account master accounts must be a sequence"
            )
        return []

    rows: list[dict[str, str]] = []
    for source in list(source_rows):
        if not isinstance(source, Mapping):
            if strict:
                raise ReceivableSettlementMasterValidationError(
                    "account master contains a malformed row"
                )
            continue
        code = _text(source.get("code"))
        name = _text(source.get("name"))
        if not code or not name:
            if strict:
                raise ReceivableSettlementMasterValidationError(
                    "account master row requires code and name"
                )
            continue
        rows.append({
            "code": code,
            "name": name,
            "category": _text(source.get("category")),
        })
    return rows


def build_receivable_account_code_index(
    account_master_snapshot: Any,
    *,
    strict: bool = True,
) -> dict[str, frozenset[str]]:
    """Build an immutable name-to-distinct-codes index."""

    codes_by_name: dict[str, set[str]] = {}
    for row in normalize_receivable_account_master(
        account_master_snapshot,
        strict=strict,
    ):
        codes_by_name.setdefault(row["name"], set()).add(row["code"])
    return {
        name: frozenset(codes)
        for name, codes in codes_by_name.items()
    }


def resolve_unique_receivable_account(
    codes_by_name: Mapping[str, frozenset[str] | set[str]],
    account_name: Any,
) -> dict[str, str] | None:
    """Resolve a name only when exactly one distinct master code exists."""

    name = _text(account_name)
    codes = codes_by_name.get(name, frozenset())
    if not name or len(codes) != 1:
        return None
    return {"code": next(iter(codes)), "name": name}


def validate_receivable_settlement_accounts(
    account_master_snapshot: Any,
    *,
    receipt_account: Any,
    difference_account: Any,
    difference_required: bool,
) -> None:
    """Validate name-only settlement intent against one master snapshot."""

    codes_by_name = build_receivable_account_code_index(
        account_master_snapshot,
        strict=True,
    )

    def require_unique_account(value: Any, label: str) -> None:
        name = _text(value)
        if not name:
            raise ReceivableSettlementMasterValidationError(
                f"{label} is required"
            )
        codes = codes_by_name.get(name)
        if not codes:
            raise ReceivableSettlementMasterValidationError(
                f"{label} does not exist in the account master: {name}"
            )
        if len(codes) != 1:
            raise ReceivableSettlementMasterValidationError(
                f"{label} is ambiguous in the account master: {name}"
            )

    require_unique_account(receipt_account, "receipt account")
    if difference_required:
        require_unique_account(difference_account, "difference account")

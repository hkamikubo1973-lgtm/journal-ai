"""Pure domain planning for receivable settlements.

This module rebuilds a settlement from a current-ledger snapshot and caller
intent.  It never reads or writes files and never commits a transaction.
"""

from __future__ import annotations

import copy
import io
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pandas as pd

from receivable_engine import CURRENT_RECEIVABLE_COLUMNS, HISTORY_COLUMNS
from receivable_preview_service import build_receivable_preview


class ReceivableSettlementDomainError(ValueError):
    """Base error for invalid settlement intent or stale ledger state."""


class ReceivableSettlementValidationError(ReceivableSettlementDomainError):
    """Raised when caller intent is invalid."""


class ReceivableSettlementConflictError(ReceivableSettlementDomainError):
    """Raised when the supplied snapshot cannot safely accept the plan."""


@dataclass(frozen=True)
class ReceivableSettlementPlan:
    """A deterministic plan ready for persistence orchestration."""

    current_after: pd.DataFrame
    history_after: pd.DataFrame
    current_after_bytes: bytes
    history_after_bytes: bytes
    preview: dict[str, Any]
    settlement: dict[str, Any]


def _snapshot_dataframe(
    snapshot: pd.DataFrame | Sequence[Mapping[str, Any]],
) -> pd.DataFrame:
    if isinstance(snapshot, pd.DataFrame):
        return snapshot.copy(deep=True)
    if isinstance(snapshot, (str, bytes)) or not isinstance(snapshot, Sequence):
        raise ReceivableSettlementValidationError(
            "snapshot must be a DataFrame or a sequence of rows"
        )
    return pd.DataFrame(copy.deepcopy(list(snapshot)))


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReceivableSettlementValidationError(
            f"{field} must be a non-empty string"
        )
    return value.strip()


def _parse_integer(value: Any, field: str) -> int:
    try:
        text = str(value).replace(",", "").strip()
        if not text:
            raise ValueError
        return int(text)
    except (TypeError, ValueError) as exc:
        raise ReceivableSettlementConflictError(
            f"{field} is not a valid integer"
        ) from exc


def _normalize_settlement_date(value: Any) -> tuple[str, str]:
    if isinstance(value, datetime):
        normalized = value.date()
    elif isinstance(value, date):
        normalized = value
    else:
        text = str(value or "").strip()[:10].replace("/", "-")
        try:
            normalized = date.fromisoformat(text)
        except ValueError as exc:
            raise ReceivableSettlementValidationError(
                "settlement_date must be a valid date"
            ) from exc
    return normalized.isoformat(), normalized.strftime("%Y/%m/%d")


def _normalize_created_at(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ReceivableSettlementValidationError(
        "created_at must be an injected date, datetime, or non-empty string"
    )


def _account_names(
    account_master_snapshot: Any,
) -> set[str]:
    if account_master_snapshot is None:
        return set()
    if isinstance(account_master_snapshot, pd.DataFrame):
        records: Any = account_master_snapshot.to_dict("records")
    elif isinstance(account_master_snapshot, Mapping):
        records = list(account_master_snapshot.keys())
    elif isinstance(account_master_snapshot, Sequence) and not isinstance(
        account_master_snapshot, (str, bytes)
    ):
        records = account_master_snapshot
    else:
        raise ReceivableSettlementValidationError(
            "account_master_snapshot has an unsupported shape"
        )

    names: set[str] = set()
    for item in records:
        if isinstance(item, str):
            if item.strip():
                names.add(item.strip())
            continue
        if not isinstance(item, Mapping):
            continue
        for field in ("科目名", "勘定科目名", "名称", "name"):
            value = item.get(field)
            if isinstance(value, str) and value.strip():
                names.add(value.strip())
    return names


def _validate_accounts(
    receipt_account: str,
    difference_account: str | None,
    account_master_snapshot: Any,
) -> None:
    if account_master_snapshot is None:
        return
    names = _account_names(account_master_snapshot)
    required = [receipt_account]
    if difference_account:
        required.append(difference_account)
    missing = [account for account in required if account not in names]
    if missing:
        raise ReceivableSettlementValidationError(
            "account does not exist in the supplied master: "
            + ", ".join(missing)
        )


def _prepare_history_snapshot(
    history_snapshot: pd.DataFrame | Sequence[Mapping[str, Any]],
) -> pd.DataFrame:
    history = _snapshot_dataframe(history_snapshot)
    if history.empty and not len(history.columns):
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    missing = [column for column in HISTORY_COLUMNS if column not in history]
    if missing:
        raise ReceivableSettlementValidationError(
            "history snapshot is missing columns: " + ", ".join(missing)
        )
    return history.loc[:, HISTORY_COLUMNS].copy(deep=True)


def _locate_candidate_row(
    current: pd.DataFrame,
    candidate: Mapping[str, Any],
) -> Any:
    code = str(candidate.get("コード", ""))
    receivable_id = str(candidate.get("未収ID", "") or "").strip()

    if receivable_id and "未収ID" in current.columns:
        ids = current["未収ID"].fillna("").astype(str)
        matches = current.index[ids == receivable_id]
        identifier = f"未収ID={receivable_id}"
    else:
        codes = current["コード"].fillna("").astype(str)
        matches = current.index[codes == code]
        identifier = f"コード={code}"

    if len(matches) != 1:
        raise ReceivableSettlementConflictError(
            f"receivable row is not unique: {identifier}"
        )
    return matches[0]


def _apply_candidates_pure(
    current_snapshot: pd.DataFrame,
    candidates: Sequence[Mapping[str, Any]],
    customer_name: str,
    settlement_id: str,
    history_date: str,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    current_after = current_snapshot.copy(deep=True)
    history_rows: list[dict[str, Any]] = []
    used_indexes: set[Any] = set()

    for candidate in candidates:
        index = _locate_candidate_row(current_after, candidate)
        if index in used_indexes:
            raise ReceivableSettlementConflictError(
                "the same receivable row was selected more than once"
            )
        used_indexes.add(index)

        actual_customer = str(current_after.at[index, "得意先名"])
        if actual_customer != customer_name:
            raise ReceivableSettlementConflictError(
                "receivable customer no longer matches the settlement"
            )

        status = str(current_after.at[index, "ステータス"]).strip()
        current_balance = _parse_integer(
            current_after.at[index, "残高"], "current balance"
        )
        scheduled_amount = _parse_integer(
            candidate.get("消込予定"), "scheduled amount"
        )
        if current_balance <= 0 or status == "完了":
            raise ReceivableSettlementConflictError(
                "receivable is already completed"
            )
        if scheduled_amount <= 0:
            raise ReceivableSettlementValidationError(
                "scheduled amount must be greater than zero"
            )
        if scheduled_amount > current_balance:
            raise ReceivableSettlementConflictError(
                "scheduled amount exceeds the current balance"
            )

        new_balance = current_balance - scheduled_amount
        code = str(current_after.at[index, "コード"])
        history_rows.append(
            {
                "消込ID": settlement_id,
                "消込日": history_date,
                "得意先名": actual_customer,
                "コード": code,
                "消込額": str(scheduled_amount),
                "消込前残高": str(current_balance),
                "消込後残高": str(new_balance),
                "仕訳登録済": "0",
            }
        )
        current_after.at[index, "残高"] = str(new_balance)
        current_after.at[index, "ステータス"] = (
            "完了" if new_balance == 0 else "部分消込"
        )

    return current_after, history_rows


def serialize_receivable_dataframe(dataframe: pd.DataFrame) -> bytes:
    """Serialize with the existing CSV contract without touching a path."""

    buffer = io.BytesIO()
    dataframe.to_csv(buffer, index=False, encoding="utf-8-sig")
    return buffer.getvalue()


def _json_safe(value: Any, path: str = "settlement") -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ReceivableSettlementValidationError(
                    f"{path} contains a non-string key"
                )
            result[key] = _json_safe(item, f"{path}.{key}")
        return result
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, f"{path}[]") for item in value]
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item(), path)
        except (TypeError, ValueError):
            pass
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ReceivableSettlementValidationError(
                f"{path} contains NaN or infinity"
            )
        return value
    raise ReceivableSettlementValidationError(
        f"{path} contains a non-JSON-safe value"
    )


def build_receivable_settlement_plan(
    current_snapshot: pd.DataFrame | Sequence[Mapping[str, Any]],
    history_snapshot: pd.DataFrame | Sequence[Mapping[str, Any]],
    *,
    customer_name: str,
    settlement_date: Any,
    payment_amount: Any,
    receipt_account: str,
    mode: str | None,
    difference_account: str | None,
    difference_summary: str | None,
    settlement_id: str,
    created_at: Any,
    account_master_snapshot: Any = None,
) -> ReceivableSettlementPlan:
    """Rebuild Preview and generate deterministic current/history after state."""

    customer = _require_text(customer_name, "customer_name")
    receipt = _require_text(receipt_account, "receipt_account")
    identifier = _require_text(settlement_id, "settlement_id")
    dto_date, history_date = _normalize_settlement_date(settlement_date)
    dto_created_at = _normalize_created_at(created_at)
    _validate_accounts(receipt, difference_account, account_master_snapshot)

    current = _snapshot_dataframe(current_snapshot)
    missing = [column for column in CURRENT_RECEIVABLE_COLUMNS if column not in current]
    if missing:
        raise ReceivableSettlementValidationError(
            "current snapshot is missing columns: " + ", ".join(missing)
        )
    history = _prepare_history_snapshot(history_snapshot)

    try:
        preview = build_receivable_preview(
            current,
            customer,
            payment_amount,
            settlement_date,
            receipt,
            mode,
            difference_account,
            difference_summary,
        )
    except ReceivableSettlementDomainError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise ReceivableSettlementValidationError(str(exc)) from exc

    candidates = copy.deepcopy(preview["source_candidates"])
    if not candidates:
        raise ReceivableSettlementValidationError(
            "customer has no unsettled receivables"
        )

    current_after, new_history_rows = _apply_candidates_pure(
        current, candidates, customer, identifier, history_date
    )
    new_history = pd.DataFrame(new_history_rows, columns=HISTORY_COLUMNS)
    history_after = pd.concat([history, new_history], ignore_index=True)

    settlement = _json_safe(
        {
            "settlement_id": identifier,
            "settlement_date": dto_date,
            "customer_name": customer,
            "payment_amount": preview["payment_amount"],
            "target_total": preview["target_total"],
            "difference": preview["difference"],
            "source_candidates": candidates,
            "rows": copy.deepcopy(preview["rows"]),
            "created_at": dto_created_at,
        }
    )
    safe_preview = _json_safe(copy.deepcopy(preview), "preview")

    return ReceivableSettlementPlan(
        current_after=current_after,
        history_after=history_after,
        current_after_bytes=serialize_receivable_dataframe(current_after),
        history_after_bytes=serialize_receivable_dataframe(history_after),
        preview=safe_preview,
        settlement=settlement,
    )

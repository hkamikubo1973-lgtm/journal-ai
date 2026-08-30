"""Pure read models for receivable summary and customer detail APIs."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from receivable_engine import CURRENT_RECEIVABLE_COLUMNS


class ReceivableCustomerNotFoundError(LookupError):
    """Raised when a customer has no active receivables."""


def _snapshot_dataframe(
    current_snapshot: pd.DataFrame | Sequence[Mapping[str, Any]],
) -> pd.DataFrame:
    if isinstance(current_snapshot, pd.DataFrame):
        dataframe = current_snapshot.copy(deep=True)
    elif isinstance(current_snapshot, Sequence) and not isinstance(
        current_snapshot, (str, bytes)
    ):
        dataframe = pd.DataFrame(copy.deepcopy(list(current_snapshot)))
    else:
        raise ValueError("current snapshot must contain receivable rows")

    missing = [
        column
        for column in CURRENT_RECEIVABLE_COLUMNS
        if column not in dataframe.columns
    ]
    if missing:
        raise ValueError(
            "current snapshot is missing columns: " + ", ".join(missing)
        )
    return dataframe.loc[:, CURRENT_RECEIVABLE_COLUMNS].copy(deep=True)


def _integer_series(values: pd.Series) -> pd.Series:
    return pd.to_numeric(
        values.astype(str).str.replace(",", "", regex=False),
        errors="coerce",
    ).fillna(0).astype(int)


def _integer_value(value: Any) -> int:
    converted = pd.to_numeric(
        str(value).replace(",", ""),
        errors="coerce",
    )
    if pd.isna(converted):
        return 0
    return int(converted)


def _text_value(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value)


def extract_active_receivables(
    current_snapshot: pd.DataFrame | Sequence[Mapping[str, Any]],
) -> pd.DataFrame:
    """Return active rows with current Streamlit filtering semantics."""

    dataframe = _snapshot_dataframe(current_snapshot)
    dataframe = dataframe[
        dataframe["得意先名"].astype(str).str.strip() != ""
    ].copy()
    dataframe["残高"] = _integer_series(dataframe["残高"])
    return dataframe[
        (dataframe["残高"] > 0)
        & (dataframe["ステータス"] != "完了")
    ].copy()


def build_receivable_summary(
    current_snapshot: pd.DataFrame | Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build a JSON-safe customer summary without changing its input."""

    active = extract_active_receivables(current_snapshot)
    if active.empty:
        return {
            "customer_count": 0,
            "outstanding_count": 0,
            "outstanding_balance": 0,
            "customers": [],
        }

    grouped = (
        active.groupby("得意先名", as_index=False, sort=False)
        .agg(
            outstanding_balance=("残高", "sum"),
            outstanding_count=("残高", "size"),
        )
        .sort_values(
            "outstanding_balance",
            ascending=False,
            kind="stable",
        )
    )
    customers = [
        {
            "customer_name": str(row["得意先名"]),
            "outstanding_count": int(row["outstanding_count"]),
            "outstanding_balance": int(row["outstanding_balance"]),
        }
        for _, row in grouped.iterrows()
    ]
    return {
        "customer_count": len(customers),
        "outstanding_count": int(len(active)),
        "outstanding_balance": int(active["残高"].sum()),
        "customers": customers,
    }


def _receivable_item(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "code": _text_value(row.get("コード")),
        "receivable_id": _text_value(row.get("未収ID")),
        "customer_name": _text_value(row.get("得意先名")),
        "billing_date": _text_value(row.get("請求日")),
        "planned_payment_date": _text_value(row.get("入金予定日")),
        "receivable_account": _text_value(row.get("未収科目")),
        "receivable_sub_account": _text_value(row.get("未収補助")),
        "department": _text_value(row.get("部門")),
        "summary": _text_value(row.get("摘要")),
        "billed_amount": _integer_value(row.get("請求金額")),
        "paid_amount": _integer_value(row.get("入金済額")),
        "balance": _integer_value(row.get("残高")),
        "status": _text_value(row.get("ステータス")),
    }


def build_receivable_customer_detail(
    current_snapshot: pd.DataFrame | Sequence[Mapping[str, Any]],
    customer_name: str,
) -> dict[str, Any]:
    """Build one customer's active rows in their original current order."""

    active = extract_active_receivables(current_snapshot)
    customer_rows = active[
        active["得意先名"] == customer_name
    ].copy()
    if customer_rows.empty:
        raise ReceivableCustomerNotFoundError(customer_name)

    receivables = [
        _receivable_item(row)
        for row in customer_rows.to_dict("records")
    ]
    return {
        "customer_name": customer_name,
        "outstanding_count": len(receivables),
        "outstanding_balance": int(customer_rows["残高"].sum()),
        "receivables": receivables,
    }

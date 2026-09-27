"""The existing six-field receivable import duplicate identity."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping


BILLING_DUPLICATE_COLUMNS = [
    "コード", "得意先名", "請求日", "請求金額", "未収科目", "未収補助",
]


def receivable_duplicate_key(
    row: Mapping[str, Any], columns: list[str] = BILLING_DUPLICATE_COLUMNS,
) -> tuple[str, ...]:
    """Retain the original trim/date/amount normalization exactly."""

    values = []
    for column in columns:
        value = str(row.get(column, "")).strip()
        if column in ["請求日", "入金予定日"]:
            normalized_date = None
            try:
                if len(value) == 8 and value.isdigit():
                    normalized_date = datetime.strptime(value, "%Y%m%d")
                else:
                    date_parts = value.replace("/", "-").split("-")
                    if len(date_parts) == 3:
                        normalized_date = datetime(
                            int(date_parts[0]), int(date_parts[1]), int(date_parts[2])
                        )
            except ValueError:
                normalized_date = None
            if normalized_date is not None:
                value = normalized_date.strftime("%Y-%m-%d")
        if column in ["請求金額", "残高"]:
            value = value.replace(",", "")
        values.append(value)
    return tuple(values)

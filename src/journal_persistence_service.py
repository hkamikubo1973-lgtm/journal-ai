"""通常仕訳バッチの既存重複確認と検索DB登録を共有する。"""

from __future__ import annotations

from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any, Callable
import unicodedata

import pandas as pd

from columns import (
    COL_CREDIT,
    COL_CREDIT_AMOUNT,
    COL_CREDIT_SUB,
    COL_DATE,
    COL_DEBIT,
    COL_DEBIT_AMOUNT,
    COL_DEBIT_SUB,
    COL_SUMMARY,
)
from engine import update_search_csv


TRANSACTIONS_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "transactions.csv"
)
NORMAL_JOURNAL_BATCH_COLUMNS = [
    COL_DATE,
    "借方科目",
    COL_DEBIT,
    "借方補助",
    COL_DEBIT_SUB,
    COL_DEBIT_AMOUNT,
    "貸方科目",
    COL_CREDIT,
    "貸方補助",
    COL_CREDIT_SUB,
    COL_CREDIT_AMOUNT,
    COL_SUMMARY,
    "伝票摘要",
    "入力会社",
]


def normalize_normal_journal_batch_value(column: str, value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = " ".join(normalized.split())

    if column == COL_DATE:
        normalized = normalized.replace("/", "").replace("-", "")
    if column in {COL_DEBIT_AMOUNT, COL_CREDIT_AMOUNT}:
        normalized = normalized.replace(",", "")
    return normalized


def normal_journal_batch_row_key(row: Any) -> tuple[str, ...]:
    return tuple(
        normalize_normal_journal_batch_value(
            column,
            row.get(column, ""),
        )
        for column in NORMAL_JOURNAL_BATCH_COLUMNS
    )


def build_normal_journal_batch_id(rows: list[dict[str, Any]]) -> str:
    row_keys = sorted(
        normal_journal_batch_row_key(row)
        for row in (rows or [])
        if isinstance(row, dict)
    )
    serialized_rows = json.dumps(
        row_keys,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized_rows.encode("utf-8")).hexdigest()


def load_transactions_df(
    transactions_path: str | Path = TRANSACTIONS_PATH,
) -> pd.DataFrame:
    return pd.read_csv(
        transactions_path,
        dtype=str,
        encoding="utf-8-sig",
    ).fillna("")


def is_normal_journal_batch_in_transactions(
    rows: list[dict[str, Any]],
    transactions_path: str | Path = TRANSACTIONS_PATH,
) -> bool:
    target_keys = Counter(
        normal_journal_batch_row_key(row)
        for row in (rows or [])
        if isinstance(row, dict)
    )
    if not target_keys:
        return False

    existing_df = load_transactions_df(transactions_path)
    existing_keys = Counter(
        normal_journal_batch_row_key(row)
        for _, row in existing_df.iterrows()
    )
    return all(
        existing_keys[key] >= count
        for key, count in target_keys.items()
    )


def register_epson_rows_to_search_db(
    registered_rows: list[dict[str, Any]],
    transactions_path: str | Path = TRANSACTIONS_PATH,
    *,
    today: date | None = None,
    start_month: int | None = None,
    db_updater: Callable[..., Any] | None = None,
) -> tuple[bool, int | str]:
    rows = [
        row for row in (registered_rows or [])
        if isinstance(row, dict)
    ]
    if not rows:
        return False, "登録対象の仕訳がありません"

    updater = db_updater or update_search_csv
    try:
        before_count = len(load_transactions_df(transactions_path))
        updater(
            [rows],
            output_path=str(transactions_path),
            today=today,
            start_month=start_month,
        )
        after_count = len(load_transactions_df(transactions_path))
    except Exception as error:
        return False, str(error)

    appended_count = after_count - before_count
    if appended_count <= 0:
        return False, "transactions.csvへの追記を確認できませんでした"
    return True, appended_count

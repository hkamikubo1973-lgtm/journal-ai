"""EPSON財務会計45列の出力行を副作用なしで生成する。"""

from __future__ import annotations

from datetime import datetime
import getpass
import platform
from collections.abc import Mapping, Sequence
from typing import Any

from columns import (
    COL_CREDIT,
    COL_CREDIT_AMOUNT,
    COL_CREDIT_SUB,
    COL_DATE,
    COL_DEBIT,
    COL_DEBIT_AMOUNT,
    COL_DEBIT_SUB,
    COL_SUMMARY,
    EPSON_COLUMNS,
)


EPSON_APP_NAME = "仕訳検索システム"


def _get_account_code(
    account_name: Any,
    account_master: Mapping[str, Any],
    fallback_account_master: Mapping[str, Any],
) -> Any:
    """現行Streamlitと同じ優先順で科目名からコードを解決する。"""

    normalized_name = str(account_name).strip()
    return account_master.get(
        normalized_name,
        fallback_account_master.get(normalized_name, ""),
    )


def build_epson_rows(
    rows: Sequence[Mapping[str, Any]],
    company_name: str,
    account_master: Mapping[str, Any],
    sub_master: Mapping[str, Any],
    fallback_account_master: Mapping[str, Any] | None = None,
    *,
    machine_name: str | None = None,
    user_name: str | None = None,
    app_name: str = EPSON_APP_NAME,
    input_date: str | None = None,
) -> list[dict[str, Any]]:
    """元45列行を保持し、現行の画面編集対象と入力情報だけを上書きする。"""

    fallback_account_master = fallback_account_master or {}
    if machine_name is None:
        machine_name = platform.node()
    if user_name is None:
        user_name = getpass.getuser()
    if input_date is None:
        input_date = datetime.now().strftime("%Y%m%d")

    result = []

    for source_row in rows:
        row = {
            column: source_row.get(column, "")
            for column in EPSON_COLUMNS
        }

        summary = source_row.get(COL_SUMMARY, "")
        debit_sub_name = source_row.get(COL_DEBIT_SUB, "")
        credit_sub_name = source_row.get(COL_CREDIT_SUB, "")

        row["伝票日付"] = source_row.get(COL_DATE, "")
        row["摘要"] = summary

        # 伝票摘要はDB雛形の値を保持し、摘要から自動コピーしない。
        row["借方科目"] = _get_account_code(
            source_row.get(COL_DEBIT, ""),
            account_master,
            fallback_account_master,
        ) or source_row.get("借方科目", "")
        row["借方科目名"] = source_row.get(COL_DEBIT, "")
        row["借方補助"] = (
            sub_master.get(
                debit_sub_name,
                source_row.get("借方補助", ""),
            )
            if debit_sub_name
            else ""
        )
        row["借方補助科目名"] = debit_sub_name
        row["借方金額"] = source_row.get(COL_DEBIT_AMOUNT, "")

        row["貸方科目"] = _get_account_code(
            source_row.get(COL_CREDIT, ""),
            account_master,
            fallback_account_master,
        ) or source_row.get("貸方科目", "")
        row["貸方科目名"] = source_row.get(COL_CREDIT, "")
        row["貸方補助"] = (
            sub_master.get(
                credit_sub_name,
                source_row.get("貸方補助", ""),
            )
            if credit_sub_name
            else ""
        )
        row["貸方補助科目名"] = credit_sub_name
        row["貸方金額"] = source_row.get(COL_CREDIT_AMOUNT, "")

        row["入力マシン"] = machine_name
        row["入力ユーザ"] = user_name
        row["入力アプリ"] = app_name
        row["入力会社"] = company_name
        row["入力日付"] = input_date

        result.append(row)

    return result

"""通常仕訳画面向けマスターを読み取り専用で提供する。"""

from __future__ import annotations

from collections import defaultdict
import csv
from pathlib import Path
from typing import Any


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ACCOUNT_MASTER_PATH = DATA_DIR / "account_master.csv"
SUB_ACCOUNT_MASTER_PATH = DATA_DIR / "sub_master.csv"
DEPARTMENT_MASTER_PATH = DATA_DIR / "department_master.csv"

UNSELECTABLE_ACCOUNT_NAMES = frozenset({"資金複合", "諸口"})
UNSELECTABLE_REASON = "通常仕訳の直接選択対象外です。"
SUB_ACCOUNT_RELATION_WARNING = (
    "sub_master.csv には親科目コードがないため、"
    "科目別補助絞り込みはできません。"
)


def _text(value: Any) -> str:
    """CSV値を文字列としてtrimし、Noneを空文字へそろえる。"""

    return "" if value is None else str(value).strip()


def _read_master_rows(
    path: Path,
    required_fields: tuple[str, ...],
) -> tuple[list[dict[str, str]], int]:
    """必須列を検証し、コード・名称が空の行を除いて読み込む。"""

    rows: list[dict[str, str]] = []
    skipped_count = 0

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        fields = set(reader.fieldnames or [])
        missing_fields = set(required_fields) - fields
        if missing_fields:
            missing = ", ".join(sorted(missing_fields))
            raise ValueError(f"{path.name} に必要な列がありません: {missing}")

        for source_row in reader:
            row = {
                field: _text(source_row.get(field))
                for field in reader.fieldnames or []
                if field is not None
            }
            if not row.get("code") or not row.get("name"):
                skipped_count += 1
                continue
            rows.append(row)

    return rows, skipped_count


def _is_selectable_account(name: str) -> bool:
    """資金複合・諸口を含む科目を通常仕訳の直接選択から除外する。"""

    return not any(excluded in name for excluded in UNSELECTABLE_ACCOUNT_NAMES)


def _build_accounts(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    accounts = []
    for row in rows:
        selectable = _is_selectable_account(row["name"])
        accounts.append({
            "code": row["code"],
            "name": row["name"],
            "category": row.get("category", ""),
            "label": f"{row['code']}\u3000{row['name']}",
            "selectable": selectable,
            "unselectable_reason": None if selectable else UNSELECTABLE_REASON,
        })
    return accounts


def _build_simple_items(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {
            "code": row["code"],
            "name": row["name"],
            "label": f"{row['code']}\u3000{row['name']}",
        }
        for row in rows
    ]


def _duplicate_account_names(
    accounts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    codes_by_name: dict[str, set[str]] = defaultdict(set)
    for account in accounts:
        codes_by_name[account["name"]].add(account["code"])

    return [
        {"name": name, "codes": sorted(codes)}
        for name, codes in sorted(codes_by_name.items())
        if len(codes) > 1
    ]


def _duplicate_sub_codes(
    sub_accounts: list[dict[str, str]],
) -> list[dict[str, Any]]:
    names_by_code: dict[str, set[str]] = defaultdict(set)
    for sub_account in sub_accounts:
        names_by_code[sub_account["code"]].add(sub_account["name"])

    return [
        {"code": code, "names": sorted(names)}
        for code, names in sorted(names_by_code.items())
        if len(names) > 1
    ]


def load_journal_masters() -> dict:
    """3種のマスターと診断情報を返す。ファイルへの書き込みは行わない。"""

    account_rows, skipped_accounts = _read_master_rows(
        ACCOUNT_MASTER_PATH,
        ("code", "name"),
    )
    sub_account_rows, skipped_sub_accounts = _read_master_rows(
        SUB_ACCOUNT_MASTER_PATH,
        ("code", "name"),
    )
    department_rows, skipped_departments = _read_master_rows(
        DEPARTMENT_MASTER_PATH,
        ("code", "name"),
    )

    accounts = _build_accounts(account_rows)
    sub_accounts = _build_simple_items(sub_account_rows)
    departments = _build_simple_items(department_rows)
    warnings = [SUB_ACCOUNT_RELATION_WARNING]

    for filename, skipped_count in (
        (ACCOUNT_MASTER_PATH.name, skipped_accounts),
        (SUB_ACCOUNT_MASTER_PATH.name, skipped_sub_accounts),
        (DEPARTMENT_MASTER_PATH.name, skipped_departments),
    ):
        if skipped_count:
            warnings.append(
                f"{filename} のコードまたは名称が空の行を"
                f"{skipped_count}件除外しました。"
            )

    selectable_account_count = sum(
        1 for account in accounts if account["selectable"]
    )
    diagnostics = {
        "account_count": len(accounts),
        "selectable_account_count": selectable_account_count,
        "unselectable_account_count": (
            len(accounts) - selectable_account_count
        ),
        "sub_account_count": len(sub_accounts),
        "department_count": len(departments),
        "duplicate_account_names": _duplicate_account_names(accounts),
        "duplicate_sub_codes": _duplicate_sub_codes(sub_accounts),
        "warnings": warnings,
    }

    return {
        "accounts": accounts,
        "sub_accounts": sub_accounts,
        "departments": departments,
        "diagnostics": diagnostics,
    }

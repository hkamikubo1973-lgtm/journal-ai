"""未収消込の入金科目候補と差額科目推薦を提供する。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import csv
from pathlib import Path
from typing import Any, Callable

from columns import (
    COL_CREDIT,
    COL_CREDIT_SUB,
    COL_DEBIT,
    COL_DEBIT_SUB,
    COL_SUMMARY,
)
from engine import EXCLUDED_SUGGESTION_ACCOUNTS, load_data, tokenize
from receivable_account_validation_service import (
    build_receivable_account_code_index,
    normalize_receivable_account_master,
    resolve_unique_receivable_account,
)


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PAYMENT_ACCOUNTS_PATH = DATA_DIR / "payment_accounts.csv"

RECEIVABLE_DIFFERENCE_RECOMMEND_EXCLUDED = frozenset({
    "資金複合",
    "諸口",
    "普通預金",
    "当座預金",
    "現金",
    "未収運賃",
    "未収金",
    "売掛金",
})

RECEIVABLE_OVERPAID_RECOMMEND_EXCLUDED = frozenset({
    "未払金",
    "買掛金",
    "未払費用",
    "預り金",
})

RECEIVABLE_CANDIDATE_CONTEXT_COLUMNS = (
    "未収科目",
    "未収補助",
    "部門",
    "摘要",
)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def load_payment_accounts(
    path: str | Path = PAYMENT_ACCOUNTS_PATH,
) -> list[str]:
    """現行Streamlitと同じset化・名前順で入金科目名を読む。"""

    result: set[str] = set()
    with Path(path).open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            account = _text(row.get("科目"))
            if account and account.casefold() != "nan":
                result.add(account)
    return sorted(result)


def _account_master_rows(account_master_snapshot: Any) -> list[dict[str, str]]:
    """既存master DTO/DataFrame/rowsを安全解決用の行へ正規化する。"""

    return normalize_receivable_account_master(
        account_master_snapshot,
        strict=False,
    )


def resolve_unique_account_options(
    account_names: Sequence[Any],
    account_master_snapshot: Any,
) -> tuple[list[dict[str, str]], list[str]]:
    """科目名を一意なcodeへ解決し、解決不能名を候補から除く。"""

    codes_by_name = build_receivable_account_code_index(
        account_master_snapshot,
        strict=False,
    )

    options: list[dict[str, str]] = []
    invalid_names: list[str] = []
    seen_names: set[str] = set()
    for value in account_names:
        name = _text(value)
        if name in seen_names:
            continue
        seen_names.add(name)
        resolved = resolve_unique_receivable_account(codes_by_name, name)
        if resolved is None:
            invalid_names.append(name)
        else:
            options.append(resolved)
    return options, invalid_names


def build_receipt_account_options(
    payment_account_names: Sequence[Any],
    account_master_snapshot: Any,
) -> dict[str, Any]:
    """APIで利用可能な入金科目候補とdefaultを組み立てる。"""

    normalized_names = sorted({
        name
        for value in payment_account_names
        if (name := _text(value)) and name.casefold() != "nan"
    })
    options, invalid_names = resolve_unique_account_options(
        normalized_names,
        account_master_snapshot,
    )
    option_names = [option["name"] for option in options]
    if "普通預金" in option_names:
        default_account: str | None = "普通預金"
    elif option_names:
        default_account = option_names[0]
    else:
        default_account = None
    return {
        "receipt_accounts": options,
        "default_receipt_account": default_account,
        "invalid_receipt_account_names": invalid_names,
    }


def load_receipt_account_options(
    account_master_snapshot: Any,
    payment_accounts_path: str | Path = PAYMENT_ACCOUNTS_PATH,
) -> dict[str, Any]:
    """正式CSVを読むapplication wrapper。書込みは行わない。"""

    return build_receipt_account_options(
        load_payment_accounts(payment_accounts_path),
        account_master_snapshot,
    )


def is_receivable_difference_recommend_excluded(account: Any) -> bool:
    account_name = _text(account)
    if (
        account_name in RECEIVABLE_DIFFERENCE_RECOMMEND_EXCLUDED
        or account_name in EXCLUDED_SUGGESTION_ACCOUNTS
    ):
        return True
    return "未収" in account_name or "売掛" in account_name


def is_receivable_difference_recommendable(
    account: Any,
    side: str,
    account_categories: Mapping[str, Any],
    account_codes: Mapping[str, Any],
) -> bool:
    """現行Streamlitのcategory/code/固定名条件をそのまま判定する。"""

    account_name = _text(account)
    if is_receivable_difference_recommend_excluded(account_name):
        return False
    if (
        side == "credit"
        and account_name in RECEIVABLE_OVERPAID_RECOMMEND_EXCLUDED
    ):
        return False

    category = _text(account_categories.get(account_name, ""))
    if category:
        if side == "debit":
            return category == "費用"
        return category in {"負債", "収益"}

    try:
        code_number = int(_text(account_codes.get(account_name, "")))
    except (TypeError, ValueError):
        code_number = 0

    if side == "debit":
        return (
            400 <= code_number < 600
            or account_name in {"支払手数料", "雑費"}
        )
    return (
        200 <= code_number < 300
        or account_name in {"仮受金", "雑収入"}
        or account_name.endswith("収入")
    )


def build_receivable_difference_account_options(
    account_master_snapshot: Any,
    transactions_snapshot: Sequence[Mapping[str, Any]],
    customer_name: Any,
    candidates: Sequence[Mapping[str, Any]],
    side: str,
    default_account: Any,
    top_n: int = 5,
    *,
    tokenize_text: Callable[[Any], list[str]] = tokenize,
) -> tuple[list[str], set[str], str]:
    """差額科目の表示順と推薦集合を作るpure core。"""

    master_rows = _account_master_rows(account_master_snapshot)
    account_codes: dict[str, str] = {}
    account_categories: dict[str, str] = {}
    ordered_accounts: list[str] = []
    seen_accounts: set[str] = set()
    for row in master_rows:
        name = row["name"]
        if name not in seen_accounts:
            ordered_accounts.append(name)
            seen_accounts.add(name)
        account_codes[name] = row["code"]
        account_categories[name] = row["category"]

    allowed_accounts = [
        account
        for account in ordered_accounts
        if account not in EXCLUDED_SUGGESTION_ACCOUNTS
    ]
    if not allowed_accounts:
        return [], set(), ""

    normalized_default = _text(default_account)
    if normalized_default not in allowed_accounts:
        normalized_default = allowed_accounts[0]

    context_text = " ".join(
        [_text(customer_name)]
        + [
            _text(candidate.get(column, ""))
            for candidate in candidates
            for column in RECEIVABLE_CANDIDATE_CONTEXT_COLUMNS
        ]
    )
    context_tokens = set(tokenize_text(context_text))
    target_column = COL_DEBIT if side == "debit" else COL_CREDIT

    scores: dict[str, int] = {}
    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    for record in transactions_snapshot:
        if not isinstance(record, Mapping):
            continue
        rows = record.get("rows", [])
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            account = _text(row.get(target_column, ""))
            if (
                not account
                or account not in allowed_accounts
                or account in EXCLUDED_SUGGESTION_ACCOUNTS
                or not is_receivable_difference_recommendable(
                    account,
                    side,
                    account_categories,
                    account_codes,
                )
            ):
                continue

            row_text = " ".join([
                _text(row.get(COL_SUMMARY, "")),
                _text(row.get("伝票摘要", "")),
                _text(row.get(COL_DEBIT_SUB, "")),
                _text(row.get(COL_CREDIT_SUB, "")),
                _text(row.get(COL_DEBIT, "")),
                _text(row.get(COL_CREDIT, "")),
            ])
            score = len(context_tokens & set(tokenize_text(row_text)))
            if score <= 0:
                continue
            if account not in first_seen:
                first_seen[account] = len(first_seen)
            scores[account] = scores.get(account, 0) + score
            counts[account] = counts.get(account, 0) + 1

    recommended_accounts = sorted(
        scores,
        key=lambda account: (
            -scores[account],
            -counts[account],
            first_seen[account],
        ),
    )[:top_n]

    if (
        is_receivable_difference_recommendable(
            normalized_default,
            side,
            account_categories,
            account_codes,
        )
        and normalized_default not in recommended_accounts
    ):
        recommended_accounts.insert(0, normalized_default)
        recommended_accounts = recommended_accounts[:top_n]

    options = recommended_accounts + [
        account
        for account in [normalized_default]
        if account not in recommended_accounts
    ] + [
        account
        for account in allowed_accounts
        if (
            account not in recommended_accounts
            and account != normalized_default
        )
    ]
    return options, set(recommended_accounts), normalized_default


def build_safe_receivable_difference_options(
    account_master_snapshot: Any,
    transactions_snapshot: Sequence[Mapping[str, Any]],
    customer_name: Any,
    candidates: Sequence[Mapping[str, Any]],
    side: str,
    default_account: Any,
    top_n: int = 5,
) -> dict[str, Any]:
    """推薦結果を一意master code付きのJSON-safe候補へ変換する。"""

    names, recommended_names, selected_default = (
        build_receivable_difference_account_options(
            account_master_snapshot,
            transactions_snapshot,
            customer_name,
            candidates,
            side,
            default_account,
            top_n,
        )
    )
    options, invalid_names = resolve_unique_account_options(
        names,
        account_master_snapshot,
    )
    safe_by_name = {option["name"]: option for option in options}
    return {
        "difference_account_options": options,
        "recommended_difference_accounts": [
            safe_by_name[name]
            for name in names
            if name in recommended_names and name in safe_by_name
        ],
        "default_difference_account": safe_by_name.get(selected_default),
        "invalid_difference_account_names": invalid_names,
    }


def build_receivable_difference_summary(
    customer_name: Any,
    side: str,
) -> str:
    """Return the existing Streamlit default summary for one difference side."""

    customer = _text(customer_name)
    if side == "debit":
        return f"{customer} 差額調整"
    if side == "credit":
        return f"{customer} 過入金調整"
    raise ValueError("difference side must be debit or credit")


def load_safe_receivable_difference_options(
    account_master_snapshot: Any,
    customer_name: Any,
    candidates: Sequence[Mapping[str, Any]],
    side: str,
    default_account: Any,
    top_n: int = 5,
) -> dict[str, Any]:
    """Read production transactions and return API-safe recommendations."""

    records, _name_to_code, _frequency = load_data()
    return build_safe_receivable_difference_options(
        account_master_snapshot,
        records,
        customer_name,
        candidates,
        side,
        default_account,
        top_n,
    )


def load_receivable_difference_account_options(
    account_master_snapshot: Any,
    customer_name: Any,
    candidates: Sequence[Mapping[str, Any]],
    side: str,
    default_account: Any,
    top_n: int = 5,
) -> tuple[list[str], set[str], str]:
    """実transactionsを読むproduction wrapper。書込みは行わない。"""

    records, _name_to_code, _frequency = load_data()
    return build_receivable_difference_account_options(
        account_master_snapshot,
        records,
        customer_name,
        candidates,
        side,
        default_account,
        top_n,
    )

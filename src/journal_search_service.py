"""通常仕訳検索結果をUI向けDTOへ変換するサービス。"""

import copy
import math
from collections.abc import Mapping
from datetime import date, datetime
from numbers import Integral, Real

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
from engine import EXCLUDED_SUGGESTION_ACCOUNTS, search, to_int


SEARCH_LIMITS = {5, 10, 20}

MATCHED_AMOUNT_ROW_FIELDS = [
    "match_type",
    "date",
    "debit_code",
    "debit",
    "credit_code",
    "credit",
    "debit_sub",
    "credit_sub",
    "amount",
    "input_amount",
    "diff",
    "diff_rate",
    "summary",
]


def _json_safe_value(value):
    """値を標準のJSONエンコーダーで扱える型へ変換する。"""

    if value is None or isinstance(value, (str, bool)):
        return value

    if isinstance(value, Integral):
        return int(value)

    if isinstance(value, Real):
        numeric_value = float(value)
        if math.isnan(numeric_value) or math.isinf(numeric_value):
            return ""
        if numeric_value.is_integer():
            return int(numeric_value)
        return numeric_value

    if isinstance(value, (date, datetime)):
        return value.isoformat()

    if isinstance(value, Mapping):
        return {
            str(key): _json_safe_value(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(item) for item in value]

    item_method = getattr(value, "item", None)
    if callable(item_method):
        try:
            return _json_safe_value(item_method())
        except (TypeError, ValueError):
            pass

    return str(value)


def normalize_search_params(
    keyword,
    department,
    amount,
    limit,
):
    """検索条件を現行Streamlit版と同じ入力表現へそろえる。"""

    normalized_keyword = "" if keyword is None else str(keyword)
    normalized_department = str(department).strip() if department else None

    try:
        normalized_amount = int(amount) if amount is not None else None
    except (TypeError, ValueError):
        raise ValueError("amountは整数で指定してください") from None

    if normalized_amount is not None and normalized_amount <= 0:
        normalized_amount = None

    try:
        normalized_limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(
            "limitは5、10、20のいずれかを指定してください"
        ) from None

    if normalized_limit not in SEARCH_LIMITS:
        raise ValueError("limitは5、10、20のいずれかを指定してください")

    return {
        "keyword": normalized_keyword,
        "department": normalized_department,
        "amount": normalized_amount,
        "limit": normalized_limit,
    }


def serialize_pattern_key(value):
    """検索エンジンのパターンキーをJSON配列へ変換する。"""

    if value is None:
        return []

    if not isinstance(value, (list, tuple)):
        value = [value]

    return ["" if item is None else str(item) for item in value]


def serialize_matched_amount_row(value):
    """金額一致・近似行を公開項目だけのDTOへ変換する。"""

    if not isinstance(value, Mapping) or not value:
        return None

    return {
        field: _json_safe_value(value.get(field))
        for field in MATCHED_AMOUNT_ROW_FIELDS
    }


def serialize_source_rows(rows):
    """元伝票行を変更せず、既存CSV列名を保ったJSON-safeな行へ変換する。"""

    return [
        {
            str(column): _json_safe_value(value)
            for column, value in row.items()
        }
        for row in (rows or [])
        if isinstance(row, Mapping)
    ]


def get_voucher_total(rows):
    """現行app.pyと同じく借方金額の合計を伝票金額として返す。"""

    return sum(to_int(row.get(COL_DEBIT_AMOUNT)) for row in (rows or []))


def split_journal(rows):
    """現行app.pyと同じ規則で1対多・多対1の伝票を分割する。"""

    source_rows = copy.deepcopy(list(rows or []))
    debits = []
    credits = []

    for row in source_rows:
        debit_amount = to_int(row.get(COL_DEBIT_AMOUNT))
        credit_amount = to_int(row.get(COL_CREDIT_AMOUNT))

        if debit_amount > 0:
            debits.append((row, debit_amount))

        if credit_amount > 0:
            credits.append((row, credit_amount))

    if len(debits) == 1 and len(credits) > 1:
        debit_row, _ = debits[0]
        result = []

        for credit_row, credit_amount in credits:
            new_row = copy.deepcopy(debit_row)
            new_row[COL_CREDIT] = credit_row.get(COL_CREDIT, "")
            new_row[COL_CREDIT_SUB] = credit_row.get(COL_CREDIT_SUB, "")
            new_row[COL_CREDIT_AMOUNT] = str(credit_amount)
            new_row[COL_DEBIT_AMOUNT] = str(credit_amount)
            result.append(new_row)

        return result

    if len(credits) == 1 and len(debits) > 1:
        credit_row, _ = credits[0]
        result = []

        for debit_row, debit_amount in debits:
            new_row = copy.deepcopy(debit_row)
            new_row[COL_CREDIT] = credit_row.get(COL_CREDIT, "")
            new_row[COL_CREDIT_SUB] = credit_row.get(COL_CREDIT_SUB, "")
            new_row[COL_CREDIT_AMOUNT] = str(debit_amount)
            new_row[COL_DEBIT_AMOUNT] = str(debit_amount)
            result.append(new_row)

        return result

    return source_rows


def build_editable_rows(source_rows):
    """元伝票から画面編集を開始するための行を生成する。"""

    return serialize_source_rows(split_journal(source_rows))


def is_excluded_account(account):
    return str(account or "").strip() in EXCLUDED_SUGGESTION_ACCOUNTS


def get_row_account(row, side):
    if not isinstance(row, Mapping):
        return ""

    if side == "debit":
        return str(row.get(COL_DEBIT, row.get("debit", "")) or "").strip()

    return str(row.get(COL_CREDIT, row.get("credit", "")) or "").strip()


def row_has_excluded_account(row):
    return is_excluded_account(get_row_account(row, "debit")) or (
        is_excluded_account(get_row_account(row, "credit"))
    )


def build_block_rows(source_rows):
    """分割前の同一伝票ブロックをReact向けの参照行へ変換する。"""

    return [
        {
            "date": _json_safe_value(row.get(COL_DATE, "")),
            "debit": _json_safe_value(row.get(COL_DEBIT, "")),
            "credit": _json_safe_value(row.get(COL_CREDIT, "")),
            "debit_sub": _json_safe_value(row.get(COL_DEBIT_SUB, "")),
            "credit_sub": _json_safe_value(row.get(COL_CREDIT_SUB, "")),
            "debit_amount": _json_safe_value(
                row.get(COL_DEBIT_AMOUNT, "")
            ),
            "credit_amount": _json_safe_value(
                row.get(COL_CREDIT_AMOUNT, "")
            ),
            "summary": _json_safe_value(row.get(COL_SUMMARY, "")),
            "voucher_summary": _json_safe_value(
                row.get("伝票摘要", "")
            ),
        }
        for row in (source_rows or [])
        if isinstance(row, Mapping)
    ]


def analyze_complex_accounts(source_rows, matched_amount_row=None):
    """資金複合・諸口と伝票ブロック表示要否を判定する。"""

    has_fukugo = any(
        "資金複合" in (
            get_row_account(row, "debit")
            + get_row_account(row, "credit")
        )
        for row in (source_rows or [])
    )
    has_sundry = any(
        "諸口" in (
            get_row_account(row, "debit")
            + get_row_account(row, "credit")
        )
        for row in (source_rows or [])
    )
    contains_complex_account = has_fukugo or has_sundry
    matched_row_is_excluded = row_has_excluded_account(
        matched_amount_row
    )

    return {
        "has_fukugo": has_fukugo,
        "has_sundry": has_sundry,
        "contains_fukugo_or_sundry": contains_complex_account,
        "show_block_rows": (
            contains_complex_account or matched_row_is_excluded
        ),
        "is_complex": contains_complex_account or len(source_rows or []) > 1,
    }


def serialize_search_reason(score_detail):
    return [
        str(reason)
        for reason in (score_detail or [])
        if reason not in (None, "")
    ]


def serialize_search_candidate(result, rank):
    """engine.search()の1候補を公開DTOへ変換する。"""

    if not isinstance(result, (list, tuple)) or len(result) != 3:
        raise ValueError("検索結果の形式が不正です")

    score, record, score_detail = result
    if not isinstance(record, Mapping):
        raise ValueError("検索候補の形式が不正です")

    source_rows = serialize_source_rows(record.get("rows", []))
    matched_amount_row = serialize_matched_amount_row(
        record.get("matched_amount_row")
    )
    complex_accounts = analyze_complex_accounts(
        source_rows,
        matched_amount_row,
    )

    pattern_rank = record.get("pattern_rank")
    try:
        pattern_rank = int(pattern_rank) if pattern_rank is not None else None
    except (TypeError, ValueError):
        pattern_rank = None

    return {
        "rank": int(rank),
        "score": int(score),
        "pattern_key": serialize_pattern_key(record.get("pattern_key")),
        "pattern_rank": pattern_rank,
        "search_reason": serialize_search_reason(score_detail),
        "matched_amount_row": matched_amount_row,
        "source_rows": source_rows,
        "editable_rows": build_editable_rows(source_rows),
        "block_rows": build_block_rows(source_rows),
        **complex_accounts,
    }


def serialize_search_results(results):
    return [
        serialize_search_candidate(result, rank)
        for rank, result in enumerate(results or [], start=1)
    ]


def search_journals(
    records,
    freq,
    keyword="",
    department=None,
    amount=None,
    limit=5,
):
    """通常仕訳を検索し、React向けの安定したDTOを返す。"""

    params = normalize_search_params(
        keyword,
        department,
        amount,
        limit,
    )
    results = search(
        records,
        params["keyword"],
        params["department"],
        params["amount"],
        freq,
        limit=params["limit"],
    )
    candidates = serialize_search_results(results)

    return {
        "query": params,
        "count": len(candidates),
        "candidates": candidates,
    }

# engine.py
# =========================================
# 仕訳検索エンジン
# =========================================

import csv
import re
import unicodedata
import copy

from collections import Counter
from datetime import datetime
from fiscal_year import (
    get_current_fiscal_year,
    get_fiscal_year,
    KEEP_PAST_FISCAL_YEARS,
    require_journal_date,
)
from system_settings import load_system_settings
from columns import SEARCH_COLUMNS
from columns import (
    EPSON_COLUMNS,
    COL_DATE,
    COL_DEBIT,
    COL_CREDIT,
    COL_DEBIT_SUB,
    COL_CREDIT_SUB,
    COL_DEBIT_AMOUNT,
    COL_CREDIT_AMOUNT,
    COL_SUMMARY,
    COL_VOUCHER_NO,
)


def extract_year(date_str):
    try:
        return datetime.strptime(date_str, "%Y/%m/%d").year
    except:
        return datetime.now().year

DATA_PATH = "data/transactions.csv"

EXCLUDED_SUGGESTION_ACCOUNTS = {
    "資金複合",
    "諸口",
}

# =========================================
# ストップワード
# =========================================
STOP_WORDS = {
    "株式会社",
    "有限会社",
    "御中",
    "様",
}

# =========================================
# 正規化
# =========================================
def normalize(text):

    if not text:
        return ""

    text = unicodedata.normalize(
        "NFKC",
        str(text)
    ).lower()

    # カタカナ → ひらがな
    text = "".join(
        chr(ord(c) - 0x60)
        if "ァ" <= c <= "ン"
        else c
        for c in text
    )

    return re.sub(
        r"\s+",
        " ",
        text
    ).strip()


# =========================================
# tokenize
# =========================================
def tokenize(text):

    text = normalize(text)

    tokens = re.split(
        r"[ 　/()（）・\-_]",
        text
    )

    result = []

    for t in tokens:

        if not t:
            continue

        if t in STOP_WORDS:
            continue

        result.append(t)

        # 2gram
        if len(t) >= 2:

            for i in range(len(t)-1):

                result.append(
                    t[i:i+2]
                )

    return list(set(result))


# =========================================
# 金額変換
# =========================================
def to_int(v):

    try:

        return int(
            float(
                str(v).replace(",", "")
            )
        )

    except:
        return 0


# =========================================
# 部門
# =========================================
def get_department(r):

    return (
        r.get("借方部門名")
        or
        r.get("貸方部門名")
        or
        ""
    )


# =========================================
# 伝票分割
# =========================================
def split_records(rows):

    records = []
    current = []

    d_sum = 0
    c_sum = 0

    last_date = None

    for r in rows:

        d = to_int(
            r.get(COL_DEBIT_AMOUNT)
        )

        c = to_int(
            r.get(COL_CREDIT_AMOUNT)
        )

        date = r.get(COL_DATE)

        # 日付変化で強制分割
        if (
            last_date
            and
            date != last_date
            and
            current
        ):

            records.append(current)

            current = []

            d_sum = 0
            c_sum = 0

        current.append(r)

        d_sum += d
        c_sum += c

        # 貸借一致で確定
        if d_sum == c_sum and d_sum > 0:

            records.append(current)

            current = []

            d_sum = 0
            c_sum = 0

        last_date = date

    if current:
        records.append(current)

    return records


# =========================================
# 資金複合分解
# FIFO簡易版
# =========================================
def explode_fukugo(rows):

    has_fukugo = any(

        "資金複合" in (

            r.get(COL_DEBIT, "")
            +
            r.get(COL_CREDIT, "")

        )

        for r in rows
    )

    # 資金複合なし
    if not has_fukugo:
        return rows

    sources = []
    targets = []

    normal_rows = []

    for r in rows:

        d = r.get(COL_DEBIT)
        c = r.get(COL_CREDIT)

        d_amt = to_int(
            r.get(COL_DEBIT_AMOUNT)
        )

        c_amt = to_int(
            r.get(COL_CREDIT_AMOUNT)
        )

        # =====================================
        # source
        # 普通預金 / 資金複合
        # =====================================
        if (
            c == "資金複合"
            and
            d != "資金複合"
        ):

            if d_amt > 0:

                sources.append({

                    "account":
                        d,

                    "amount":
                        d_amt,

                    "remain":
                        d_amt,

                    "row":
                        r
                })

        # =====================================
        # target
        # 資金複合 / 売上
        # =====================================
        elif (
            d == "資金複合"
            and
            c != "資金複合"
        ):

            if c_amt > 0:

                targets.append({

                    "account":
                        c,

                    "amount":
                        c_amt,

                    "remain":
                        c_amt,

                    "row":
                        r
                })

        # =====================================
        # 通常行
        # =====================================
        else:

            normal_rows.append(r)

    # =====================================
    # source / target不足
    # =====================================
    if not sources or not targets:

        return rows

    new_rows = copy.deepcopy(
        normal_rows
    )

    s_idx = 0
    t_idx = 0

    # =====================================
    # FIFO分配
    # =====================================
    while (
        s_idx < len(sources)
        and
        t_idx < len(targets)
    ):

        s = sources[s_idx]
        t = targets[t_idx]

        amt = min(
            s["remain"],
            t["remain"]
        )

        if amt <= 0:
            break

        # =====================================
        # 45列維持版
        # =====================================
        new = copy.deepcopy(
            s["row"]
        )

        new[COL_DEBIT] = (
            s["account"]
        )

        new[COL_CREDIT] = (
            t["account"]
        )

        new[COL_DEBIT_AMOUNT] = (
            str(amt)
        )

        new[COL_CREDIT_AMOUNT] = (
           str(amt)
        )

        new["推定変換"] = "1"

        new_rows.append(new)

        # 残高減算
        s["remain"] -= amt
        t["remain"] -= amt

        # source消化
        if s["remain"] <= 0:
            s_idx += 1

        # target消化
        if t["remain"] <= 0:
            t_idx += 1

    # =====================================
    # 分解失敗
    # =====================================
    if not new_rows:
        return rows

    # =====================================
    # 残高未消化
    # =====================================
    remain_exists = any(
        s["remain"] > 0
        for s in sources
    ) or any(
        t["remain"] > 0
        for t in targets
    )

    # 未消化あり
    if remain_exists:
        return rows

    return new_rows


# =========================================
# 科目マップ
# =========================================
def build_account_map(rows):

    mp = {}

    for r in rows:

        if (
            r.get(COL_DEBIT)
            and
            r.get("借方科目")
        ):

            mp[
                r["借方科目名"]
            ] = r["借方科目"]

        if (
            r.get(COL_CREDIT)
            and
            r.get("貸方科目")
        ):

            mp[
                r["貸方科目名"]
            ] = r["貸方科目"]

    return mp

# =========================================
# テンプレ読込
# =========================================
TEMPLATE_PATH = "data/templates.csv"

def load_templates():
    templates = []
    try:
        with open(TEMPLATE_PATH, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for r in reader:
                templates.append(r)
    except:
        pass
    return templates

# =========================================
# 伝票総額
# =========================================
def get_voucher_total(rows):

    d_total = sum(
        to_int(r.get(COL_DEBIT_AMOUNT))
        for r in rows
    )

    c_total = sum(
        to_int(r.get(COL_CREDIT_AMOUNT))
        for r in rows
    )

    return max(
        d_total,
        c_total
    )


def diagnose_voucher_numbers_in_rows(rows):

    rows = rows or []
    voucher_numbers = []
    debit_total = 0
    credit_total = 0

    for row in rows:
        voucher_no = str(row.get(COL_VOUCHER_NO, "") or "").strip()

        if voucher_no and voucher_no not in voucher_numbers:
            voucher_numbers.append(voucher_no)

        debit_total += to_int(row.get(COL_DEBIT_AMOUNT))
        credit_total += to_int(row.get(COL_CREDIT_AMOUNT))

    voucher_count = len(voucher_numbers)

    return {
        "voucher_numbers": voucher_numbers,
        "voucher_count": voucher_count,
        "has_multiple_voucher_numbers": voucher_count > 1,
        "row_count": len(rows),
        "debit_total": debit_total,
        "credit_total": credit_total,
        "balance_diff": debit_total - credit_total,
    }


def build_search_rows(rows):

    expanded_rows = explode_fukugo(rows)

    if expanded_rows is rows or expanded_rows == rows:
        return rows

    return rows + expanded_rows


def build_matched_row_info(
    row,
    amount=None,
    match_type="",
    input_amount=None,
    diff=None,
    diff_rate=None
):

    matched_amount = amount

    if matched_amount is None:
        matched_amount = max(
            to_int(row.get(COL_DEBIT_AMOUNT)),
            to_int(row.get(COL_CREDIT_AMOUNT))
        )

    return {
        "match_type": match_type,
        "date": row.get(COL_DATE, ""),
        "debit_code": row.get("借方科目", ""),
        "debit": row.get(COL_DEBIT, ""),
        "credit_code": row.get("貸方科目", ""),
        "credit": row.get(COL_CREDIT, ""),
        "debit_sub": row.get(COL_DEBIT_SUB, ""),
        "credit_sub": row.get(COL_CREDIT_SUB, ""),
        "amount": matched_amount,
        "input_amount": input_amount,
        "diff": diff,
        "diff_rate": diff_rate,
        "summary": row.get(COL_SUMMARY, ""),
    }


def get_amount_proximity(input_amount, past_amount):

    input_amount = to_int(input_amount)
    past_amount = to_int(past_amount)

    if input_amount <= 0 or past_amount <= 0:
        return None

    diff = abs(past_amount - input_amount)
    rate = diff / input_amount

    if diff == 0:
        return {
            "score": 200,
            "diff": diff,
            "rate": rate,
            "label": "一致",
        }

    thresholds = [
        (0.01, 160),
        (0.03, 120),
        (0.05, 80),
        (0.10, 40),
        (0.20, 15),
    ]

    for threshold, add_score in thresholds:
        if rate <= threshold:
            return {
                "score": add_score,
                "diff": diff,
                "rate": rate,
                "label": "近似",
            }

    return None


def format_amount_proximity_detail(input_amount, past_amount, proximity):

    rate_percent = proximity["rate"] * 100

    return (
        f"金額近似:入力 {input_amount:,} / "
        f"過去 {past_amount:,} / "
        f"差額 {proximity['diff']:,} / "
        f"約{rate_percent:.1f}% "
        f"+{proximity['score']}"
    )


def find_best_amount_match(rows, input_amount):

    best = None

    for row in rows:
        for candidate_amount in {
            to_int(row.get(COL_DEBIT_AMOUNT)),
            to_int(row.get(COL_CREDIT_AMOUNT)),
        }:
            proximity = get_amount_proximity(
                input_amount,
                candidate_amount
            )

            if not proximity:
                continue

            candidate = {
                "row": row,
                "amount": candidate_amount,
                "proximity": proximity,
            }

            if (
                best is None
                or proximity["score"] > best["proximity"]["score"]
                or (
                    proximity["score"] == best["proximity"]["score"]
                    and proximity["diff"] < best["proximity"]["diff"]
                )
            ):
                best = candidate

    return best


def is_debug_target_row(row):

    date = row.get(COL_DATE, row.get("date", ""))

    if date != "20250327":
        return False

    amount = to_int(row.get("amount"))

    if (
        amount != 1966850
        and to_int(row.get(COL_DEBIT_AMOUNT)) != 1966850
        and to_int(row.get(COL_CREDIT_AMOUNT)) != 1966850
    ):
        return False

    accounts = {
        row.get(COL_DEBIT, row.get("debit", "")),
        row.get(COL_CREDIT, row.get("credit", ""))
    }

    if not {"仮払金", "資金複合"}.issubset(accounts):
        return False

    summary_text = " ".join([
        str(row.get(COL_SUMMARY, row.get("summary", "")) or ""),
        str(row.get("伝票摘要", "") or "")
    ])

    return "健厚" in summary_text


def build_row_debug_info(row):

    if not row:
        return {}

    return {
        "日付": row.get(COL_DATE, ""),
        "借方科目コード": row.get("借方科目", ""),
        "借方科目名": row.get(COL_DEBIT, ""),
        "貸方科目コード": row.get("貸方科目", ""),
        "貸方科目名": row.get(COL_CREDIT, ""),
        "借方補助": row.get(COL_DEBIT_SUB, ""),
        "貸方補助": row.get(COL_CREDIT_SUB, ""),
        "借方金額": to_int(row.get(COL_DEBIT_AMOUNT)),
        "貸方金額": to_int(row.get(COL_CREDIT_AMOUNT)),
        "摘要": row.get(COL_SUMMARY, ""),
        "伝票摘要": row.get("伝票摘要", ""),
    }


def diagnose_debug_target(records, keyword, dept, amount, freq, results=None):

    diagnostic = {
        "target_exists": False,
        "in_rows": False,
        "in_search_rows": False,
        "score": None,
        "rank": None,
        "group_id": None,
        "representative_row": None,
        "target_row": None,
        "matched_row_kept": False,
        "passed_to_display": False,
        "score_detail": [],
    }

    all_results = []

    for group_id, rec in enumerate(records, start=1):

        rows = rec.get("rows", [])
        search_rows = rec.get("search_rows", rows)

        rows_matches = [
            row
            for row in rows
            if is_debug_target_row(row)
        ]
        search_rows_matches = [
            row
            for row in search_rows
            if is_debug_target_row(row)
        ]

        s, score_detail, matched_amount_row = calculate_score(
            rec,
            keyword,
            dept,
            amount,
            freq
        )
        all_results.append({
            "group_id": group_id,
            "record": rec,
            "score": s,
            "score_detail": score_detail,
            "matched_amount_row": matched_amount_row,
            "rows_matches": rows_matches,
            "search_rows_matches": search_rows_matches,
        })

    ranked_results = sorted(
        all_results,
        key=lambda item: item["score"],
        reverse=True
    )

    for rank, item in enumerate(ranked_results, start=1):

        if not (
            item["rows_matches"]
            or item["search_rows_matches"]
        ):
            continue

        rec = item["record"]
        representative_row = (
            rec.get("rows", [{}])[0]
            if rec.get("rows")
            else {}
        )
        target_row = (
            item["rows_matches"][0]
            if item["rows_matches"]
            else item["search_rows_matches"][0]
        )
        matched_row = item.get("matched_amount_row") or {}

        diagnostic.update({
            "target_exists": True,
            "in_rows": bool(item["rows_matches"]),
            "in_search_rows": bool(item["search_rows_matches"]),
            "score": item["score"],
            "rank": rank,
            "group_id": item["group_id"],
            "representative_row": build_row_debug_info(
                representative_row
            ),
            "target_row": build_row_debug_info(target_row),
            "matched_row_kept": is_debug_target_row(matched_row),
            "score_detail": item["score_detail"],
        })
        break

    if results and diagnostic["target_exists"]:
        for _, rec, _ in results:
            if rec.get("matched_amount_row") and is_debug_target_row(
                rec.get("matched_amount_row")
            ):
                diagnostic["passed_to_display"] = True
                break

    return diagnostic


# =========================================
# データ読込
# =========================================
def load_data():

    raw = []

    with open(
        DATA_PATH,
        encoding="utf-8-sig"
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            raw.append({

                k.strip():
                    str(v).strip()

                for k, v
                in row.items()

            })

    groups = split_records(raw)

    name_to_code = build_account_map(raw)

    records = []

    for g in groups:

        search_rows = build_search_rows(g)

        # 空伝票防止
        if not g:
            continue

        tokens = []

        for r in search_rows:

            text = " ".join(
                str(r.get(col, ""))
                for col in SEARCH_COLUMNS
            )

            tokens += tokenize(text)

        # =========================
        # 日付取得（columns統一）
        # =========================
        date = ""

        if g:
            date = g[0].get(COL_DATE, "")

        # =========================
        # 年取得（安全版）
        # =========================
        year = extract_year(date)

        records.append({

            "rows":
                g,

            "search_rows":
                search_rows,

            "tokens":
                list(set(tokens)),

            "year":
                year,
        })

    # =========================
    # token頻度
    # =========================
    freq = Counter()

    for rec in records:

        for t in rec["tokens"]:

            freq[t] += 1

    return (
        records,
        name_to_code,
        freq
    )


def get_account_suggestions(
    records,
    summary,
    opposite_account,
    sub_account=None,
    top_n=3
):

    summary_tokens = tokenize(summary)
    opposite_account = str(opposite_account or "").strip()
    sub_account = str(sub_account or "").strip()

    if not opposite_account:
        return []

    scores = Counter()
    counts = Counter()
    first_seen = {}

    for rec in records:

        for row in rec["rows"]:

            debit = str(row.get(COL_DEBIT, "") or "").strip()
            credit = str(row.get(COL_CREDIT, "") or "").strip()

            if opposite_account == debit:
                account = credit
            elif opposite_account == credit:
                account = debit
            else:
                continue

            account = str(account or "").strip()

            if (
                not account
                or account == opposite_account
                or account in EXCLUDED_SUGGESTION_ACCOUNTS
            ):
                continue

            text = " ".join([
                str(row.get("摘要", "") or ""),
                str(row.get("伝票摘要", "") or "")
            ])
            row_tokens = set(tokenize(text))

            score = sum(
                1
                for token in summary_tokens
                if token in row_tokens
            )

            if (
                sub_account
                and sub_account in (
                    row.get(COL_DEBIT_SUB, ""),
                    row.get(COL_CREDIT_SUB, "")
                )
            ):
                score += 3

            if score <= 0:
                continue

            if account not in first_seen:
                first_seen[account] = len(first_seen)

            scores[account] += score
            counts[account] += 1

    ordered = sorted(
        scores,
        key=lambda account: (
            -scores[account],
            -counts[account],
            first_seen[account]
        )
    )

    return [
        (account, scores[account])
        for account in ordered[:top_n]
    ]


# =========================================
# スコア（完全版・安全強化）
# =========================================
def calculate_score(
    rec,
    keyword,
    dept,
    amount,
    freq
):

    score = 0
    score_detail = []
    matched_amount_row = None

    tokens = rec["tokens"]
    search_rows = rec.get(
        "search_rows",
        rec["rows"]
    )

    q = tokenize(keyword)

    match = 0

    # =========================================
    # 補助科目一致
    # =========================================
    for r in search_rows:

        debit_sub = normalize(
            r.get(COL_DEBIT_SUB, "")
        )

        credit_sub = normalize(
            r.get(COL_CREDIT_SUB, "")
        )

        keyword_norm = normalize(keyword)

        if (
            keyword_norm
            and
            (
                keyword_norm in debit_sub
                or
                keyword_norm in credit_sub
            )
        ):

            score += 150

            score_detail.append(
                f"補助科目一致:{keyword} +150"
            )

            break

    for kw in q:
        for t in tokens:

            # 完全一致
            if kw == t:

                rare_bonus = max(
                    0,
                    50 - freq.get(kw, 0)
                )

                add_score = 120 + rare_bonus

                score += add_score

                score_detail.append(
                    f"完全一致:{kw} +{add_score}"
                )

                match += 1

            # 部分一致
            elif kw in t or t in kw:

                score += 50

                score_detail.append(
                    f"部分一致:{kw} +50"
                )

    if match >= 2:

        score += 150

        score_detail.append(
            "複数キーワード一致 +150"
        )

    # 部門
    if dept:
        for r in search_rows:
            if dept in get_department(r):
                score += 120

                score_detail.append(
                    f"部門一致:{dept} +120"
                )
                break

    # 金額
    if amount:
        score_before_amount = score
        total = get_voucher_total(rec["rows"])
        if total == amount:
            score += 200

            score_detail.append(
                f"金額一致:{amount} +200"
            )
            for r in search_rows:
                if (
                    to_int(r.get(COL_DEBIT_AMOUNT)) == amount
                    or to_int(r.get(COL_CREDIT_AMOUNT)) == amount
                ):
                    matched_amount_row = build_matched_row_info(
                        r,
                        amount=amount,
                        match_type="amount"
                    )
                    break
        else:
            best_row_match = find_best_amount_match(
                search_rows,
                amount
            )

            if best_row_match:
                row = best_row_match["row"]
                row_amount = best_row_match["amount"]
                proximity = best_row_match["proximity"]

                if proximity["label"] == "一致":
                    score += 200
                    matched_amount_row = build_matched_row_info(
                        row,
                        amount=row_amount,
                        match_type="amount",
                        input_amount=amount,
                        diff=proximity["diff"],
                        diff_rate=proximity["rate"]
                    )

                    score_detail.append(
                        "金額一致行:"
                        f"{row.get(COL_DATE, '')} "
                        f"{row.get(COL_DEBIT, '')}/"
                        f"{row.get(COL_CREDIT, '')} "
                        f"{row_amount} "
                        f"{row.get(COL_SUMMARY, '')} +200"
                    )
                elif score_before_amount > 0:
                    score += proximity["score"]
                    matched_amount_row = build_matched_row_info(
                        row,
                        amount=row_amount,
                        match_type="amount_near",
                        input_amount=amount,
                        diff=proximity["diff"],
                        diff_rate=proximity["rate"]
                    )

                    score_detail.append(
                        format_amount_proximity_detail(
                            amount,
                            row_amount,
                            proximity
                        )
                    )
            elif score_before_amount > 0:
                proximity = get_amount_proximity(
                    amount,
                    total
                )

                if proximity and proximity["label"] == "近似":
                    score += proximity["score"]

                    score_detail.append(
                        format_amount_proximity_detail(
                            amount,
                            total,
                            proximity
                        )
                    )

    # =========================================
    # 年度（相対化）
    # =========================================
    current_year = datetime.now().year

    diff = current_year - rec.get("year", current_year)

    if diff <= 1:
        year_weight = 1.0
    elif diff == 2:
        year_weight = 0.8
    elif diff == 3:
        year_weight = 0.6
    else:
        year_weight = 0.4

    score *= year_weight

    return int(score), score_detail, matched_amount_row

# =========================================
# テンプレ検索
# =========================================
#def search_templates(templates, keyword, dept):

    results = []

    for t in templates:

        kw = t.get("keyword", "")
        dept_t = t.get("dept", "")

        if kw and kw in keyword:

            score = 1000 + int(t.get("priority", 5)) * 100

            if dept and dept == dept_t:
                score += 200

            results.append((score, t))

    return results

# =========================================
# 検索
# =========================================
def get_journal_pattern_key(rec):

    matched_row = rec.get("matched_amount_row") or {}

    if matched_row:
        return (
            str(matched_row.get("debit_code") or matched_row.get("debit", "")),
            str(matched_row.get("credit_code") or matched_row.get("credit", "")),
            str(matched_row.get("debit_sub", "")),
            str(matched_row.get("credit_sub", "")),
        )

    rows = rec.get("rows", [])
    row = rows[0] if rows else {}

    return (
        str(row.get("借方科目") or row.get(COL_DEBIT, "")),
        str(row.get("貸方科目") or row.get(COL_CREDIT, "")),
        str(row.get("借方補助") or row.get(COL_DEBIT_SUB, "")),
        str(row.get("貸方補助") or row.get(COL_CREDIT_SUB, "")),
    )


def diversify_search_results(
    results,
    limit,
    max_per_pattern=2
):

    selected = []
    pattern_counts = Counter()

    for score, rec, score_detail in results:
        pattern_key = get_journal_pattern_key(rec)
        pattern_rank = pattern_counts[pattern_key] + 1

        if pattern_rank > max_per_pattern:
            continue

        result_rec = dict(rec)
        result_rec["pattern_key"] = pattern_key
        result_rec["pattern_rank"] = pattern_rank

        selected.append(
            (score, result_rec, score_detail)
        )
        pattern_counts[pattern_key] += 1

        if len(selected) >= limit:
            break

    return selected


def search(records, keyword, dept, amount, freq, limit=5):

    #templates = load_templates()

    results = []

    # テンプレ優先
    #results += search_templates(templates, keyword, dept)

    # 通常検索
    for rec in records:

        s, score_detail, matched_amount_row = calculate_score(
            rec,
            keyword,
            dept,
            amount,
            freq
        )

        if s > 50:
            result_rec = dict(rec)
            result_rec["matched_amount_row"] = matched_amount_row
            results.append(
                (s, result_rec, score_detail)
            )

    sorted_results = sorted(
        results,
        key=lambda x: x[0],
        reverse=True
    )

    return diversify_search_results(
        sorted_results,
        limit
    )

# =========================================
# 金額サジェスト（安全版）
# =========================================
def get_amount_suggestions(records, debit, credit, limit=5):

    candidates = []

    for rec in records:

        for r in rec["rows"]:

            d = r.get(COL_DEBIT)
            c = r.get(COL_CREDIT)

            if d == debit and c == credit:

                amt = to_int(r.get(COL_DEBIT_AMOUNT))

                if amt > 0:

                    candidates.append({
                        "amount": amt,
                        "date": r.get(COL_DATE, "")
                    })

    if not candidates:
        return None

    candidates.sort(key=lambda x: x["date"], reverse=True)

    recent = [c["amount"] for c in candidates[:limit]]

    avg = int(sum(c["amount"] for c in candidates) / len(candidates))

    return {
        "recent": recent,
        "avg": avg
    }

# =========================================
# CSV更新（完全版・安全設計）
# =========================================

import os

OUTPUT_PATH = "data/transactions.csv"


def clean_row(r):
    """
    1行の整形（超重要）
    """

    new = {}

    for k, v in r.items():

        if v is None:
            v = ""

        v = str(v).strip()

        # 空白統一
        v = re.sub(r"\s+", " ", v)

        new[k.strip()] = v

    return new


def is_valid_row(r):
    """
    壊れデータ除外
    """

    d = to_int(r.get(COL_DEBIT_AMOUNT))
    c = to_int(r.get(COL_CREDIT_AMOUNT))

    # 両方ゼロはNG
    if d == 0 and c == 0:
        return False

    # 日付なしNG
    if not r.get(COL_DATE):
        return False

    return True


def normalize_rows(rows):
    """
    行単位整形
    """

    result = []

    for r in rows:

        r = clean_row(r)

        require_journal_date(r.get(COL_DATE, ""))

        if not is_valid_row(r):
            continue

        result.append(r)

    return result

# =========================================
# 3年保持
# =========================================
KEEP_YEARS = KEEP_PAST_FISCAL_YEARS

def keep_recent_years(rows, today=None, start_month=None):

    if today is None:
        today = datetime.now().date()

    if start_month is None:
        start_month = load_system_settings()[
            "fiscal_year_start_month"
        ]

    minimum_fiscal_year = (
        get_current_fiscal_year(today, start_month)
        - KEEP_YEARS
    )

    result = []

    for r in rows:

        journal_date = require_journal_date(
            r.get(COL_DATE, "")
        )

        if get_fiscal_year(journal_date, start_month) >= minimum_fiscal_year:

            result.append(r)

    return result

def append_to_csv(
    new_rows,
    output_path=None,
    today=None,
    start_month=None,
):
    """
    CSVへ上追加（最重要）
    """

    if not new_rows:
        return

    target_path = output_path or OUTPUT_PATH

    # 既存読込
    existing = []

    if os.path.exists(target_path):

        with open(target_path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for r in reader:
                existing.append(r)

    # ヘッダー固定
    fieldnames = EPSON_COLUMNS

    # 上に追加
    combined = new_rows + existing

    # 3年保持
    combined = keep_recent_years(
        combined,
        today=today,
        start_month=start_month,
    )

    # 書き込み
    with open(
        target_path,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames
        )

        writer.writeheader()

        for r in combined:
            writer.writerow(r)


def update_search_csv(
    confirmed_docs,
    output_path=None,
    today=None,
    start_month=None,
):
    """
    メイン処理
    confirmed（登録済）をCSVへ反映
    """

    all_rows = []

    for doc in confirmed_docs:

        # 伝票単位で整形
        rows = normalize_rows(doc)

        if not rows:
            continue

        all_rows.extend(rows)

    # CSVへ追加
    append_to_csv(
        all_rows,
        output_path=output_path,
        today=today,
        start_month=start_month,
    )

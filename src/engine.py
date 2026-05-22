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

DATA_PATH = "data/transactions.csv"

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
            r.get("借方金額")
        )

        c = to_int(
            r.get("貸方金額")
        )

        date = r.get("伝票日付")

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

            r.get("借方科目名", "")
            +
            r.get("貸方科目名", "")

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

        d = r.get("借方科目名")
        c = r.get("貸方科目名")

        d_amt = to_int(
            r.get("借方金額")
        )

        c_amt = to_int(
            r.get("貸方金額")
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

        new_rows.append({

            "伝票日付":
                s["row"].get(
                    "伝票日付",
                    ""
                ),

            "摘要":
                s["row"].get(
                    "摘要",
                    ""
                ),

            "借方科目名":
                s["account"],

            "貸方科目名":
                t["account"],

            "借方金額":
                str(amt),

            "貸方金額":
                str(amt),

            "取引先":
                s["row"].get(
                    "取引先",
                    ""
                ),

            "推定変換":
                "1",
        })

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
            r.get("借方科目名")
            and
            r.get("借方科目")
        ):

            mp[
                r["借方科目名"]
            ] = r["借方科目"]

        if (
            r.get("貸方科目名")
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
        to_int(r.get("借方金額"))
        for r in rows
    )

    c_total = sum(
        to_int(r.get("貸方金額"))
        for r in rows
    )

    return max(
        d_total,
        c_total
    )


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

        g = explode_fukugo(g)

        # 空伝票防止
        if not g:
            continue

        tokens = []

        for r in g:

            text = (

                r.get("摘要", "")
                + " " +

                r.get("借方科目名", "")
                + " " +

                r.get("貸方科目名", "")
                + " " +

                r.get("取引先", "")

            )

            tokens += tokenize(text)

        date = ""

        if g:
            date = g[0].get(
                "伝票日付",
                ""
            )

        year = datetime.now().year

        try:
            year = int(date[:4])
        except:
            pass

        records.append({

            "rows":
                g,

            "tokens":
                list(set(tokens)),

            "year":
                year,
        })

    # token頻度
    freq = Counter()

    for rec in records:

        for t in rec["tokens"]:

            freq[t] += 1

    return (
        records,
        name_to_code,
        freq
    )


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

    tokens = rec["tokens"]

    q = tokenize(keyword)

    match = 0

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
        for r in rec["rows"]:
            if dept in get_department(r):
                score += 120

                score_detail.append(
                    f"部門一致:{dept} +120"
                )
                break

    # 金額
    if amount:
        total = get_voucher_total(rec["rows"])
        if total == amount:
            score += 200

            score_detail.append(
                f"金額一致:{amount} +200"
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

    return int(score), score_detail

# =========================================
# テンプレ検索
# =========================================
def search_templates(templates, keyword, dept):

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
def search(records, keyword, dept, amount, freq):

    templates = load_templates()

    results = []

    # テンプレ優先
    results += search_templates(templates, keyword, dept)

    # 通常検索
    for rec in records:

        s, score_detail = calculate_score(
            rec,
            keyword,
            dept,
            amount,
            freq
        )

        if s > 50:
            results.append(
                (s, rec, score_detail)
            )

    return sorted(
        results,
        key=lambda x: x[0],
        reverse=True
    )[:5]

# =========================================
# 金額サジェスト（安全版）
# =========================================
def get_amount_suggestions(records, debit, credit, limit=5):

    candidates = []

    for rec in records:

        for r in rec["rows"]:

            d = r.get("借方科目名")
            c = r.get("貸方科目名")

            if d == debit and c == credit:

                amt = to_int(r.get("借方金額"))

                if amt > 0:

                    candidates.append({
                        "amount": amt,
                        "date": r.get("伝票日付", "")
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

    d = to_int(r.get("借方金額"))
    c = to_int(r.get("貸方金額"))

    # 両方ゼロはNG
    if d == 0 and c == 0:
        return False

    # 日付なしNG
    if not r.get("伝票日付"):
        return False

    return True


def normalize_rows(rows):
    """
    行単位整形
    """

    result = []

    for r in rows:

        r = clean_row(r)

        if not is_valid_row(r):
            continue

        result.append(r)

    return result


def append_to_csv(new_rows):
    """
    CSVへ上追加（最重要）
    """

    if not new_rows:
        return

    # 既存読込
    existing = []

    if os.path.exists(OUTPUT_PATH):

        with open(OUTPUT_PATH, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for r in reader:
                existing.append(r)

    # ヘッダー取得
    if existing:
        fieldnames = list(existing[0].keys())
    else:
        fieldnames = list(new_rows[0].keys())

    # 上に追加
    combined = new_rows + existing

    # 書き込み
    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8-sig") as f:

        writer = csv.DictWriter(f, fieldnames=fieldnames)

        writer.writeheader()

        for r in combined:
            writer.writerow(r)


def update_search_csv(confirmed_docs):
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
    append_to_csv(all_rows)
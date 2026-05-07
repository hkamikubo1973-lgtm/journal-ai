import csv
import re
import unicodedata
import copy
from collections import Counter
from datetime import datetime

DATA_PATH = "data/transactions.csv"

STOP_WORDS = {
    "株式会社",
    "有限会社",
    "御中",
    "様",
}


# =========================
# ■ 正規化
# =========================
def normalize(text):

    if not text:
        return ""

    text = unicodedata.normalize("NFKC", str(text)).lower()

    text = "".join(
        chr(ord(c) - 0x60) if "ァ" <= c <= "ン" else c
        for c in text
    )

    return re.sub(r"\s+", " ", text).strip()


# =========================
# ■ tokenize
# =========================
def tokenize(text):

    text = normalize(text)

    tokens = re.split(r"[ 　/()（）・\-_]", text)

    result = []

    for t in tokens:

        if not t:
            continue

        if t in STOP_WORDS:
            continue

        result.append(t)

        if len(t) >= 2:
            for i in range(len(t) - 1):
                result.append(t[i:i+2])

    return list(set(result))


# =========================
# ■ 金額変換
# =========================
def to_int(v):

    try:
        return int(float(str(v).replace(",", "")))
    except:
        return 0


# =========================
# ■ 部門
# =========================
def get_department(r):

    return r.get("借方部門名") or r.get("貸方部門名") or ""


# =========================
# ■ 伝票分割
# =========================
def split_records(rows):

    records = []
    current = []

    d_sum = 0
    c_sum = 0

    last_date = None

    for r in rows:

        d = to_int(r.get("借方金額"))
        c = to_int(r.get("貸方金額"))

        date = r.get("伝票日付")

        # 日付変化で強制分割
        if last_date and date != last_date and current:

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


# =========================
# ■ 資金複合分解（FIFO簡易版）
# =========================
def explode_fukugo(rows):

    has_fukugo = any(
        "資金複合" in (
            r.get("借方科目名", "") +
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

        d_amt = to_int(r.get("借方金額"))
        c_amt = to_int(r.get("貸方金額"))

        # =========================
        # source
        # 普通預金 / 資金複合
        # =========================
        if c == "資金複合" and d != "資金複合":

            if d_amt > 0:

                sources.append({
                    "account": d,
                    "amount": d_amt,
                    "remain": d_amt,
                    "row": r
                })

        # =========================
        # target
        # 資金複合 / 売上
        # =========================
        elif d == "資金複合" and c != "資金複合":

            if c_amt > 0:

                targets.append({
                    "account": c,
                    "amount": c_amt,
                    "remain": c_amt,
                    "row": r
                })

        # =========================
        # 通常行
        # =========================
        else:
            normal_rows.append(r)

    # =========================
    # source / target 不足
    # 分解不可 → 元返却
    # =========================
    if not sources or not targets:
        return rows

    new_rows = copy.deepcopy(normal_rows)

    s_idx = 0
    t_idx = 0

    # =========================
    # FIFO分配
    # =========================
    while s_idx < len(sources) and t_idx < len(targets):

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
                s["row"].get("伝票日付", ""),

            "摘要":
                s["row"].get("摘要", ""),

            "借方科目名":
                s["account"],

            "貸方科目名":
                t["account"],

            "借方金額":
                str(amt),

            "貸方金額":
                str(amt),

            "取引先":
                s["row"].get("取引先", ""),

            # 推定変換マーク
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

    # =========================
    # 分解失敗時
    # =========================
    if not new_rows:
        return rows

    # =========================
    # 残高未消化チェック
    # =========================
    remain_exists = any(
        s["remain"] > 0
        for s in sources
    ) or any(
        t["remain"] > 0
        for t in targets
    )

    # 未消化あり
    # → 元返却（安全優先）
    if remain_exists:
        return rows

    return new_rows

# =========================
# ■ 科目マップ
# =========================
def build_account_map(rows):

    mp = {}

    for r in rows:

        if r.get("借方科目名") and r.get("借方科目"):
            mp[r["借方科目名"]] = r["借方科目"]

        if r.get("貸方科目名") and r.get("貸方科目"):
            mp[r["貸方科目名"]] = r["貸方科目"]

    return mp


# =========================
# ■ 伝票総額
# =========================
def get_voucher_total(rows):

    d_total = sum(
        to_int(r.get("借方金額"))
        for r in rows
    )

    c_total = sum(
        to_int(r.get("貸方金額"))
        for r in rows
    )

    return max(d_total, c_total)


# =========================
# ■ データ読込
# =========================
def load_data():

    raw = []

    with open(DATA_PATH, encoding="utf-8-sig") as f:

        reader = csv.DictReader(f)

        for row in reader:

            raw.append({
                k.strip(): str(v).strip()
                for k, v in row.items()
            })

    groups = split_records(raw)

    name_to_code = build_account_map(raw)

    records = []

    for g in groups:

        g = explode_fukugo(g)

        # =========================
        # 空伝票防止
        # =========================
        if not g:
            print("⚠ 空伝票スキップ")
            continue

        tokens = []

        for r in g:

            text = (
                r.get("摘要", "") + " " +
                r.get("借方科目名", "") + " " +
                r.get("貸方科目名", "") + " " +
                r.get("取引先", "")
            )

            tokens += tokenize(text)

        # =========================
        # 年度取得
        # =========================
        date = ""

        if g:
            date = g[0].get("伝票日付", "")

        year = datetime.now().year

        try:
            year = int(date[:4])
        except:
            pass

        records.append({
            "rows": g,
            "tokens": list(set(tokens)),
            "year": year,
        })

    # =========================
    # token頻度
    # =========================
    freq = Counter()

    for rec in records:
        for t in rec["tokens"]:
            freq[t] += 1

    return records, name_to_code, freq

# =========================
# ■ スコア
# =========================
def calculate_score(rec, keyword, dept, amount, freq):

    score = 0

    tokens = rec["tokens"]

    q = tokenize(keyword)

    match = 0

    for kw in q:

        for t in tokens:

            if kw == t:

                rare_bonus = max(0, 50 - freq.get(kw, 0))

                score += 120 + rare_bonus

                match += 1

            elif kw in t:
                score += 50

    if match >= 2:
        score += 150

    # 部門
    if dept:

        for r in rec["rows"]:

            if dept in get_department(r):
                score += 120
                break

    # 金額
    if amount:

        total = get_voucher_total(rec["rows"])

        if total == amount:
            score += 200

    # 年度重み
    current_year = datetime.now().year

    diff = current_year - rec.get("year", current_year)

    year_weight = {
        0: 1.0,
        1: 0.8,
        2: 0.6,
    }.get(diff, 0.4)

    score *= year_weight

    return int(score)


# =========================
# ■ 検索
# =========================
def search(records, keyword, dept, amount, freq):

    res = []

    for rec in records:

        s = calculate_score(
            rec,
            keyword,
            dept,
            amount,
            freq
        )

        if s > 50:
            res.append((s, rec))

    return sorted(
        res,
        key=lambda x: x[0],
        reverse=True
    )[:5]


# =========================
# ■ 表示
# =========================
def show_results(results):

    print("\n候補:")

    for i, (score, rec) in enumerate(results, 1):

        print(f"\n{i}. ★スコア:{score}")

        d_sum = 0
        c_sum = 0

        for r in rec["rows"]:

            d = to_int(r.get("借方金額"))
            c = to_int(r.get("貸方金額"))

            d_sum += d
            c_sum += c

            mark = ""

            if r.get("推定変換") == "1":
                mark = " [推定]"

            print(
                f"{r.get('伝票日付')} | "
                f"{r.get('摘要')} | "
                f"{r.get('借方科目名')} {d} / "
                f"{r.get('貸方科目名')} {c} | "
                f"{get_department(r)}"
                f"{mark}"
            )

        print(f"--- 合計：借方={d_sum} / 貸方={c_sum}")

        if d_sum == c_sum:
            print("→ 貸借一致")
        else:
            print("⚠ 不一致")


# =========================
# ■ 編集（必須）
# =========================
def edit_entry(rows, name_to_code):

    rows = copy.deepcopy(rows)

    print("\n=== 編集（必須）===")

    allow_same = {
        "仮払金",
        "立替金",
    }

    for i, r in enumerate(rows, 1):

        print(f"\n--- 行{i} ---")

        r["摘要"] = (
            input(f"摘要({r['摘要']}): ")
            or r["摘要"]
        )

        while True:

            d = (
                input(f"借方({r['借方科目名']}): ")
                or r["借方科目名"]
            )

            if d not in name_to_code:
                print("⚠ 未登録")
                continue

            c = (
                input(f"貸方({r['貸方科目名']}): ")
                or r["貸方科目名"]
            )

            if c not in name_to_code:
                print("⚠ 未登録")
                continue

            # 同一科目禁止
            if d == c and d not in allow_same:
                print("⚠ 同一科目は禁止")
                continue

            r["借方科目名"] = d
            r["貸方科目名"] = c

            break

        is_debit = to_int(r.get("借方金額")) > 0

        current_amt = (
            r.get("借方金額")
            if is_debit
            else r.get("貸方金額")
        )

        amt = input(f"金額({current_amt}): ")

        if amt:

            if is_debit:
                r["借方金額"] = amt
                r["貸方金額"] = "0"
            else:
                r["貸方金額"] = amt
                r["借方金額"] = "0"

    return rows


# =========================
# ■ CSV出力
# =========================
def export_csv(entries, name_to_code):

    with open(
        "output.csv",
        "w",
        newline="",
        encoding="cp932"
    ) as f:

        w = csv.writer(f)

        w.writerow([
            "伝票日付",
            "借方科目",
            "貸方科目",
            "金額"
        ])

        for doc in entries:

            for r in doc:

                amt = max(
                    to_int(r.get("借方金額")),
                    to_int(r.get("貸方金額"))
                )

                w.writerow([
                    r.get("伝票日付", ""),
                    name_to_code.get(
                        r["借方科目名"],
                        ""
                    ),
                    name_to_code.get(
                        r["貸方科目名"],
                        ""
                    ),
                    amt
                ])

    print("✔ CSV出力完了")


# =========================
# ■ メイン
# =========================
if __name__ == "__main__":

    records, name_to_code, freq = load_data()

    confirmed = []

    while True:

        print("\n==== メニュー ====")
        print("1：検索")
        print("2：CSV出力して終了")

        cmd = input("> ")

        if cmd == "2":
            break

        dept = input("部門：")

        kw = input("検索ワード：")

        amt = input("金額：")

        amt = (
            int(re.sub(r"[^\d]", "", amt))
            if amt
            else None
        )

        res = search(
            records,
            kw,
            dept,
            amt,
            freq
        )

        if not res:
            print("候補なし")
            continue

        show_results(res)

        idx_input = input("番号選択：")

        try:

            idx = int(idx_input)

            if not (1 <= idx <= len(res)):
                raise ValueError

        except:

            print("⚠ 無効な番号")
            continue

        selected = res[idx - 1][1]

        edited = edit_entry(
            selected["rows"],
            name_to_code
        )

        print("\n登録しますか？ (y/n)")

        if input("> ").lower() == "y":

            confirmed.append(edited)

            print("✔ 登録完了")

    export_csv(confirmed, name_to_code)
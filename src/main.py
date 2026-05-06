import csv
import re
import unicodedata
import copy

DATA_PATH = "data/transactions.csv"

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

    ngrams = []
    for t in tokens:
        if len(t) >= 2:
            for i in range(len(t)-1):
                ngrams.append(t[i:i+2])

    return list(set([t for t in tokens if t] + ngrams))


# =========================
# ■ 金額変換
# =========================
def to_int(v):
    try:
        return int(float(v))
    except:
        return 0


# =========================
# ■ 部門
# =========================
def get_department(r):
    return r.get("借方部門名") or r.get("貸方部門名") or ""


# =========================
# ■ 伝票分割（最重要）
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

        if last_date and date != last_date and current:
            records.append(current)
            current = []
            d_sum = c_sum = 0

        current.append(r)

        d_sum += d
        c_sum += c

        if d_sum == c_sum and d_sum > 0:
            records.append(current)
            current = []
            d_sum = c_sum = 0

        last_date = date

    if current:
        records.append(current)

    return records


# =========================
# ■ 資金複合分解（完全版）
# =========================
def explode_fukugo(rows):

    if not any("資金複合" in (r.get("借方科目名","") + r.get("貸方科目名","")) for r in rows):
        return rows

    base = None

    for r in rows:
        d = r.get("借方科目名")
        c = r.get("貸方科目名")

        if c == "資金複合" and d != "資金複合":
            base = d
        elif d == "資金複合" and c != "資金複合":
            base = c

    if not base:
        return rows

    new_rows = []

    for r in rows:

        d = r.get("借方科目名")
        c = r.get("貸方科目名")

        d_amt = to_int(r.get("借方金額"))
        c_amt = to_int(r.get("貸方金額"))

        # 分解
        if d == "資金複合" and c != "資金複合":
            new_rows.append({
                **r,
                "借方科目名": base,
                "貸方科目名": c,
                "借方金額": c_amt,
                "貸方金額": c_amt
            })

        elif c == "資金複合" and d != "資金複合":
            new_rows.append({
                **r,
                "借方科目名": d,
                "貸方科目名": base,
                "借方金額": d_amt,
                "貸方金額": d_amt
            })

        elif d == base and c == "資金複合":
            continue

        else:
            new_rows.append(r)

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
# ■ データ読込
# =========================
def load_data():

    raw = []

    with open(DATA_PATH, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw.append({k.strip(): str(v).strip() for k,v in row.items()})

    groups = split_records(raw)
    name_to_code = build_account_map(raw)

    records = []

    for g in groups:

        g = explode_fukugo(g)

        tokens = []

        for r in g:
            text = (
                r.get("摘要","") + " " +
                r.get("借方科目名","") + " " +
                r.get("貸方科目名","") + " " +
                r.get("取引先","")
            )
            tokens += tokenize(text)

        records.append({
            "rows": g,
            "tokens": list(set(tokens))
        })

    return records, name_to_code


# =========================
# ■ スコア
# =========================
def calculate_score(rec, keyword, dept, amount):

    score = 0
    tokens = rec["tokens"]

    q = tokenize(keyword)
    match = 0

    for kw in q:
        for t in tokens:
            if kw == t:
                score += 120
                match += 1
            elif kw in t:
                score += 50

    if match >= 2:
        score += 150

    if dept:
        for r in rec["rows"]:
            if dept in get_department(r):
                score += 120
                break

    if amount:
        total = sum(
            to_int(r.get("借方金額")) or to_int(r.get("貸方金額"))
            for r in rec["rows"]
        )
        if total == amount:
            score += 200

    return score


# =========================
# ■ 検索
# =========================
def search(records, keyword, dept, amount):

    res = []

    for rec in records:
        s = calculate_score(rec, keyword, dept, amount)
        if s > 50:
            res.append((s, rec))

    return sorted(res, key=lambda x: x[0], reverse=True)[:5]


# =========================
# ■ 表示
# =========================
def show_results(results):

    print("\n候補:")

    for i,(score,rec) in enumerate(results,1):

        print(f"\n{i}. ★スコア:{score}")

        d_sum = c_sum = 0

        for r in rec["rows"]:

            d = to_int(r.get("借方金額"))
            c = to_int(r.get("貸方金額"))

            d_sum += d
            c_sum += c

            print(
                f"{r.get('伝票日付')} | "
                f"{r.get('摘要')} | "
                f"{r.get('借方科目名')} {d} / "
                f"{r.get('貸方科目名')} {c} | "
                f"{get_department(r)}"
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

    for i,r in enumerate(rows,1):

        print(f"\n--- 行{i} ---")

        r["摘要"] = input(f"摘要({r['摘要']}): ") or r["摘要"]

        while True:
            d = input(f"借方({r['借方科目名']}): ") or r["借方科目名"]
            if d in name_to_code:
                r["借方科目名"] = d
                break
            print("⚠ 未登録")

        while True:
            c = input(f"貸方({r['貸方科目名']}): ") or r["貸方科目名"]
            if c in name_to_code:
                r["貸方科目名"] = c
                break
            print("⚠ 未登録")

        amt = input(f"金額({r.get('借方金額') or r.get('貸方金額')}): ")
        if amt:
            if r.get("借方金額"):
                r["借方金額"] = amt
            else:
                r["貸方金額"] = amt

    return rows


# =========================
# ■ CSV出力
# =========================
def export_csv(entries, name_to_code):

    with open("output.csv","w",newline="",encoding="cp932") as f:

        w = csv.writer(f)
        w.writerow(["伝票日付","借方科目","貸方科目","金額"])

        for doc in entries:
            for r in doc:

                w.writerow([
                    r["伝票日付"],
                    name_to_code.get(r["借方科目名"],""),
                    name_to_code.get(r["貸方科目名"],""),
                    r.get("借方金額") or r.get("貸方金額")
                ])

    print("CSV出力完了")


# =========================
# ■ メイン
# =========================
if __name__ == "__main__":

    records, name_to_code = load_data()

    confirmed = []

    while True:

        print("\n==== メニュー ====")
        print("1：検索")
        print("2：CSV出力して終了")

        if input("> ") == "2":
            break

        dept = input("部門：")
        kw = input("検索ワード：")
        amt = input("金額：")

        amt = int(re.sub(r"[^\d]","",amt)) if amt else None

        res = search(records, kw, dept, amt)

        show_results(res)

        idx = input("番号選択：")

        if not idx.isdigit():
            continue

        selected = res[int(idx)-1][1]

        edited = edit_entry(selected["rows"], name_to_code)

        print("\n登録しますか？ (y/n)")
        if input("> ").lower()=="y":
            confirmed.append(edited)
            print("✔ 登録完了")

    export_csv(confirmed, name_to_code)
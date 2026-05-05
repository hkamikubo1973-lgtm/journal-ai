```python
import csv
import re
from collections import Counter
from datetime import datetime

DATA_PATH = "../data/transactions.csv"


# =========================
# ■ 文字正規化（検索用）
# =========================
def normalize(text):
    if not text:
        return ""
    text = str(text).lower()
    text = text.replace("　", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# =========================
# ■ 日付取得（BOM・ズレ対応）
# =========================
def get_date(row):
    for k in row.keys():
        key = k.strip().lower().replace("\ufeff", "")
        if key == "date":
            return str(row[k]).strip()
    return ""


# =========================
# ■ 日付フォーマット
# =========================
def format_date(date_str):
    try:
        date_str = str(date_str).strip()

        if len(date_str) == 8:
            d = datetime.strptime(date_str, "%Y%m%d")
        else:
            d = datetime.strptime(date_str, "%Y-%m-%d")

        return d.strftime("%Y-%m-%d")
    except:
        return "----"


# =========================
# ■ 日付スコア（新しいほど優遇）
# =========================
def get_date_score(date_str):
    try:
        if len(date_str) == 8:
            d = datetime.strptime(date_str, "%Y%m%d")
        else:
            d = datetime.strptime(date_str, "%Y-%m-%d")

        days = (datetime.now() - d).days
        return max(0, 30 - days)
    except:
        return 0


# =========================
# ■ CSV読込（完成版）
# =========================
def load_data():
    records = []

    with open(DATA_PATH, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        for row in reader:
            fixed = {}

            for k, v in row.items():
                key = k.strip().lower().replace("\ufeff", "")
                fixed[key] = str(v).strip()

            # ★ 検索用と表示用を分離
            fixed["description_raw"] = fixed.get("description", "")
            fixed["description"] = normalize(fixed.get("description", ""))

            fixed["debit"] = normalize(fixed.get("debit", ""))
            fixed["credit"] = normalize(fixed.get("credit", ""))

            records.append(fixed)

    return records


# =========================
# ■ 完全一致マップ
# =========================
def build_exact_map(records):
    m = {}
    for r in records:
        desc = r.get("description", "")
        if desc:
            m[desc] = r
    return m


# =========================
# ■ 頻度
# =========================
def build_frequency(records):
    c = Counter()
    for r in records:
        key = (r.get("debit", ""), r.get("credit", ""))
        c[key] += 1
    return c


# =========================
# ■ 検索エンジン
# =========================
def search(records, keyword, exact_map, freq_map):
    keyword = normalize(keyword)

    # 完全一致
    if keyword in exact_map:
        return [exact_map[keyword]]

    keywords = keyword.split()
    results = []

    for r in records:
        desc = r.get("description", "")
        debit = r.get("debit", "")
        credit = r.get("credit", "")

        full = f"{desc} {debit} {credit}"

        score = 0

        for kw in keywords:
            if desc.startswith(kw):
                score += 80

            if kw in full:
                score += 50

            score += full.count(kw) * 15

        # 頻度スコア
        score += freq_map.get((debit, credit), 0) * 5

        # 金額スコア
        try:
            amount = int(r.get("amount", 0))
            score += amount * 0.00001
        except:
            pass

        # 日付スコア
        date_val = get_date(r)
        score += get_date_score(date_val)

        if score > 0:
            results.append((score, r))

    results.sort(reverse=True, key=lambda x: x[0])
    return [r for score, r in results[:5]]


# =========================
# ■ メイン
# =========================
if __name__ == "__main__":
    data = load_data()

    exact_map = build_exact_map(data)
    freq_map = build_frequency(data)

    while True:
        keyword = input("検索ワード：").strip()

        if keyword == "":
            print("終了")
            break

        results = search(data, keyword, exact_map, freq_map)

        print("\n候補:")

        if not results:
            print("該当なし")

        for r in results:
            date_val = get_date(r)

            print(
                f"{format_date(date_val)} | "
                f"{r.get('description_raw','')} | "
                f"{r.get('debit','')} / {r.get('credit','')} | "
                f"{r.get('amount','')}"
            )

        print("-" * 50)
```

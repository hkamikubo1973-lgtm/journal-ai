import csv
import re
from datetime import datetime
from collections import defaultdict

DATA_PATH = "data/transactions.csv"


# =========================
# ■ 正規化
# =========================
def normalize(text):
    if not text:
        return ""
    text = str(text).lower()
    text = text.replace("　", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# =========================
# ■ tokenize（N-gram）
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
# ■ CSV読み込み → 伝票化
# =========================
def load_data():
    rows = []

    with open(DATA_PATH, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            fixed = {}
            for k, v in row.items():
                key = k.strip().lower().replace("\ufeff", "")
                fixed[key] = str(v).strip()
            rows.append(fixed)

    # 仮グループ（簡易：日付＋description）
    groups = defaultdict(list)

    for r in rows:
        key = r.get("date") + "_" + r.get("description", "")
        groups[key].append(r)

    records = []

    for k, g in groups.items():
        tokens = []
        for r in g:
            tokens += tokenize(r.get("description", ""))

        records.append({
            "v_id": k,
            "rows": g,
            "tokens": list(set(tokens))
        })

    return records


# =========================
# ■ 検索
# =========================
def search(records, keyword):
    results = []

    query_tokens = tokenize(keyword)

    for rec in records:
        score = 0

        for kw in query_tokens:
            for t in rec["tokens"]:
                if kw == t:
                    score += 100
                elif kw in t or t in kw:
                    score += 40

        if score > 0:
            results.append((score, rec))

    results.sort(reverse=True)
    return [r for s, r in results[:5]]


# =========================
# ■ 候補表示
# =========================
def select_result(results):
    if not results:
        print("該当なし")
        return None

    print("\n候補:")

    for i, rec in enumerate(results, 1):
        print(f"\n{i}. =====")

        for r in rec["rows"]:
            print(
                f"{r.get('debit')} / {r.get('credit')} | {r.get('amount')}"
            )

    print("\n" + "-" * 40)

    choice = input("番号選択（Enterスキップ）：")

    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(results):
            return results[idx]

    return None


# =========================
# ■ 複合入力
# =========================
def input_confirm():
    print("\n==== 伝票入力 ====")

    entries = []

    while True:
        debit = input("借方（Enterで終了）：")
        if debit == "":
            break

        credit = input("貸方：")
        amount = input("金額：")

        entries.append({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "debit": debit,
            "credit": credit,
            "amount": amount
        })

    return entries


# =========================
# ■ 一覧表示
# =========================
def show_entries(entries):
    print("\n==== 入力済一覧 ====")

    for i, doc in enumerate(entries, 1):
        print(f"\n{i}件目")

        for r in doc:
            print(f"{r['debit']} / {r['credit']} | {r['amount']}")


# =========================
# ■ 修正
# =========================
def edit_entry(entries):
    show_entries(entries)

    idx = input("\n修正する番号：")

    if idx.isdigit():
        i = int(idx) - 1
        if 0 <= i < len(entries):
            print("再入力してください")
            entries[i] = input_confirm()


# =========================
# ■ 削除
# =========================
def delete_entry(entries):
    show_entries(entries)

    idx = input("\n削除する番号：")

    if idx.isdigit():
        i = int(idx) - 1
        if 0 <= i < len(entries):
            entries.pop(i)
            print("削除しました")


# =========================
# ■ CSV出力
# =========================
def export_csv(entries):
    if not entries:
        print("データなし")
        return

    with open("output.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "debit", "credit", "amount"])

        for doc in entries:
            for r in doc:
                writer.writerow([
                    r["date"],
                    r["debit"],
                    r["credit"],
                    r["amount"]
                ])

    print("\n✅ CSV出力完了")


# =========================
# ■ メイン
# =========================
if __name__ == "__main__":
    records = load_data()

    confirmed = []

    while True:
        print("\n==== メニュー ====")
        print("1：検索して入力")
        print("2：一覧確認")
        print("3：修正")
        print("4：削除")
        print("5：CSV出力して終了")

        mode = input("> ")

        if mode == "5":
            break

        elif mode == "2":
            show_entries(confirmed)

        elif mode == "3":
            edit_entry(confirmed)

        elif mode == "4":
            delete_entry(confirmed)

        elif mode == "1":
            keyword = input("検索ワード：")

            results = search(records, keyword)
            select_result(results)

            doc = input_confirm()
            confirmed.append(doc)

            print("✔ 保存済")

    export_csv(confirmed)
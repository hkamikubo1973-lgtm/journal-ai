# =========================================
# 仕訳検索システム（安定版・編集対応）
# =========================================

import streamlit as st
import pandas as pd
import copy
from datetime import datetime

from engine import (
    load_data,
    search,
    get_department,
    to_int,
    get_amount_suggestions
)

def split_journal(rows):

    debits = []
    credits = []

    for r in rows:
        d_amt = to_int(r.get("借方金額"))
        c_amt = to_int(r.get("貸方金額"))

        if d_amt > 0:
            debits.append((r, d_amt))

        if c_amt > 0:
            credits.append((r, c_amt))

    # =====================================
    # ★ 分解して良い条件
    # =====================================
    # 1対多 or 多対1 かつ片側が1行のみ
    if len(debits) == 1 and len(credits) > 1:

        d_row, _ = debits[0]
        result = []

        for c_row, c_amt in credits:
            new = copy.deepcopy(d_row)
            new["貸方科目名"] = c_row.get("貸方科目名", "")
            new["貸方補助科目名"] = c_row.get("貸方補助科目名", "")
            new["貸方金額"] = str(c_amt)
            new["借方金額"] = str(c_amt)
            result.append(new)

        return result

    elif len(credits) == 1 and len(debits) > 1:

        c_row, _ = credits[0]
        result = []

        for d_row, d_amt in debits:
            new = copy.deepcopy(d_row)
            new["貸方科目名"] = c_row.get("貸方科目名", "")
            new["貸方補助科目名"] = c_row.get("貸方補助科目名", "")
            new["貸方金額"] = str(d_amt)
            new["借方金額"] = str(d_amt)
            result.append(new)

        return result

    # =====================================
    # ★ それ以外は絶対分解しない
    # =====================================
    return rows

# =========================================
# 初期設定
# =========================================
st.set_page_config(page_title="仕訳検索", layout="wide")
st.title("📘 仕訳検索システム")

# =========================================
# データロード
# =========================================
@st.cache_data
def cached_load():
    return load_data()

records, name_to_code, freq = cached_load()

account_master = sorted(list(name_to_code.keys()))

department_master = sorted(
    list(set(
        get_department(r)
        for rec in records
        for r in rec["rows"]
        if get_department(r)
    ))
)

sub_master = sorted(
    list(set(
        r.get("借方補助科目名", "")
        for rec in records
        for r in rec["rows"]
    ) | set(
        r.get("貸方補助科目名", "")
        for rec in records
        for r in rec["rows"]
    ))
)

# =========================================
# セッション
# =========================================
if "results" not in st.session_state:
    st.session_state.results = []

if "confirmed" not in st.session_state:
    st.session_state.confirmed = []

# =========================================
# サイドバー（検索）
# =========================================
st.sidebar.header("🔍 検索")

dept = st.sidebar.selectbox("部門（任意）", [""] + department_master)
keyword = st.sidebar.text_input("キーワード")
amount_str = st.sidebar.text_input("金額")

amount = None
if amount_str:
    try:
        amount = int(amount_str.replace(",", ""))
    except:
        st.sidebar.error("金額エラー")

if st.sidebar.button("検索"):
    st.session_state.results = search(
        records,
        keyword,
        dept if dept else None,
        amount,
        freq
    )

# =========================================
# 日付
# =========================================
st.subheader("📅 伝票日付")

process_date_obj = st.date_input("日付", datetime.today())
process_date = process_date_obj.strftime("%Y%m%d")

st.divider()

# =========================================
# 検索結果
# =========================================
results = st.session_state.results

if not results:
    st.info("検索してください")

else:

    st.success(f"{len(results)}件ヒット")

    for idx, (score, rec) in enumerate(results, 1):

        if not isinstance(rec, dict) or "rows" not in rec:
            continue

        # =========================================
        # ★ ここが重要：仕訳分解
        # =========================================
        rows = split_journal(rec["rows"])

        doc_id = f"{idx}_{rows[0].get('摘要','')}"

        with st.expander(f"{idx}. ★{score} {rows[0].get('摘要','')}"):

            edited_rows = []
            d_sum = 0
            c_sum = 0

            for r_idx, r in enumerate(rows):

                st.markdown(f"### 行 {r_idx+1}")

                col1, col2 = st.columns(2)

                # =================================
                # 借方
                # =================================
                with col1:
                    default_debit = r.get("借方科目名", "")
                    debit = st.selectbox(
                        "借方",
                        account_master,
                        index=account_master.index(default_debit)
                        if default_debit in account_master else 0,
                        key=f"d_{doc_id}_{r_idx}"
                    )

                # =================================
                # 貸方
                # =================================
                with col2:
                    default_credit = r.get("貸方科目名", "")
                    credit = st.selectbox(
                        "貸方",
                        account_master,
                        index=account_master.index(default_credit)
                        if default_credit in account_master else 0,
                        key=f"c_{doc_id}_{r_idx}"
                    )

                # =================================
                # 補助科目
                # =================================
                col3, col4 = st.columns(2)

                with col3:
                    default_ds = r.get("借方補助科目名", "")
                    debit_sub = st.selectbox(
                        "借方補助",
                        [""] + sub_master,
                        index=([""] + sub_master).index(default_ds)
                        if default_ds in ([""] + sub_master) else 0,
                        key=f"ds_{doc_id}_{r_idx}"
                    )

                with col4:
                    default_cs = r.get("貸方補助科目名", "")
                    credit_sub = st.selectbox(
                        "貸方補助",
                        [""] + sub_master,
                        index=([""] + sub_master).index(default_cs)
                        if default_cs in ([""] + sub_master) else 0,
                        key=f"cs_{doc_id}_{r_idx}"
                    )

                # =================================
                # 金額サジェスト
                # =================================
                suggest = get_amount_suggestions(records, debit, credit)

                if suggest:
                    st.caption(
                        f"平均:{suggest['avg']:,} / "
                        f"直近:{', '.join(str(v) for v in suggest['recent'][:3])}"
                    )
                    default_amt = suggest["avg"]
                else:
                    default_amt = to_int(r.get("借方金額"))

                amt = st.number_input(
                    "金額",
                    min_value=0,
                    value=default_amt,
                    key=f"amt_{doc_id}_{r_idx}"
                )

                # =================================
                # 摘要
                # =================================
                memo = st.text_input(
                    "摘要",
                    value=r.get("摘要", ""),
                    key=f"m_{doc_id}_{r_idx}"
                )

                # =================================
                # 合計
                # =================================
                d_sum += amt
                c_sum += amt

                # =================================
                # 保存データ
                # =================================
                new_row = copy.deepcopy(r)

                new_row["伝票日付"] = process_date
                new_row["借方科目名"] = debit
                new_row["借方補助科目名"] = debit_sub
                new_row["貸方科目名"] = credit
                new_row["貸方補助科目名"] = credit_sub
                new_row["借方金額"] = str(amt)
                new_row["貸方金額"] = str(amt)
                new_row["摘要"] = memo

                edited_rows.append(new_row)

                st.divider()

            # =========================
            # チェック
            # =========================
            if d_sum != c_sum:
                st.error("❌ 貸借不一致")

            if process_date_obj > datetime.today().date():
                st.warning("⚠️ 未来日付")

            # =========================
            # 登録ボタン
            # =========================
            if st.button("登録", key=f"save_{doc_id}"):

                if d_sum != c_sum:
                    st.error("登録不可")
                else:
                    st.session_state.confirmed.append(
                        copy.deepcopy(edited_rows)
                    )
                    st.success("✔ 登録しました")

# =========================================
# 登録済（ここが本体）
# =========================================
if st.session_state.confirmed:

    st.divider()
    st.header("📦 登録済仕訳（編集可能）")

    for doc_idx, doc in enumerate(st.session_state.confirmed):

        with st.expander(f"伝票 {doc_idx+1}"):

            edited_doc = []

            for row_idx, r in enumerate(doc):

                st.markdown(f"### 行 {row_idx+1}")

                col1, col2 = st.columns(2)

                with col1:
                    debit = st.selectbox(
                        "借方",
                        account_master,
                        index=account_master.index(r["借方科目名"]) if r["借方科目名"] in account_master else 0,
                        key=f"conf_d_{doc_idx}_{row_idx}"
                    )

                with col2:
                    credit = st.selectbox(
                        "貸方",
                        account_master,
                        index=account_master.index(r["貸方科目名"]) if r["貸方科目名"] in account_master else 0,
                        key=f"conf_c_{doc_idx}_{row_idx}"
                    )

                # ===== 補助科目 =====
                col3, col4 = st.columns(2)

                with col3:
                    debit_sub = st.selectbox(
                        "借方補助",
                        [""] + sub_master,
                        index=([""] + sub_master).index(
                            r.get("借方補助科目名", "")
                        ) if r.get("借方補助科目名", "") in ([""] + sub_master) else 0,
                        key=f"conf_ds_{doc_idx}_{row_idx}"
                    )

                with col4:
                    credit_sub = st.selectbox(
                        "貸方補助",
                        [""] + sub_master,
                        index=([""] + sub_master).index(
                            r.get("貸方補助科目名", "")
                        ) if r.get("貸方補助科目名", "") in ([""] + sub_master) else 0,
                        key=f"conf_cs_{doc_idx}_{row_idx}"
                    )

                amt = st.number_input(
                    "金額",
                    value=to_int(r["借方金額"]),
                    key=f"conf_amt_{doc_idx}_{row_idx}"
                )

                memo = st.text_input(
                    "摘要",
                    value=r.get("摘要",""),
                    key=f"conf_m_{doc_idx}_{row_idx}"
                )

                new_row = copy.deepcopy(r)
                new_row["借方科目名"] = debit
                new_row["貸方科目名"] = credit
                new_row["借方補助科目名"] = debit_sub
                new_row["貸方補助科目名"] = credit_sub
                new_row["借方金額"] = str(amt)
                new_row["貸方金額"] = str(amt)
                new_row["摘要"] = memo

                edited_doc.append(new_row)

            colA, colB = st.columns(2)

            with colA:
                if st.button("更新保存", key=f"update_{doc_idx}"):
                    st.session_state.confirmed[doc_idx] = edited_doc
                    st.success("更新しました")
                    st.rerun()

            with colB:
                if st.button("削除", key=f"delete_{doc_idx}"):
                    st.session_state.confirmed.pop(doc_idx)
                    st.rerun()

    # =========================================
    # CSV
    # =========================================
    st.divider()
    st.header("📄 CSV")

    all_rows = []
    for doc in st.session_state.confirmed:
        all_rows.extend(doc)

    df = pd.DataFrame(all_rows).fillna("")

    st.dataframe(df)

    csv = df.to_csv(index=False).encode("cp932")

    st.download_button("CSVダウンロード", csv, "output.csv")
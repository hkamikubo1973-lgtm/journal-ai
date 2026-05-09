# =========================================
# app.py
# 仕訳検索システム Streamlit版
# Epson CSV対応強化版
# 完全修正版
# =========================================

import streamlit as st
import pandas as pd
import copy
from engine import get_amount_suggestions

from datetime import datetime

from engine import (
    load_data,
    search,
    get_department,
    to_int,
)

# =========================================
# 初期設定
# =========================================
st.set_page_config(
    page_title="仕訳検索システム",
    layout="wide"
)

st.title("📘 仕訳検索システム")

# =========================================
# データロード
# =========================================
@st.cache_data
def cached_load():
    return load_data()

records, name_to_code, freq = cached_load()

# =========================================
# マスタ
# =========================================
account_master = sorted(
    list(name_to_code.keys())
)

department_master = sorted(
    list(set(
        get_department(r)
        for rec in records
        for r in rec["rows"]
        if get_department(r)
    ))
)

# =========================================
# 補助科目マスタ
# =========================================
sub_master = sorted(
    list(set(

        r.get("借方補助科目名", "")
        for rec in records
        for r in rec["rows"]
        if r.get("借方補助科目名", "")

    ) | set(

        r.get("貸方補助科目名", "")
        for rec in records
        for r in rec["rows"]
        if r.get("貸方補助科目名", "")

    ))
)

# =========================================
# session_state
# =========================================
if "searched" not in st.session_state:
    st.session_state.searched = False

if "results" not in st.session_state:
    st.session_state.results = []

if "confirmed" not in st.session_state:
    st.session_state.confirmed = []

if "process_date" not in st.session_state:
    st.session_state.process_date = datetime.today()

if "template_selected" not in st.session_state:
    st.session_state.template_selected = None

# =========================================
# サイドバー（検索＋テンプレ）
# =========================================

# -------------------------------
# 検索
# -------------------------------
st.sidebar.header("🔍 検索")

dept = st.sidebar.selectbox(
    "部門",
    [""] + department_master,
    key="search_dept"
)

keyword = st.sidebar.text_input(
    "検索ワード",
    "",
    key="search_keyword"
)

amount_str = st.sidebar.text_input(
    "金額",
    "",
    key="search_amount"
)

amount = None

if amount_str.strip():
    try:
        amount = int(amount_str.replace(",", ""))
    except:
        st.sidebar.error("金額が不正です")


# -------------------------------
# テンプレ登録
# -------------------------------
st.sidebar.divider()
st.sidebar.header("📌 テンプレ登録")

with st.sidebar.expander("テンプレを追加"):

    t_name = st.text_input(
        "テンプレ名",
        key="tpl_name"
    )

    t_kw = st.text_input(
        "検索キーワード",
        key="tpl_keyword"
    )

    t_dept = st.selectbox(
        "部門",
        [""] + department_master,
        key="tpl_dept"
    )

    t_debit = st.selectbox(
        "借方科目",
        account_master,
        key="tpl_debit"
    )

    t_credit = st.selectbox(
        "貸方科目",
        account_master,
        key="tpl_credit"
    )

    t_amount = st.text_input(
        "金額（任意）",
        key="tpl_amount"
    )

    t_priority = st.number_input(
        "優先度（大きいほど上）",
        min_value=1,
        max_value=10,
        value=5,
        key="tpl_priority"
    )

    # -------------------------------
    # 保存処理
    # -------------------------------
    if st.button("テンプレ保存", key="tpl_save"):

        if not t_kw:
            st.warning("キーワードは必須です")
        else:
            import pandas as pd
            import os

            new_row = {
                "template_name": t_name,
                "keyword": t_kw,
                "dept": t_dept,
                "debit": t_debit,
                "credit": t_credit,
                "amount": t_amount,
                "priority": t_priority
            }

            file_path = "data/templates.csv"

            if os.path.exists(file_path):
                df = pd.read_csv(file_path)
                df = pd.concat([df, pd.DataFrame([new_row])])
            else:
                df = pd.DataFrame([new_row])

            df.to_csv(
                file_path,
                index=False,
                encoding="utf-8-sig"
            )

            st.success("テンプレ保存完了")
            st.rerun()

# =========================================
# 処理日
# =========================================
st.subheader("📅 登録処理日")

process_date_obj = st.date_input(

    "登録日",

    value=st.session_state.process_date,

    key="global_process_date"
)

st.session_state.process_date = process_date_obj

process_date = process_date_obj.strftime(
    "%Y%m%d"
)

st.caption(
    f"登録される伝票日付：{process_date}"
)

st.divider()

# =========================================
# 検索
# =========================================
if st.sidebar.button("検索"):

    st.session_state.searched = True

    st.session_state.results = search(
        records,
        keyword,
        dept,
        amount,
        freq
    )

# =========================================
# 初期画面
# =========================================
if not st.session_state.searched:

    st.info("左から検索してください")

# =========================================
# 検索結果
# =========================================
else:

    results = st.session_state.results

    if not results:

        st.warning("候補なし")

    else:

        st.success(
            f"{len(results)}件ヒット"
        )

        # =====================================
        # 候補ループ
        # =====================================
        for idx, (score, rec) in enumerate(results, 1):

            # ==============================
            # 🔵 通常仕訳（既存処理）
            # ==============================
            if isinstance(rec, dict) and "rows" in rec:

                rows = rec["rows"]

                doc_id = (
                    str(rows[0].get("伝票日付", ""))
                    + "_"
                    + str(rows[0].get("摘要", ""))
                    + "_"
                    + str(idx)
                )

                d_sum = sum(
                    to_int(r.get("借方金額"))
                    for r in rows
                )

                c_sum = sum(
                    to_int(r.get("貸方金額"))
                    for r in rows
                )

                title = (
                    f"{idx}. "
                    f"★スコア:{score} ｜ "
                    f"{rows[0].get('摘要','')}"
                )

                # =====================================
                # expander
                # =====================================
                with st.expander(title):

                    st.info(
                        f"元伝票日付："
                        f"{rows[0].get('伝票日付','')}"
                    )

                    col1, col2, col3 = st.columns(3)

                    with col1:
                        st.write(f"💴 借方合計：{d_sum:,}")

                    with col2:
                        st.write(f"💴 貸方合計：{c_sum:,}")

                    with col3:
                        if d_sum == c_sum:
                            st.success("貸借一致")
                        else:
                            st.error("貸借不一致")

                    st.divider()

                    # =================================
                    # 編集
                    # =================================
                    edited_rows = []

                    template = st.session_state.get("template_selected")

                    for r_idx, r in enumerate(rows):

                        st.markdown(f"### 行 {r_idx+1}")

                        c1, c2 = st.columns(2)

                        # ===== 借方 =====
                        with c1:
                            default_debit = r.get("借方科目名","")

                            if template and r_idx == 0:
                                default_debit = template.get("debit", default_debit)

                            debit = st.selectbox(
                                "借方科目",
                                account_master,
                                index=(
                                    account_master.index(default_debit)
                                    if default_debit in account_master
                                    else 0
                                ),
                                key=f"d_{doc_id}_{r_idx}"
                            )

                        # ===== 貸方 =====
                        with c2:
                            default_credit = r.get("貸方科目名","")

                            if template and r_idx == 0:
                                default_credit = template.get("credit", default_credit)

                            credit = st.selectbox(
                                "貸方科目",
                                account_master,
                                index=(
                                    account_master.index(default_credit)
                                    if default_credit in account_master
                                    else 0
                                ),
                                key=f"c_{doc_id}_{r_idx}"
                            )

                        # ===== サジェスト =====
                        suggest = get_amount_suggestions(
                            records,
                            debit,
                            credit
                        )

                        if suggest:
                            st.caption("📊 過去参考")

                            col_s1, col_s2 = st.columns(2)

                            with col_s1:
                                st.write(f"平均：{suggest['avg']:,}")

                            with col_s2:
                                st.write(
                                    "直近："
                                    + ", ".join(
                                        f"{v:,}" for v in suggest["recent"][:3]
                                    )
                                )

                        # ===== 金額 =====
                        amt = st.number_input(
                            "金額",
                            min_value=0,
                            value=to_int(r.get("借方金額")),
                            key=f"amt_{doc_id}_{r_idx}"
                        )

                        # ===== 摘要 =====
                        memo = st.text_input(
                            "摘要",
                            value=r.get("摘要",""),
                            key=f"m_{doc_id}_{r_idx}"
                        )

                        # ===== 保存用 =====
                        new_row = copy.deepcopy(r)

                        new_row["伝票日付"] = process_date
                        new_row["摘要"] = memo
                        new_row["借方科目名"] = debit
                        new_row["貸方科目名"] = credit
                        new_row["借方金額"] = str(amt)
                        new_row["貸方金額"] = str(amt)

                        edited_rows.append(new_row)

                        st.divider()

                    # 登録
                    if st.button(f"登録_{doc_id}"):

                        st.session_state.confirmed.append(
                           copy.deepcopy(edited_rows)
                        )

                        st.success("登録完了")

            # ==============================
            # 🔴 テンプレ
            # ==============================
            else:

                title = f"{idx}. ★テンプレ:{score}"

                with st.expander(title):

                    st.info(
                        f"テンプレ：{rec.get('template_name','')}"
                    )

                    st.write(
                        f"{rec.get('debit','')} / "
                        f"{rec.get('credit','')}"
                    )

                    if st.button(f"テンプレ使用_{idx}"):

                        st.session_state.template_selected = rec

                        st.success("テンプレをフォームに反映しました")

# =========================================
# 登録済一覧
# =========================================
if st.session_state.confirmed:

    st.divider()

    st.header("📦 登録済仕訳")

    delete_target = None

    for doc_idx, doc in enumerate(
        st.session_state.confirmed
    ):

        with st.expander(
            f"登録済伝票 {doc_idx+1}"
        ):

            edited_doc = []

            for row_idx, r in enumerate(doc):

                st.markdown(
                    f"### 行 {row_idx+1}"
                )

                st.write(
                    f"伝票日付："
                    f"{r.get('伝票日付','')}"
                )

                c1, c2 = st.columns(2)

                with c1:

                    debit = st.selectbox(

                        "借方科目",

                        account_master,

                        index=(
                            account_master.index(
                                r.get(
                                    "借方科目名",
                                    ""
                                )
                            )
                            if r.get(
                                "借方科目名",
                                ""
                            ) in account_master
                            else 0
                        ),

                        key=f"conf_d_{doc_idx}_{row_idx}"
                    )

                with c2:

                    credit = st.selectbox(

                        "貸方科目",

                        account_master,

                        index=(
                            account_master.index(
                                r.get(
                                    "貸方科目名",
                                    ""
                                )
                            )
                            if r.get(
                                "貸方科目名",
                                ""
                            ) in account_master
                            else 0
                        ),

                        key=f"conf_c_{doc_idx}_{row_idx}"
                    )

                c3, c4 = st.columns(2)

                with c3:

                    debit_sub = st.selectbox(

                        "借方補助",

                        [""] + sub_master,

                        index=(
                            ([""] + sub_master).index(
                                r.get(
                                    "借方補助科目名",
                                    ""
                                )
                            )
                            if r.get(
                                "借方補助科目名",
                                ""
                            ) in ([""] + sub_master)
                            else 0
                        ),

                        key=f"conf_ds_{doc_idx}_{row_idx}"
                    )

                with c4:

                    credit_sub = st.selectbox(

                        "貸方補助",

                        [""] + sub_master,

                        index=(
                            ([""] + sub_master).index(
                                r.get(
                                    "貸方補助科目名",
                                    ""
                                )
                            )
                            if r.get(
                                "貸方補助科目名",
                                ""
                            ) in ([""] + sub_master)
                            else 0
                        ),

                        key=f"conf_cs_{doc_idx}_{row_idx}"
                    )

                # =================================
                # 金額サジェスト（登録済用）
                # =================================
                suggest = get_amount_suggestions(
                    records,
                    debit,
                    credit
                )

                if suggest:

                    st.caption("📊 過去参考")

                    col_s1, col_s2 = st.columns(2)

                    with col_s1:
                        st.write(f"平均：{suggest['avg']:,}")

                    with col_s2:
                        st.write(
                            "直近："
                            + ", ".join(
                                f"{v:,}" for v in suggest["recent"][:3]
                            )
                        )

                amt = st.number_input(

                    "金額",

                    min_value=0,

                    value=to_int(
                        r.get("借方金額")
                    ),

                    key=f"conf_amt_{doc_idx}_{row_idx}"
                )

                memo = st.text_input(

                    "摘要",

                    value=r.get(
                        "摘要",
                        ""
                    ),

                    key=f"conf_m_{doc_idx}_{row_idx}"
                )

                # -----------------------------
                # 更新データ
                # -----------------------------
                new_row = copy.deepcopy(r)

                new_row["摘要"] = memo

                new_row["借方科目名"] = debit
                new_row["借方補助科目名"] = debit_sub

                new_row["貸方科目名"] = credit
                new_row["貸方補助科目名"] = credit_sub

                new_row["借方金額"] = str(amt)
                new_row["貸方金額"] = str(amt)

                edited_doc.append(new_row)

                st.divider()

            col1, col2 = st.columns(2)

            with col1:

                if st.button(

                    "更新保存",

                    key=f"update_{doc_idx}"
                ):

                    st.session_state.confirmed[
                        doc_idx
                    ] = copy.deepcopy(
                        edited_doc
                    )

                    st.success(
                        "更新しました"
                    )

                    st.rerun()

            with col2:

                if st.button(

                    "削除",

                    key=f"delete_{doc_idx}"
                ):

                    delete_target = doc_idx

    # =====================================
    # 削除実行
    # =====================================
    if delete_target is not None:

        st.session_state.confirmed.pop(
            delete_target
        )

        st.success("削除しました")

        st.rerun()

    # =====================================
    # CSV出力
    # =====================================
    st.divider()

    st.header("📄 CSV出力")

    all_rows = []

    for doc in st.session_state.confirmed:

        for r in doc:

            all_rows.append(r)

    # =====================================
    # DataFrame化
    # =====================================
    out_df = pd.DataFrame(all_rows)

    # =====================================
    # NaN除去
    # =====================================
    out_df = out_df.fillna("")

    # =====================================
    # 表示
    # =====================================
    st.dataframe(

        out_df,

        use_container_width=True,

        hide_index=True
    )

    # =====================================
    # CSV生成
    # =====================================
    csv_data = out_df.to_csv(
        index=False
    ).encode("cp932")

    # =====================================
    # ダウンロード
    # =====================================
    st.download_button(

        label="CSVダウンロード",

        data=csv_data,

        file_name="output.csv",

        mime="text/csv"
    )

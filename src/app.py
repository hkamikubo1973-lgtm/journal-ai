# =========================================
# app.py
# 仕訳検索システム Streamlit版
# Epson CSV対応強化版
# 完全修正版
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

# =========================================
# サイドバー
# =========================================
st.sidebar.header("🔍 検索")

dept = st.sidebar.selectbox(
    "部門",
    [""] + department_master
)

keyword = st.sidebar.text_input(
    "検索ワード",
    ""
)

amount_str = st.sidebar.text_input(
    "金額",
    ""
)

amount = None

if amount_str.strip():

    try:

        amount = int(
            amount_str.replace(",", "")
        )

    except:

        st.sidebar.error("金額が不正です")

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

                # =================================
                # 元伝票情報
                # =================================
                st.info(
                    f"元伝票日付："
                    f"{rows[0].get('伝票日付','')}"
                )

                col1, col2, col3 = st.columns(3)

                with col1:

                    st.write(
                        f"💴 借方合計："
                        f"{d_sum:,}"
                    )

                with col2:

                    st.write(
                        f"💴 貸方合計："
                        f"{c_sum:,}"
                    )

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

                for r_idx, r in enumerate(rows):

                    st.markdown(
                        f"### 行 {r_idx+1}"
                    )

                    # -----------------------------
                    # 科目
                    # -----------------------------
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

                            key=f"d_{doc_id}_{r_idx}"
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

                            key=f"c_{doc_id}_{r_idx}"
                        )

                    # -----------------------------
                    # 補助科目
                    # -----------------------------
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

                            key=f"ds_{doc_id}_{r_idx}"
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

                            key=f"cs_{doc_id}_{r_idx}"
                        )

                    # -----------------------------
                    # 金額
                    # -----------------------------
                    amt = st.number_input(

                        "金額",

                        min_value=0,

                        value=to_int(
                            r.get("借方金額")
                        ),

                        step=1,

                        key=f"amt_{doc_id}_{r_idx}"
                    )

                    st.caption(
                        f"貸方金額：{amt:,}"
                    )

                    # -----------------------------
                    # 摘要
                    # -----------------------------
                    memo = st.text_input(

                        "摘要",

                        value=r.get(
                            "摘要",
                            ""
                        ),

                        key=f"m_{doc_id}_{r_idx}"
                    )

                    # -----------------------------
                    # 編集データ
                    # 元データ保持型
                    # -----------------------------
                    new_row = copy.deepcopy(r)

                    new_row["伝票日付"] = process_date

                    new_row["摘要"] = memo

                    new_row["借方科目名"] = debit
                    new_row["借方補助科目名"] = debit_sub

                    new_row["貸方科目名"] = credit
                    new_row["貸方補助科目名"] = credit_sub

                    new_row["借方金額"] = str(amt)
                    new_row["貸方金額"] = str(amt)

                    edited_rows.append(new_row)

                    st.divider()

                # =================================
                # 編集確認
                # =================================
                if st.button(

                    "編集内容確認",

                    key=f"check_{doc_id}"
                ):

                    check_df = pd.DataFrame(
                        edited_rows
                    )

                    st.dataframe(

                        check_df,

                        use_container_width=True,

                        hide_index=True
                    )

                # =================================
                # 登録
                # =================================
                if st.button(

                    "登録",

                    key=f"save_{doc_id}"
                ):

                    st.session_state.confirmed.append(
                        copy.deepcopy(
                            edited_rows
                        )
                    )

                    st.success("登録完了")

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

# =========================================
# 仕訳検索システム（完全版・安全版）
# 削除禁止版
# ・登録済編集維持
# ・CSV上追加
# ・エプソン45列対応
# ・AO～AS自動付与
# =========================================

import streamlit as st
import pandas as pd
import copy
import platform
import getpass

from datetime import datetime

from engine import (
    load_data,
    search,
    get_department,
    to_int,
    get_amount_suggestions,
    update_search_csv
)

# =========================================
# エプソンCSV列
# =========================================
EPSON_COLUMNS = [
    "月種別",
    "種類",
    "形式",
    "作成方法",
    "付箋",
    "伝票日付",
    "伝票番号",
    "伝票摘要",
    "枝番",
    "借方部門",
    "借方部門名",
    "借方科目",
    "借方科目名",
    "借方補助",
    "借方補助科目名",
    "借方金額",
    "借方消費税コード",
    "借方消費税業種",
    "借方消費税税率",
    "借方資金区分",
    "借方任意項目１",
    "借方任意項目２",
    "借方インボイス情報",
    "貸方部門",
    "貸方部門名",
    "貸方科目",
    "貸方科目名",
    "貸方補助",
    "貸方補助科目名",
    "貸方金額",
    "貸方消費税コード",
    "貸方消費税業種",
    "貸方消費税税率",
    "貸方資金区分",
    "貸方任意項目１",
    "貸方任意項目２",
    "貸方インボイス情報",
    "摘要",
    "期日",
    "証番号",
    "入力マシン",
    "入力ユーザ",
    "入力アプリ",
    "入力会社",
    "入力日付",
]

# =========================================
# 伝票合計
# =========================================
def get_voucher_total(rows):

    total = 0

    for r in rows:

        try:
            total += int(
                str(r.get("借方金額", 0))
                .replace(",", "")
            )
        except:
            pass

    return total

# =========================================
# 伝票分割
# =========================================
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

    # 1対多
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

    # 多対1
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

    return rows

# =========================================
# エプソンCSV変換
# =========================================
def build_epson_rows(rows, company_name):

    result = []

    machine_name = platform.node()
    user_name = getpass.getuser()

    app_name = "仕訳検索システム"

    input_date = datetime.now().strftime("%Y%m%d")

    for r in rows:

        row = {}

        for c in EPSON_COLUMNS:
            row[c] = ""

        # =====================================
        # 基本
        # =====================================
        row["伝票日付"] = r.get("伝票日付", "")
        row["摘要"] = r.get("摘要", "")
        row["伝票摘要"] = r.get("摘要", "")

        # =====================================
        # 借方
        # =====================================
        row["借方部門"] = r.get("借方部門", "")
        row["借方部門名"] = r.get("借方部門名", "")

        row["借方科目"] = r.get("借方科目", "")
        row["借方科目名"] = r.get("借方科目名", "")

        row["借方補助"] = r.get("借方補助", "")
        row["借方補助科目名"] = r.get("借方補助科目名", "")

        row["借方金額"] = r.get("借方金額", "")

        row["借方消費税コード"] = r.get(
            "借方消費税コード",
            ""
        )

        row["借方消費税業種"] = r.get(
            "借方消費税業種",
            ""
        )

        row["借方消費税税率"] = r.get(
            "借方消費税税率",
            ""
        )

        # =====================================
        # 貸方
        # =====================================
        row["貸方部門"] = r.get("貸方部門", "")
        row["貸方部門名"] = r.get("貸方部門名", "")

        row["貸方科目"] = r.get("貸方科目", "")
        row["貸方科目名"] = r.get("貸方科目名", "")

        row["貸方補助"] = r.get("貸方補助", "")
        row["貸方補助科目名"] = r.get("貸方補助科目名", "")

        row["貸方金額"] = r.get("貸方金額", "")

        row["貸方消費税コード"] = r.get(
            "貸方消費税コード",
            ""
        )

        row["貸方消費税業種"] = r.get(
            "貸方消費税業種",
            ""
        )

        row["貸方消費税税率"] = r.get(
            "貸方消費税税率",
            ""
        )

        # =====================================
        # AO～AS
        # =====================================
        row["入力マシン"] = machine_name
        row["入力ユーザ"] = user_name
        row["入力アプリ"] = app_name
        row["入力会社"] = company_name
        row["入力日付"] = input_date

        result.append(row)

    return result

# =========================================
# 初期設定
# =========================================
st.set_page_config(
    page_title="仕訳検索",
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

if "company_name" not in st.session_state:
    st.session_state.company_name = ""


# =========================================
# サイドバー
# =========================================

# -----------------------------------------
# システム設定
# -----------------------------------------
st.sidebar.header("🏢 システム設定")

company_name = st.sidebar.text_input(
    "入力会社",
    value=st.session_state.company_name
)

st.session_state.company_name = company_name

st.sidebar.divider()


st.sidebar.header("🔍 検索")

dept = st.sidebar.selectbox(
    "部門（任意）",
    [""] + department_master
)

keyword = st.sidebar.text_input("キーワード")

amount_str = st.sidebar.text_input("金額")

amount = None

if amount_str:

    try:
        amount = int(
            amount_str.replace(",", "")
        )

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

process_date_obj = st.date_input(
    "日付",
    datetime.today()
)

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

    for idx, (score, rec, score_detail) in enumerate(results, 1):

        if not isinstance(rec, dict):
            continue

        if "rows" not in rec:
            continue

        rows = split_journal(rec["rows"])

        doc_id = f"{idx}_{id(rec)}"

        summary = (
            f"{idx}. ★{score} "
            f"{rows[0].get('摘要','')}"
            f"　{len(rows)}行"
            f"　¥{get_voucher_total(rows):,}"
        )

        with st.expander(summary):

            with st.expander("検索理由"):
                for d in score_detail:
                    st.write("・", d)

            st.divider()

            edited_rows = []

            d_sum = 0
            c_sum = 0

            for r_idx, r in enumerate(rows):

                st.markdown(f"### 行 {r_idx+1}")

                col1, col2 = st.columns(2)

                # =====================================
                # 借方 / 貸方
                # =====================================
                with col1:

                    default_debit = r.get(
                        "借方科目名",
                        ""
                    )

                    debit = st.selectbox(
                        "借方",
                        account_master,
                        index=(
                            account_master.index(default_debit)
                            if default_debit in account_master
                            else 0
                        ),
                        key=f"d_{doc_id}_{r_idx}"
                    )

                with col2:

                    default_credit = r.get(
                        "貸方科目名",
                        ""
                    )

                    credit = st.selectbox(
                        "貸方",
                        account_master,
                        index=(
                            account_master.index(default_credit)
                            if default_credit in account_master
                            else 0
                        ),
                        key=f"c_{doc_id}_{r_idx}"
                    )

                # =====================================
                # 補助
                # =====================================
                col3, col4 = st.columns(2)

                with col3:

                    default_ds = r.get(
                        "借方補助科目名",
                        ""
                    )

                    debit_sub = st.selectbox(
                        "借方補助",
                        [""] + sub_master,
                        index=(
                            ([""] + sub_master).index(default_ds)
                            if default_ds in ([""] + sub_master)
                            else 0
                        ),
                        key=f"ds_{doc_id}_{r_idx}"
                    )

                with col4:

                    default_cs = r.get(
                        "貸方補助科目名",
                        ""
                    )

                    credit_sub = st.selectbox(
                        "貸方補助",
                        [""] + sub_master,
                        index=(
                            ([""] + sub_master).index(default_cs)
                            if default_cs in ([""] + sub_master)
                            else 0
                        ),
                        key=f"cs_{doc_id}_{r_idx}"
                    )

                # =====================================
                # 金額
                # =====================================
                suggest = get_amount_suggestions(
                    records,
                    debit,
                    credit
                )

                if len(rows) == 1 and suggest:

                    default_amt = suggest["avg"]

                    st.caption(
                        f"平均:{suggest['avg']:,}"
                    )

                else:

                    default_amt = to_int(
                        r.get("借方金額")
                    )

                amt = st.number_input(
                    "金額",
                    min_value=0,
                    value=default_amt,
                    key=f"amt_{doc_id}_{r_idx}"
                )

                # =====================================
                # 摘要
                # =====================================
                memo = st.text_input(
                    "摘要",
                    value=r.get("摘要", ""),
                    key=f"m_{doc_id}_{r_idx}"
                )

                d_sum += amt
                c_sum += amt

                new_row = copy.deepcopy(r)

                new_row["伝票日付"] = process_date

                new_row["借方科目名"] = debit
                new_row["貸方科目名"] = credit

                new_row["借方補助科目名"] = debit_sub
                new_row["貸方補助科目名"] = credit_sub

                new_row["借方金額"] = str(amt)
                new_row["貸方金額"] = str(amt)

                new_row["摘要"] = memo

                edited_rows.append(new_row)

                st.divider()

            # =====================================
            # 貸借確認
            # =====================================
            if d_sum != c_sum:
                st.error("❌ 貸借不一致")

            # =====================================
            # 未来日付
            # =====================================
            if process_date_obj > datetime.today().date():
                st.warning("⚠️ 未来日付")

            # =====================================
            # 登録
            # =====================================
            if st.button(
                "登録",
                key=f"save_{doc_id}"
            ):

                if d_sum != c_sum:

                    st.error("登録不可")

                else:

                    st.session_state.confirmed.append(
                        copy.deepcopy(edited_rows)
                    )

                    st.success("✔ 登録しました")

# =========================================
# 登録済
# =========================================
if st.session_state.confirmed:

    st.divider()

    st.header("📦 登録済仕訳（編集可能）")

    for doc_idx, doc in enumerate(
        st.session_state.confirmed
    ):

        with st.expander(f"伝票 {doc_idx+1}"):

            edited_doc = []

            for row_idx, r in enumerate(doc):

                st.markdown(f"### 行 {row_idx+1}")

                col1, col2 = st.columns(2)

                with col1:

                    debit = st.selectbox(
                        "借方",
                        account_master,
                        index=(
                            account_master.index(
                                r["借方科目名"]
                            )
                            if r["借方科目名"] in account_master
                            else 0
                        ),
                        key=f"conf_d_{doc_idx}_{row_idx}"
                    )

                with col2:

                    credit = st.selectbox(
                        "貸方",
                        account_master,
                        index=(
                            account_master.index(
                                r["貸方科目名"]
                            )
                            if r["貸方科目名"] in account_master
                            else 0
                        ),
                        key=f"conf_c_{doc_idx}_{row_idx}"
                    )

                col3, col4 = st.columns(2)

                with col3:

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

                with col4:

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
                    value=to_int(
                        r["借方金額"]
                    ),
                    key=f"conf_amt_{doc_idx}_{row_idx}"
                )

                memo = st.text_input(
                    "摘要",
                    value=r.get("摘要", ""),
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

            # =====================================
            # 更新保存
            # =====================================
            with colA:

                if st.button(
                    "更新保存",
                    key=f"update_{doc_idx}"
                ):

                    st.session_state.confirmed[
                        doc_idx
                    ] = edited_doc

                    st.success("更新しました")

                    st.rerun()

            # =====================================
            # 削除
            # =====================================
            with colB:

                if st.button(
                    "削除",
                    key=f"delete_{doc_idx}"
                ):

                    st.session_state.confirmed.pop(
                        doc_idx
                    )

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

    # =====================================
    # DB保存
    # =====================================
    if st.button("💾 検索DBへ保存"):

        update_search_csv(
            st.session_state.confirmed
        )

        st.success(
            "transactions.csv 更新完了"
        )

        st.cache_data.clear()

    # =====================================
    # 内部CSV
    # =====================================
    csv = df.to_csv(
        index=False
    ).encode("cp932")

    st.download_button(
        "内部CSVダウンロード",
        csv,
        "journal_output.csv"
    )

    # =====================================
    # エプソンCSV
    # =====================================
    epson_rows = build_epson_rows(
        all_rows,
        st.session_state.company_name
    )

    epson_df = pd.DataFrame(
        epson_rows,
        columns=EPSON_COLUMNS
    ).fillna("")

    epson_csv = epson_df.to_csv(
        index=False
    ).encode("cp932")

    st.download_button(
        "エプソンCSVダウンロード",
        epson_csv,
        "epson_output.csv"
    )
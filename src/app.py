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
import csv

from ocr_gateway import DummyOcrGateway
from receivable_engine import (
    apply_receivable_candidates,
    is_receivable_journal_registered,
    load_receivable_history,
    load_receivables,
    mark_receivable_journal_registered,
)

def load_account_master():

    result = {}

    with open(
        "data/account_master.csv",
        encoding="utf-8-sig"
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            result[
                row["name"]
            ] = row["code"]

    return result


ACCOUNT_MASTER = load_account_master()

def load_sub_master():

    result = {}

    with open(
        "data/sub_master.csv",
        encoding="utf-8-sig"
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            result[
                row["name"]
            ] = row["code"]

    return result

SUB_MASTER = load_sub_master()

def load_department_master():

    result = {}

    with open(
        "data/department_master.csv",
        encoding="utf-8-sig"
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            result[
                row["name"]
            ] = row["code"]

    return result


DEPARTMENT_MASTER = load_department_master()

from datetime import datetime

from engine import (
    load_data,
    search,
    get_department,
    to_int,
    get_amount_suggestions,
    update_search_csv
)

from columns import (
    EPSON_COLUMNS,
    SEARCH_COLUMNS,
    EDIT_COLUMNS,
    DISPLAY_COLUMNS,
)

from columns import (
    COL_DATE,
    COL_DEBIT,
    COL_CREDIT,
    COL_DEBIT_SUB,
    COL_CREDIT_SUB,
    COL_DEBIT_AMOUNT,
    COL_CREDIT_AMOUNT,
    COL_SUMMARY,
)


# =========================================
# 科目名変換
# =========================================
def get_account_name(code):

    code = str(code)

    return ACCOUNT_MASTER.get(
        code,
        code
    )

# =========================================
# 伝票合計
# =========================================
def get_voucher_total(rows):

    total = 0

    for r in rows:

        try:
            total += int(
                str(r.get(COL_DEBIT_AMOUNT, 0))
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

        d_amt = to_int(r.get(COL_DEBIT_AMOUNT))
        c_amt = to_int(r.get(COL_CREDIT_AMOUNT))

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

            new["貸方科目名"] = c_row.get(COL_CREDIT, "")
            new["貸方補助科目名"] = c_row.get(COL_CREDIT_SUB, "")
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

            new["貸方科目名"] = c_row.get(COL_CREDIT, "")
            new["貸方補助科目名"] = c_row.get(COL_CREDIT_SUB, "")
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
        row["伝票日付"] = r.get(COL_DATE, "")
        row["摘要"] = r.get(COL_SUMMARY, "")
        row["伝票摘要"] = r.get(COL_SUMMARY, "")

        # =====================================
        # 借方
        # =====================================
        row["借方部門"] = r.get("借方部門", "")
        row["借方部門名"] = r.get("借方部門名", "")

        row["借方科目"] = r.get("借方科目", "")
        row["借方科目名"] = r.get(COL_DEBIT, "")

        row["借方補助"] = r.get("借方補助", "")
        row["借方補助科目名"] = r.get(COL_DEBIT_SUB, "")

        row["借方金額"] = r.get(COL_DEBIT_AMOUNT, "")

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
        row["貸方科目名"] = r.get(COL_CREDIT, "")

        row["貸方補助"] = r.get("貸方補助", "")
        row["貸方補助科目名"] = r.get(COL_CREDIT_SUB, "")

        row["貸方金額"] = r.get(COL_CREDIT_AMOUNT, "")

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

# =========================================
# データロード
# =========================================
@st.cache_data
def cached_load():
    return load_data()

records, name_to_code, freq = cached_load()

account_master = sorted(
    ACCOUNT_MASTER.keys()
)

department_master = sorted(
    DEPARTMENT_MASTER.keys()
)

sub_master = sorted(
    SUB_MASTER.keys()
)

# =========================================
# 科目マスター生成
# =========================================
def generate_account_master(records):

    rows = []

    seen = set()

    for rec in records:

        for r in rec["rows"]:

            debit_code = str(
                r.get("借方科目", "")
            ).strip()

            debit_name = str(
                r.get(COL_DEBIT, "")
            ).strip()

            if debit_code and debit_name:

                key = (
                    debit_code,
                    debit_name
                )

                if key not in seen:

                    seen.add(key)

                    rows.append({
                        "code": debit_code,
                        "name": debit_name
                    })

            credit_code = str(
                r.get("貸方科目", "")
            ).strip()

            credit_name = str(
                r.get(COL_CREDIT, "")
            ).strip()

            if credit_code and credit_name:

                key = (
                    credit_code,
                    credit_name
                )

                if key not in seen:

                    seen.add(key)

                    rows.append({
                        "code": credit_code,
                        "name": credit_name
                    })

    rows = sorted(
        rows,
        key=lambda x: x["code"]
    )

    pd.DataFrame(rows).to_csv(
        "data/account_master.csv",
        index=False,
        encoding="utf-8-sig"
    )

    return len(rows)

# =========================================
# 部門マスター生成
# =========================================
def generate_department_master(records):

    rows = []
    seen = set()

    for rec in records:

        for r in rec["rows"]:

            targets = [

                (
                    str(r.get("借方部門", "")).strip(),
                    str(r.get("借方部門名", "")).strip()
                ),

                (
                    str(r.get("貸方部門", "")).strip(),
                    str(r.get("貸方部門名", "")).strip()
                )

            ]

            for code, name in targets:

                if code and name:

                    key = (code, name)

                    if key not in seen:

                        seen.add(key)

                        rows.append({
                            "code": code,
                            "name": name
                        })


    pd.DataFrame(rows).to_csv(
        "data/department_master.csv",
        index=False,
        encoding="utf-8-sig"
    )

    return len(rows)

# =========================================
# 補助マスター生成
# =========================================
def generate_sub_master(records):

    rows = []
    seen = set()

    for rec in records:

        for r in rec["rows"]:

            targets = [

                (
                    str(r.get("借方補助", "")).strip(),
                    str(r.get("借方補助科目名", "")).strip()
                ),

                (
                    str(r.get("貸方補助", "")).strip(),
                    str(r.get("貸方補助科目名", "")).strip()
                )

            ]

            for code, name in targets:

                if code and name:

                    key = (code, name)

                    if key not in seen:

                        seen.add(key)

                        rows.append({
                            "code": code,
                            "name": name
                        })

    pd.DataFrame(rows).to_csv(
        "data/sub_master.csv",
        index=False,
        encoding="utf-8-sig"
    )

    return len(rows)

mode = st.sidebar.radio(
    "モード",
    [
        "通常仕訳",
        "未収消込"
    ]
)

if mode == "通常仕訳":

    st.title("📘 仕訳検索システム")

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
    
    # -----------------------------------------
    # 検索
    # -----------------------------------------
    st.sidebar.header("🔍 検索")
    
    dept = st.sidebar.selectbox(
        "部門",
        [""] + department_master
    )
    
    if "keyword_input" not in st.session_state:
        st.session_state["keyword_input"] = ""
    
    if "ocr_search_text_pending" in st.session_state:
        st.session_state["keyword_input"] = (
            st.session_state.pop("ocr_search_text_pending")
        )
    
    keyword = st.sidebar.text_input(
        "キーワード",
        key="keyword_input"
    )
    
    amount_str = st.sidebar.text_input("金額")
    
    amount = None
    
    if amount_str:
    
        try:
            amount = int(
                amount_str.replace(",", "")
            )
    
        except:
            st.sidebar.error("金額エラー")
    
    search_clicked = st.sidebar.button("検索")
    
    if st.sidebar.button(
        "科目マスター生成"
    ):
        count = generate_account_master(
            records
        )
    
        ACCOUNT_MASTER.clear()
    
        ACCOUNT_MASTER.update(
            load_account_master()
        )
    
        st.toast(
            f"科目マスター {count}件生成しました"
        )
    
    
    if st.sidebar.button(
        "部門マスター生成"
    ):
        
        count = generate_department_master(
            records
        )
    
        DEPARTMENT_MASTER.clear()
    
        DEPARTMENT_MASTER.update(
            load_department_master()
        )
    
        st.toast(
            f"部門マスター {count}件生成しました"
        )
    
    
    if st.sidebar.button(
        "補助マスター生成"
    ):
    
        count = generate_sub_master(
            records
        )
    
        SUB_MASTER.clear()
    
        SUB_MASTER.update(
            load_sub_master()
        )
    
        st.toast(
            f"補助マスター {count}件生成しました"
        )
    
    # =========================================
    # 検索実行
    # =========================================
    if search_clicked:
    
        st.session_state.results = search(
            records,
            st.session_state["keyword_input"],
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
    # OCR読込
    # =========================================
    st.subheader("📄 OCR読込")
    
    uploaded_file = st.file_uploader(
        "画像 / PDF アップロード",
        type=["jpg", "jpeg", "png", "pdf"]
    )
    
    current_file = None
    
    if uploaded_file:
        current_file = uploaded_file.name
    
    if "last_uploaded_file" not in st.session_state:
        st.session_state["last_uploaded_file"] = ""
    
    if "ocr_result" not in st.session_state:
        st.session_state["ocr_result"] = None
    
    if current_file != st.session_state["last_uploaded_file"]:
    
        st.session_state["ocr_done"] = False
        st.session_state["ocr_result"] = None
        st.session_state["last_uploaded_file"] = current_file
    
    # =========================================
    # OCR実行
    # =========================================
    if uploaded_file and not st.session_state.get("ocr_done", False):
    
        st.success(
            f"アップロード: {uploaded_file.name}"
        )
    
        st.info("OCR解析中...")
    
        gateway = DummyOcrGateway()
    
        st.session_state["ocr_result"] = gateway.analyze(
            content=uploaded_file.getvalue(),
            filename=uploaded_file.name,
            mime_type=uploaded_file.type or "",
        )
    
        # OCR済み
        st.session_state["ocr_done"] = True
    
        st.success("OCR解析完了")
    
    ocr_result = st.session_state["ocr_result"]
    
    if ocr_result is not None:
    
        st.write(
            "検索文字列:",
            ocr_result.search_text
        )
    
        st.write(
            "金額:",
            f"¥{ocr_result.amount:,}"
            if ocr_result.amount is not None
            else ""
        )
    
        st.write(
            "摘要:",
            ocr_result.memo
        )
    
        st.write(
            "OCR全文:",
            ocr_result.raw_text
        )
    
        st.write(
            "信頼度:",
            ocr_result.confidence
        )
    
        if st.button(
            "検索へ反映",
            disabled=not bool(ocr_result.search_text.strip()),
        ):
            st.session_state["ocr_search_text_pending"] = (
                ocr_result.search_text
            )
            st.rerun()
    
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
    
                    full_match_count = len([
                        d for d in score_detail
                        if "完全一致" in d
                    ])
    
                    partial_match_count = len([
                        d for d in score_detail
                        if "部分一致" in d
                    ])
    
                    if full_match_count:
                        st.write(
                            f"✅ 完全一致 {full_match_count}件"
                        )
    
                    if partial_match_count:
                        st.write(
                            f"✅ 部分一致 {partial_match_count}件"
                        )
    
                    if any(
                        "部門一致" in d
                        for d in score_detail
                    ):
                        st.write("✅ 部門一致")
    
                    if any(
                        "複数キーワード一致" in d
                        for d in score_detail
                    ):
                        st.write("✅ 複数キーワード一致")
    
                st.divider()
    
                edited_rows = []
    
                d_sum = 0
                c_sum = 0
    
                for r_idx, r in enumerate(rows):
    
                    debit_account = get_account_name(
                        r.get("借方科目", "")
                    )
    
                    credit_account = get_account_name(
                        r.get("貸方科目", "")
                    )
                    amount_value = to_int(
                        r.get(COL_DEBIT_AMOUNT, 0)
                    )
    
                    row_summary = (
                        f"{r_idx+1}行目 "
                        f"🔵[借] {debit_account} "
                        f"/ "
                        f"🔴[貸] {credit_account} "
                        f"/ ¥{amount_value:,}"
                    )
    
                    with st.expander(
                        row_summary,
                        expanded=False
                    ):
    
                        col1, col2 = st.columns(2)
    
                        # =====================================
                        # 借方 / 貸方
                        # =====================================
                        with col1:
    
                            default_debit = r.get(
                                COL_DEBIT,
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
                                COL_CREDIT,
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
                            COL_DEBIT_SUB,
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
                            COL_CREDIT_SUB,
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
                            r.get(COL_DEBIT_AMOUNT)
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
                        value=r.get(COL_SUMMARY, ""),
                        key=f"m_{doc_id}_{r_idx}"
                    )
    
                    d_sum += amt
                    c_sum += amt
    
                    new_row = copy.deepcopy(r)
    
                    new_row[COL_DATE] = process_date
    
                    new_row[COL_DEBIT] = debit
                    new_row[COL_CREDIT] = credit
    
                    new_row[COL_DEBIT_SUB] = debit_sub
                    new_row[COL_CREDIT_SUB] = credit_sub
    
                    new_row[COL_DEBIT_AMOUNT] = str(amt)
                    new_row[COL_CREDIT_AMOUNT] = str(amt)
    
                    new_row[COL_SUMMARY] = memo
    
                    edited_rows.append(new_row)
    
                    if d_sum != c_sum:
                        st.error(
                            f"借貸不一致: 借方¥{d_sum:,} / 貸方¥{c_sum:,}"
                        )
                    else:
                        st.success("借貸一致")
    
                st.divider()
                
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
                                    r[COL_DEBIT]
                                )
                                if r[COL_DEBIT] in account_master
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
                                    r[COL_CREDIT]
                                )
                                if r[COL_CREDIT] in account_master
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
                                        COL_DEBIT_SUB,
                                        ""
                                    )
                                )
                                if r.get(
                                    COL_DEBIT_SUB,
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
                                        COL_CREDIT_SUB,
                                        ""
                                    )
                                )
                                if r.get(
                                    COL_CREDIT_SUB,
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
                        value=r.get(COL_SUMMARY, ""),
                        key=f"conf_m_{doc_idx}_{row_idx}"
                    )
    
                    new_row = copy.deepcopy(r)
    
                    new_row[COL_DEBIT] = debit
                    new_row[COL_CREDIT] = credit
    
                    new_row[COL_DEBIT_SUB] = debit_sub
                    new_row[COL_CREDIT_SUB] = credit_sub
    
                    new_row[COL_DEBIT_AMOUNT] = str(amt)
                    new_row[COL_CREDIT_AMOUNT] = str(amt)
    
                    new_row[COL_SUMMARY] = memo
    
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
            "確認用CSVダウンロード",
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
    

elif mode == "未収消込":

    st.header("未収消込")

    if "receivable_success" in st.session_state:
        st.success(
            st.session_state.pop("receivable_success")
        )

    receivables_df = load_receivables()

    if receivables_df.empty:

        st.info("未収データがありません")

    else:

        receivables_df = receivables_df.copy()

        receivables_df["残高"] = pd.to_numeric(
            receivables_df["残高"].str.replace(",", ""),
            errors="coerce"
        ).fillna(0).astype(int)

        receivables_df = receivables_df[
            (receivables_df["残高"] > 0)
            |
            (receivables_df["ステータス"] != "完了")
        ].copy()

        if receivables_df.empty:

            st.info("未収データがありません")

        else:

            balance_df = (
                receivables_df
                .groupby(
                    "得意先名",
                    as_index=False
                )
                .agg(
                    残高=("残高", "sum"),
                    件数=("残高", "size")
                )
                .rename(
                    columns={"得意先名": "取引先"}
                )
                .sort_values(
                    "残高",
                    ascending=False
                )
            )

            st.dataframe(
                balance_df[["取引先", "残高", "件数"]],
                use_container_width=True
            )

            for customer_idx, (_, customer) in enumerate(
                balance_df.iterrows()
            ):

                customer_name = customer["取引先"]

                with st.expander(
                    f"{customer_name}の明細"
                ):

                    detail_df = receivables_df[
                        receivables_df["得意先名"] == customer_name
                    ].copy()

                    if "請求日" not in detail_df.columns:
                        detail_df["請求日"] = ""

                    detail_df["請求金額"] = pd.to_numeric(
                        detail_df["請求金額"].str.replace(",", ""),
                        errors="coerce"
                    ).fillna(0).astype(int)

                    detail_df["入金済額"] = (
                        detail_df["請求金額"]
                        - detail_df["残高"]
                    )

                    detail_df = detail_df[
                        [
                            "コード",
                            "請求日",
                            "請求金額",
                            "入金済額",
                            "残高",
                            "ステータス",
                            "未収科目",
                            "未収補助",
                            "部門"
                        ]
                    ]

                    st.dataframe(
                        detail_df,
                        use_container_width=True
                    )

                    payment_date = st.date_input(
                        "入金日",
                        key=f"payment_date_{customer_idx}"
                    )

                    payment_amount = st.number_input(
                        "入金額",
                        min_value=0,
                        step=1,
                        key=f"payment_amount_{customer_idx}"
                    )

                    if st.button(
                        "入金候補表示",
                        key=f"payment_preview_{customer_idx}"
                    ):

                        fifo_df = detail_df.copy()

                        fifo_df["_請求日"] = pd.to_datetime(
                            fifo_df["請求日"],
                            errors="coerce"
                        )

                        fifo_df["_表示順"] = range(
                            len(fifo_df)
                        )

                        fifo_df = fifo_df.sort_values(
                            ["_請求日", "_表示順"],
                            na_position="last",
                            kind="stable"
                        )

                        remaining = payment_amount
                        candidates = []

                        for _, detail in fifo_df.iterrows():

                            if remaining <= 0:
                                break

                            scheduled_amount = min(
                                int(detail["残高"]),
                                remaining
                            )

                            if scheduled_amount <= 0:
                                continue

                            candidates.append({
                                "コード": detail["コード"],
                                "請求日": detail["請求日"],
                                "請求額": detail["請求金額"],
                                "残高": detail["残高"],
                                "消込予定": scheduled_amount,
                                "未収科目": detail["未収科目"],
                                "未収補助": detail["未収補助"],
                                "部門": detail["部門"]
                            })

                            remaining -= scheduled_amount

                        st.session_state[
                            f"payment_candidates_{customer_idx}"
                        ] = {
                            "payment_date": payment_date,
                            "payment_amount": payment_amount,
                            "items": candidates
                        }

                    candidate_state = st.session_state.get(
                        f"payment_candidates_{customer_idx}"
                    )

                    if candidate_state:

                        candidates = candidate_state["items"]

                        if candidates:

                            st.write("FIFO候補")

                            st.dataframe(
                                pd.DataFrame(candidates),
                                use_container_width=True
                            )

                            st.write(
                                "合計消込予定:",
                                sum(
                                    item["消込予定"]
                                    for item in candidates
                                )
                            )

                            if st.button(
                                "消込実行",
                                key=f"payment_execute_{customer_idx}"
                            ):

                                try:

                                    settlement_id = apply_receivable_candidates(
                                        candidates,
                                        candidate_state["payment_date"]
                                    )

                                    journal_groups = {}

                                    for item in candidates:

                                        group_key = (
                                            item["未収科目"],
                                            item["未収補助"],
                                            item["部門"]
                                        )

                                        journal_groups[group_key] = (
                                            journal_groups.get(
                                                group_key,
                                                0
                                            )
                                            + item["消込予定"]
                                        )

                                    st.session_state[
                                        "generated_receivable_journal"
                                    ] = {
                                        "settlement_id": settlement_id,
                                        "settlement_date": candidate_state[
                                            "payment_date"
                                        ],
                                        "rows": [
                                            {
                                                "借方科目": "普通預金",
                                                "貸方科目": account,
                                                "貸方補助": sub_account,
                                                "部門": department,
                                                "金額": amount,
                                                "摘要": f"{customer_name}入金"
                                            }
                                            for (
                                                account,
                                                sub_account,
                                                department
                                            ), amount in journal_groups.items()
                                        ]
                                    }

                                    del st.session_state[
                                        f"payment_candidates_{customer_idx}"
                                    ]

                                    st.session_state[
                                        "receivable_success"
                                    ] = "消込が完了しました"

                                    st.rerun()

                                except Exception as e:

                                    st.error(str(e))

                        else:

                            st.info("消込候補がありません")

    with st.expander("消込履歴"):

        history_df = load_receivable_history()

        if history_df.empty:

            st.info("消込履歴がありません")

        else:

            st.dataframe(
                history_df[
                    ["消込日", "コード", "消込額"]
                ],
                use_container_width=True
            )

    with st.expander("生成仕訳"):

        generated_journal = st.session_state.get(
            "generated_receivable_journal"
        )

        if generated_journal is None:

            st.info("生成された仕訳はありません")

        else:

            if (
                isinstance(generated_journal, dict)
                and "rows" in generated_journal
            ):
                journal_rows = generated_journal["rows"]
                settlement_id = generated_journal[
                    "settlement_id"
                ]
                settlement_date = generated_journal[
                    "settlement_date"
                ]
            else:
                journal_rows = generated_journal
                settlement_id = None
                settlement_date = None

                if isinstance(journal_rows, dict):
                    journal_rows = [journal_rows]

            st.dataframe(
                pd.DataFrame(journal_rows),
                use_container_width=True
            )

            if settlement_id is None:

                st.info(
                    "この仕訳候補には消込IDがありません"
                )

            elif is_receivable_journal_registered(
                settlement_id
            ):

                st.success("仕訳登録済みです")

            elif st.button(
                "transactions.csvへ登録",
                key=f"register_receivable_{settlement_id}"
            ):

                try:

                    transaction_rows = []

                    for journal in journal_rows:

                        row = {
                            column: ""
                            for column in EPSON_COLUMNS
                        }

                        row[COL_DATE] = (
                            settlement_date.strftime("%Y%m%d")
                            if hasattr(settlement_date, "strftime")
                            else str(settlement_date)
                            .replace("/", "")
                            .replace("-", "")
                        )

                        row["借方科目"] = ACCOUNT_MASTER.get(
                            journal["借方科目"],
                            ""
                        )
                        row[COL_DEBIT] = journal["借方科目"]

                        row["貸方科目"] = ACCOUNT_MASTER.get(
                            journal["貸方科目"],
                            ""
                        )
                        row[COL_CREDIT] = journal["貸方科目"]

                        row["貸方補助"] = SUB_MASTER.get(
                            journal["貸方補助"],
                            ""
                        )
                        row[COL_CREDIT_SUB] = journal["貸方補助"]

                        row["貸方部門"] = DEPARTMENT_MASTER.get(
                            journal["部門"],
                            ""
                        )
                        row["貸方部門名"] = journal["部門"]

                        row[COL_DEBIT_AMOUNT] = str(journal["金額"])
                        row[COL_CREDIT_AMOUNT] = str(journal["金額"])
                        row[COL_SUMMARY] = journal["摘要"]

                        row["証番号"] = settlement_id
                        row["入力マシン"] = platform.node()
                        row["入力ユーザ"] = getpass.getuser()
                        row["入力アプリ"] = "未収消込"
                        row["入力会社"] = st.session_state.get(
                            "company_name",
                            ""
                        )
                        row["入力日付"] = datetime.now().strftime(
                            "%Y%m%d"
                        )

                        transaction_rows.append(row)

                    update_search_csv([transaction_rows])

                    mark_receivable_journal_registered(
                        settlement_id
                    )

                    st.cache_data.clear()
                    st.rerun()

                except Exception as e:

                    st.error(str(e))

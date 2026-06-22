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
import io

from ocr_gateway import DummyOcrGateway
from receivable_engine import (
    append_standard_receivables,
    apply_receivable_candidates,
    convert_company_billing_excel,
    exclude_duplicate_receivables,
    is_receivable_journal_registered,
    load_receivable_history,
    load_receivables,
    mark_receivable_journal_registered,
    normalize_standard_receivable_csv,
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

def load_payment_accounts():

    result = set()

    with open(
        "data/payment_accounts.csv",
        encoding="utf-8-sig"
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            raw_account = row.get("科目")

            if raw_account is None:
                continue

            account = str(raw_account).strip()

            if account and account.casefold() != "nan":
                result.add(account)

    return sorted(result)

def append_account_master(code, name):

    with open(
        "data/account_master.csv",
        "a",
        encoding="utf-8",
        newline=""
    ) as f:

        writer = csv.writer(f)
        writer.writerow([code, name])

def append_payment_account(name):

    accounts = load_payment_accounts()
    name = str(name).strip()

    if name and name.casefold() != "nan":
        accounts = sorted(set(accounts + [name]))

    with open(
        "data/payment_accounts.csv",
        "w",
        encoding="utf-8",
        newline=""
    ) as f:

        writer = csv.writer(f)
        writer.writerow(["科目"])
        writer.writerows(
            [account]
            for account in accounts
        )

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


def save_exported_journals(rows):

    duplicate_columns = [
        COL_DATE,
        "借方科目",
        COL_DEBIT,
        COL_DEBIT_SUB,
        "貸方科目",
        COL_CREDIT,
        COL_CREDIT_SUB,
        COL_DEBIT_AMOUNT,
        COL_SUMMARY,
    ]

    def row_key(row):

        values = []

        for column in duplicate_columns:
            value = " ".join(
                str(row.get(column, "")).split()
            )

            if column == COL_DATE:
                value = value.replace("/", "").replace("-", "")
            elif column == COL_DEBIT_AMOUNT:
                value = value.replace(",", "")

            values.append(value)

        return tuple(values)

    try:
        existing_df = pd.read_csv(
            "data/transactions.csv",
            dtype=str
        ).fillna("")
    except (FileNotFoundError, pd.errors.EmptyDataError):
        existing_df = pd.DataFrame()

    registered_keys = {
        row_key(row)
        for _, row in existing_df.iterrows()
    }
    new_rows = []

    for row in rows:
        key = row_key(row)

        if key in registered_keys:
            continue

        registered_keys.add(key)
        new_rows.append(row)

    if new_rows:
        update_search_csv([new_rows])

    st.session_state["epson_export_success"] = (
        "エプソンCSVを作成し、検索DBも更新しました"
    )
    st.cache_data.clear()

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

def get_account_code(account_name):

    account_name = str(account_name).strip()

    return ACCOUNT_MASTER.get(
        account_name,
        name_to_code.get(account_name, "")
    )

def keep_receivable_customer_open(customer_name):

    st.session_state[
        "open_receivable_customer"
    ] = customer_name

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
    
    if "keyword_input" not in st.session_state:
        st.session_state["keyword_input"] = ""
    
    if "ocr_search_text_pending" in st.session_state:
        st.session_state["keyword_input"] = (
            st.session_state.pop("ocr_search_text_pending")
        )
    
    with st.sidebar.form(key="journal_search_form"):
        dept = st.selectbox(
            "部門",
            [""] + department_master,
            key="search_department"
        )

        keyword = st.text_input(
            "キーワード",
            key="keyword_input"
        )

        amount_input = st.number_input(
            "金額",
            value=None,
            min_value=0,
            step=1,
            placeholder="0",
            key="search_amount"
        )

        st.caption("条件を入力し、Enterキーでも検索できます。")
        search_clicked = st.form_submit_button(
            "検索",
            type="primary"
        )

    amount = (
        int(amount_input)
        if amount_input is not None and amount_input > 0
        else None
    )
    
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
                entered_amounts_valid = True
    
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

                    st.caption(f"過去金額: ¥{amount_value:,}")

                    if suggest:
                        st.caption(
                            f"平均金額: ¥{suggest['avg']:,}"
                        )

                    default_amt = (
                        amount
                        if len(rows) == 1
                        and amount is not None
                        and amount > 0
                        else None
                    )
    
                    amt = st.number_input(
                        "金額",
                        min_value=0,
                        value=default_amt,
                        step=1,
                        placeholder="今回の金額",
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
    
                    if amt is None or amt <= 0:
                        entered_amounts_valid = False
                        registered_amount = 0
                    else:
                        registered_amount = int(amt)

                    d_sum += registered_amount
                    c_sum += registered_amount
    
                    new_row = copy.deepcopy(r)
    
                    new_row[COL_DATE] = process_date
    
                    new_row[COL_DEBIT] = debit
                    new_row[COL_CREDIT] = credit
    
                    new_row[COL_DEBIT_SUB] = debit_sub
                    new_row[COL_CREDIT_SUB] = credit_sub
    
                    new_row[COL_DEBIT_AMOUNT] = (
                        str(registered_amount)
                        if registered_amount > 0
                        else ""
                    )
                    new_row[COL_CREDIT_AMOUNT] = (
                        str(registered_amount)
                        if registered_amount > 0
                        else ""
                    )
    
                    new_row[COL_SUMMARY] = memo
    
                    edited_rows.append(new_row)
    
                    if entered_amounts_valid:
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
    
                    if not entered_amounts_valid:

                        st.warning("今回の金額を入力してください")

                    elif d_sum != c_sum:
    
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

        if "epson_export_success" in st.session_state:
            st.success(
                st.session_state.pop("epson_export_success")
            )
    
        st.caption("登録済み仕訳をエプソン取込形式で保存します。")
        st.download_button(
            "エプソン取込CSVをダウンロード",
            epson_csv,
            "epson_output.csv",
            type="primary",
            on_click=save_exported_journals,
            args=(epson_rows,)
        )
    

elif mode == "未収消込":

    st.header("未収消込")

    if "receivable_import_success" in st.session_state:
        st.success(
            st.session_state.pop("receivable_import_success")
        )

    if "receivable_import_key" not in st.session_state:
        st.session_state.receivable_import_key = 0

    with st.expander("未収一覧CSV取込"):

        import_format = st.selectbox(
            "取込形式",
            [
                "標準未収CSV形式",
                "請求一覧Excel形式"
            ],
            key=(
                "receivable_import_format_"
                f"{st.session_state.receivable_import_key}"
            )
        )

        import_preview = None
        import_errors = pd.DataFrame()
        excluded_duplicate_count = 0

        if import_format == "標準未収CSV形式":

            uploaded_receivables = st.file_uploader(
                "未収一覧CSV",
                type=["csv"],
                key=(
                    "receivable_import_csv_"
                    f"{st.session_state.receivable_import_key}"
                )
            )

        else:

            invoice_date = st.date_input(
                "請求日",
                key=(
                    "company_invoice_date_"
                    f"{st.session_state.receivable_import_key}"
                )
            )

            specify_payment_due_date = st.checkbox(
                "入金予定日を指定する",
                key=(
                    "specify_payment_due_date_"
                    f"{st.session_state.receivable_import_key}"
                )
            )

            if specify_payment_due_date:
                payment_due_date = st.date_input(
                    "入金予定日",
                    key=(
                        "company_payment_due_date_"
                        f"{st.session_state.receivable_import_key}"
                    )
                )
            else:
                payment_due_date = None
                st.caption(
                    "入金予定日は請求日の翌月末で設定します"
                )

            default_receivable_account = st.selectbox(
                "既定の未収科目",
                account_master,
                index=(
                    account_master.index("未収運賃")
                    if "未収運賃" in account_master
                    else 0
                ),
                key=(
                    "company_receivable_account_"
                    f"{st.session_state.receivable_import_key}"
                )
            )

            import_department = st.selectbox(
                "部門",
                [""] + department_master,
                key=(
                    "company_receivable_department_"
                    f"{st.session_state.receivable_import_key}"
                )
            )

            uploaded_receivables = st.file_uploader(
                "請求一覧Excel",
                type=["xlsx", "xls"],
                key=(
                    "receivable_import_excel_"
                    f"{st.session_state.receivable_import_key}"
                )
            )

        if uploaded_receivables is not None:

            try:
                if import_format == "標準未収CSV形式":
                    try:
                        uploaded_receivables.seek(0)
                        source_receivables = pd.read_csv(
                            uploaded_receivables,
                            dtype=str,
                            encoding="utf-8-sig"
                        ).fillna("")
                    except UnicodeDecodeError:
                        uploaded_receivables.seek(0)
                        source_receivables = pd.read_csv(
                            uploaded_receivables,
                            dtype=str,
                            encoding="cp932"
                        ).fillna("")

                    import_preview, import_errors = (
                        normalize_standard_receivable_csv(
                            source_receivables
                        )
                    )

                else:
                    excel_data = uploaded_receivables.getvalue()
                    excel_file = pd.ExcelFile(
                        io.BytesIO(excel_data)
                    )
                    sheet_name = (
                        "プリント用"
                        if "プリント用" in excel_file.sheet_names
                        else excel_file.sheet_names[0]
                    )
                    raw_billing_df = pd.read_excel(
                        io.BytesIO(excel_data),
                        sheet_name=sheet_name,
                        header=None,
                        dtype=object
                    )
                    st.caption(f"読込シート: {sheet_name}")

                    standard_source, conversion_errors = (
                        convert_company_billing_excel(
                            raw_billing_df,
                            invoice_date,
                            payment_due_date,
                            default_receivable_account,
                            import_department
                        )
                    )
                    import_preview, validation_errors = (
                        normalize_standard_receivable_csv(
                            standard_source
                        )
                    )
                    company_duplicate_columns = [
                        "コード",
                        "得意先名",
                        "請求日",
                        "請求金額",
                        "未収科目",
                        "未収補助"
                    ]
                    import_preview, duplicate_errors = (
                        exclude_duplicate_receivables(
                            import_preview,
                            company_duplicate_columns
                        )
                    )
                    excluded_duplicate_count = len(
                        duplicate_errors
                    )
                    import_errors = pd.concat(
                        [
                            conversion_errors,
                            validation_errors,
                            duplicate_errors.drop(
                                columns=["未収ID"],
                                errors="ignore"
                            )
                        ],
                        ignore_index=True
                    )

                if excluded_duplicate_count:
                    st.warning(
                        "取り込み済みの請求を"
                        f"{excluded_duplicate_count}件除外しました"
                    )

                if not import_errors.empty:
                    st.error(
                        f"取込対象外の行が{len(import_errors)}件あります"
                    )
                    st.dataframe(
                        import_errors,
                        use_container_width=True
                    )

                st.write("取込プレビュー")
                st.dataframe(
                    import_preview.drop(
                        columns=["未収ID"],
                        errors="ignore"
                    ),
                    use_container_width=True
                )

                if import_preview.empty:
                    st.warning("取り込める明細がありません")

                else:
                    st.caption("プレビューの明細を未収一覧へ追加します。")

                if not import_preview.empty and st.button(
                    "未収一覧へ取り込む",
                    key=(
                        "append_receivables_"
                        f"{st.session_state.receivable_import_key}"
                    ),
                    type="primary"
                ):
                    duplicate_columns = (
                        [
                            "コード",
                            "得意先名",
                            "請求日",
                            "請求金額",
                            "未収科目",
                            "未収補助"
                        ]
                        if import_format == "請求一覧Excel形式"
                        else None
                    )
                    imported_count, duplicate_count = (
                        append_standard_receivables(
                            import_preview,
                            duplicate_columns=duplicate_columns
                        )
                    )

                    if imported_count:
                        message = (
                            f"未収一覧へ{imported_count}件取り込みました"
                        )
                    else:
                        message = "追加対象の未収明細はありません"

                    if excluded_duplicate_count or duplicate_count:
                        message += (
                            "（重複"
                            f"{excluded_duplicate_count + duplicate_count}"
                            "件を除外）"
                        )

                    st.session_state[
                        "receivable_import_success"
                    ] = message
                    st.session_state.receivable_import_key += 1
                    st.rerun()

            except ImportError as e:
                if (
                    import_format == "請求一覧Excel形式"
                    and "openpyxl" in str(e).casefold()
                ):
                    st.error(
                        "Excelファイルの読み込みに必要な "
                        "openpyxl がインストールされていません。"
                        "\n\n以下を実行してください："
                        "\n\n`pip install openpyxl`"
                    )
                else:
                    st.error(
                        f"未収一覧ファイルを読み込めません: {e}"
                    )
            except ValueError as e:
                st.error(str(e))
            except Exception as e:
                st.error(f"未収一覧ファイルを読み込めません: {e}")

    payment_accounts = load_payment_accounts()

    if "account_master_success" in st.session_state:
        for message in st.session_state.pop(
            "account_master_success"
        ):
            st.success(message)

    default_receipt_account = (
        "普通預金"
        if "普通預金" in payment_accounts
        else payment_accounts[0]
    )

    if (
        "receipt_account" not in st.session_state
        or st.session_state.receipt_account
        not in payment_accounts
    ):
        st.session_state.receipt_account = default_receipt_account

    if "receivable_success" in st.session_state:
        st.success(
            st.session_state.pop("receivable_success")
        )

    receivables_df = load_receivables()

    if receivables_df.empty:

        st.info("未収データがありません")

    else:

        receivables_df = receivables_df.copy()

        receivables_df = receivables_df[
            receivables_df["得意先名"].astype(str).str.strip() != ""
        ].copy()

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
                    f"{customer_name}の明細",
                    expanded=(
                        st.session_state.get(
                            "open_receivable_customer"
                        ) == customer_name
                    )
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

                    detail_display_columns = [
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

                    detail_df = detail_df[
                        ["未収ID"] + detail_display_columns
                    ]

                    st.dataframe(
                        detail_df[detail_display_columns],
                        use_container_width=True
                    )

                    with st.form(
                        key=f"payment_form_{customer_idx}_{customer_name}"
                    ):
                        payment_date = st.date_input(
                            "入金日",
                            key=f"payment_date_{customer_idx}"
                        )

                        payment_amount = st.number_input(
                            "入金額",
                            value=None,
                            min_value=0,
                            step=1,
                            placeholder="0",
                            key=f"payment_amount_{customer_idx}"
                        )

                        receipt_account = st.selectbox(
                            "入金科目",
                            payment_accounts,
                            index=(
                                payment_accounts.index(
                                    st.session_state.receipt_account
                                )
                            ),
                            key=f"receipt_account_{customer_idx}"
                        )

                        st.caption(
                            "入金額を入力し、Enterキーでも候補を表示できます。"
                        )
                        payment_preview_submitted = (
                            st.form_submit_button(
                                "入金候補を表示",
                                on_click=keep_receivable_customer_open,
                                args=(customer_name,),
                                type="primary"
                            )
                        )

                    with st.expander("未登録科目を追加"):

                        new_account_code = st.text_input(
                            "科目コード",
                            key=f"new_account_code_{customer_idx}"
                        ).strip()

                        new_account_name = st.text_input(
                            "科目名",
                            key=f"new_account_name_{customer_idx}"
                        ).strip()

                        add_to_payment_accounts = st.checkbox(
                            "入金科目として追加する",
                            key=f"add_payment_account_{customer_idx}"
                        )

                        if st.button(
                            "登録",
                            key=f"add_account_master_{customer_idx}"
                        ):

                            registered_name = next((
                                name
                                for name, code
                                in ACCOUNT_MASTER.items()
                                if str(code).strip()
                                == new_account_code
                            ), None)

                            registered_code = str(
                                ACCOUNT_MASTER.get(
                                    new_account_name,
                                    ""
                                )
                            ).strip()

                            if not new_account_code or not new_account_name:
                                st.warning(
                                    "科目コードと科目名を入力してください"
                                )

                            elif (
                                registered_name
                                and registered_name != new_account_name
                            ):
                                st.warning(
                                    "同じ科目コードが登録済みです"
                                )

                            elif (
                                registered_code
                                and registered_code != new_account_code
                            ):
                                st.warning(
                                    "同じ科目名が登録済みです"
                                )

                            else:
                                account_registered = (
                                    registered_code
                                    == new_account_code
                                )

                                if account_registered:
                                    messages = [
                                        "科目マスターには既に登録されています"
                                    ]
                                else:
                                    append_account_master(
                                        new_account_code,
                                        new_account_name
                                    )
                                    messages = [
                                        "科目マスターへ追加しました"
                                    ]

                                if add_to_payment_accounts:
                                    if (
                                        new_account_name
                                        in payment_accounts
                                    ):
                                        messages.append(
                                            "入金科目候補にも既に登録されています"
                                        )
                                    else:
                                        append_payment_account(
                                            new_account_name
                                        )
                                        messages.append(
                                            "入金科目候補に追加しました"
                                            if account_registered
                                            else "入金科目候補にも追加しました"
                                        )

                                st.session_state[
                                    "account_master_success"
                                ] = messages

                                st.rerun()

                    if (
                        payment_preview_submitted
                        and (
                            payment_amount is None
                            or payment_amount <= 0
                        )
                    ):
                        st.warning("入金額を入力してください")

                    if (
                        payment_preview_submitted
                        and payment_amount is not None
                        and payment_amount > 0
                    ):

                        st.session_state.receipt_account = receipt_account

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
                                "未収ID": detail["未収ID"],
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
                            "receipt_account": receipt_account,
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
                                pd.DataFrame(candidates).drop(
                                    columns=["未収ID"],
                                    errors="ignore"
                                ),
                                use_container_width=True
                            )

                            st.write(
                                "合計消込予定:",
                                sum(
                                    item["消込予定"]
                                    for item in candidates
                                )
                            )

                            st.caption("表示された候補で未収を消し込みます。")
                            if st.button(
                                "消込実行",
                                key=f"payment_execute_{customer_idx}",
                                type="primary"
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
                                                "借方科目": candidate_state[
                                                    "receipt_account"
                                                ],
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

            missing_account_names = sorted({
                account_name
                for journal in journal_rows
                for account_name in (
                    journal["借方科目"],
                    journal["貸方科目"]
                )
                if account_name
                and not get_account_code(account_name)
            })

            if missing_account_names:
                st.warning(
                    "科目コードを補完できない科目があります: "
                    + "、".join(missing_account_names)
                    + "。account_master.csvを確認してください。"
                )

            journal_registered = (
                settlement_id is not None
                and is_receivable_journal_registered(settlement_id)
            )

            if settlement_id is None:

                st.info(
                    "この仕訳候補には消込IDがありません"
                )

            elif journal_registered:

                st.success("仕訳登録済みです")

            else:
                st.caption("生成した仕訳をCSV出力対象へ追加します。")

            if (
                settlement_id is not None
                and not journal_registered
                and st.button(
                    "この仕訳をCSV出力対象へ登録",
                    key=f"register_receivable_{settlement_id}",
                    type="primary"
                )
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

                        row["借方科目"] = get_account_code(
                            journal["借方科目"]
                        )
                        row[COL_DEBIT] = journal["借方科目"]

                        row["貸方科目"] = get_account_code(
                            journal["貸方科目"]
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

                    mark_receivable_journal_registered(
                        settlement_id
                    )

                    if "confirmed" not in st.session_state:
                        st.session_state.confirmed = []

                    st.session_state.confirmed.append(
                        copy.deepcopy(transaction_rows)
                    )

                    st.session_state[
                        "receivable_success"
                    ] = "仕訳を登録し、CSV出力対象に追加しました"

                    st.cache_data.clear()
                    st.rerun()

                except Exception as e:

                    st.error(str(e))

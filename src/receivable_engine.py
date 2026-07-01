import pandas as pd
import shutil
import os
import uuid

from datetime import datetime

STANDARD_RECEIVABLE_COLUMNS = [
    "取引先",
    "請求日",
    "入金予定日",
    "未収科目",
    "未収補助",
    "部門",
    "摘要",
    "請求額",
    "残高"
]

STANDARD_RECEIVABLE_REQUIRED_COLUMNS = [
    "取引先",
    "請求日",
    "請求額",
    "残高",
    "摘要"
]

STANDARD_RECEIVABLE_OPTIONAL_COLUMNS = [
    "入金予定日",
    "未収科目",
    "未収補助",
    "部門"
]

CURRENT_RECEIVABLE_COLUMNS = [
    "コード",
    "未収ID",
    "得意先名",
    "請求日",
    "入金予定日",
    "未収科目",
    "未収補助",
    "部門",
    "摘要",
    "請求金額",
    "入金済額",
    "残高",
    "ステータス"
]


def remove_empty_receivable_rows(df):

    df = df.copy().fillna("")
    stripped_df = df.apply(
        lambda column: column.astype(str).str.strip()
    )
    completely_empty = stripped_df.eq("").all(axis=1)

    customer_empty = stripped_df.get(
        "得意先名",
        pd.Series("", index=df.index)
    ).eq("")

    amounts_are_zero = pd.Series(True, index=df.index)
    for column in ["請求金額", "入金済額", "残高"]:
        values = stripped_df.get(
            column,
            pd.Series("", index=df.index)
        ).str.replace(",", "", regex=False)
        numeric_values = pd.to_numeric(values, errors="coerce")
        amounts_are_zero &= values.eq("") | numeric_values.eq(0)

    empty_data = customer_empty & amounts_are_zero
    remove_mask = completely_empty | empty_data

    return df.loc[~remove_mask].reset_index(drop=True), remove_mask.any()

# =========================================
# 未収CSV読み込み
# =========================================
def load_receivables():

    path = "data/receivables/current.csv"

    try:

        df = pd.read_csv(
            path,
            dtype=str
        ).fillna("")

        df, removed_empty_rows = remove_empty_receivable_rows(df)

        defaults = {
            "未収科目": "売掛金",
            "未収補助": "",
            "部門": ""
        }

        for column, default_value in defaults.items():
            if column not in df.columns:
                df[column] = default_value

        migrated = removed_empty_rows
        code_values = df["コード"].astype(str)

        if "未収ID" not in df.columns:
            df.insert(1, "未収ID", code_values)
            migrated = True
        else:
            empty_ids = df["未収ID"].astype(str).str.strip() == ""
            if empty_ids.any():
                df.loc[empty_ids, "未収ID"] = code_values.loc[
                    empty_ids
                ]
                migrated = True

        legacy_codes = code_values.str.extract(
            r"^(.+)-([0-9a-fA-F]{32})$",
            expand=True
        )
        legacy_mask = legacy_codes[0].notna()

        if legacy_mask.any():
            df.loc[legacy_mask, "コード"] = legacy_codes.loc[
                legacy_mask,
                0
            ]
            migrated = True

        if migrated:
            df.to_csv(
                path,
                index=False,
                encoding="utf-8-sig"
            )

        return df

    except Exception as e:

        print("未収CSV読込エラー:", e)

        return pd.DataFrame(
            columns=[
                "コード",
                "未収ID",
                "得意先名",
                "請求金額",
                "残高",
                "ステータス",
                "未収科目",
                "未収補助",
                "部門"
            ]
        )


def organize_completed_receivables():

    current_path = "data/receivables/current.csv"
    receivables_df = load_receivables()

    balances = pd.to_numeric(
        receivables_df["残高"].astype(str).str.replace(",", ""),
        errors="coerce"
    )
    completed_mask = (
        receivables_df["ステータス"].astype(str).str.strip().eq("完了")
        | balances.le(0)
    )
    organized_count = int(completed_mask.sum())

    if organized_count == 0:
        return 0

    remaining_df = receivables_df.loc[~completed_mask].copy()
    remaining_df, _ = remove_empty_receivable_rows(remaining_df)
    remaining_df.to_csv(
        current_path,
        index=False,
        encoding="utf-8-sig"
    )

    return organized_count

# =========================================
# 未収CSV取込
# =========================================
def import_receivable_csv(upload_path):

    # フォルダ作成
    os.makedirs(
        "data/receivables/logs",
        exist_ok=True
    )

    now = datetime.now()

    ym = f"{now.year}_{now.month:02d}"

    log_path = f"data/receivables/logs/{ym}.csv"

    current_path = "data/receivables/current.csv"

    # logs保存
    shutil.copy(upload_path, log_path)

    # current更新
    shutil.copy(upload_path, current_path)
    load_receivables()

    cleanup_logs()

    print("未収CSV取込完了")

# =========================================
# 標準未収CSV変換
# =========================================
def normalize_standard_receivable_csv(source_df):

    source_df = source_df.copy()
    source_df.columns = [
        str(column).strip()
        for column in source_df.columns
    ]

    missing_columns = [
        column
        for column in STANDARD_RECEIVABLE_REQUIRED_COLUMNS
        if column not in source_df.columns
    ]

    if missing_columns:
        raise ValueError(
            "必須列が不足しています: "
            + "、".join(missing_columns)
        )

    for column in STANDARD_RECEIVABLE_OPTIONAL_COLUMNS:
        if column not in source_df.columns:
            source_df[column] = ""

    if "コード" in source_df.columns:
        source_codes = (
            source_df["コード"]
            .fillna("")
            .astype(str)
            .str.strip()
            .reset_index(drop=True)
        )
    else:
        source_codes = pd.Series(
            "",
            index=range(len(source_df)),
            dtype=str
        )

    source_df = source_df[
        STANDARD_RECEIVABLE_COLUMNS
    ].fillna("").reset_index(drop=True)

    for column in STANDARD_RECEIVABLE_COLUMNS:
        source_df[column] = (
            source_df[column]
            .astype(str)
            .str.strip()
        )

    error_messages = pd.Series(
        "",
        index=source_df.index,
        dtype=str
    )

    def add_error(mask, message):

        error_messages.loc[mask] = (
            error_messages.loc[mask]
            .apply(
                lambda current: (
                    f"{current}、{message}"
                    if current
                    else message
                )
            )
        )

    for column in ["取引先", "摘要"]:
        add_error(
            source_df[column].eq(""),
            f"{column}は必須です"
        )

    def parse_standard_date_values(values):

        parsed_dates = pd.Series(
            pd.NaT,
            index=source_df.index,
            dtype="datetime64[ns]"
        )

        hyphenated = values.str.fullmatch(
            r"\d{4}-\d{2}-\d{2}"
        )
        slashed = values.str.fullmatch(
            r"\d{4}/\d{2}/\d{2}"
        )
        compact = values.str.fullmatch(r"\d{8}")

        parsed_dates.loc[hyphenated] = pd.to_datetime(
            values.loc[hyphenated],
            format="%Y-%m-%d",
            errors="coerce"
        )
        parsed_dates.loc[slashed] = pd.to_datetime(
            values.loc[slashed],
            format="%Y/%m/%d",
            errors="coerce"
        )
        parsed_dates.loc[compact] = pd.to_datetime(
            values.loc[compact],
            format="%Y%m%d",
            errors="coerce"
        )

        return parsed_dates

    normalized_dates = {}

    invoice_dates = parse_standard_date_values(source_df["請求日"])

    add_error(
        invoice_dates.isna(),
        "請求日を変換できません"
    )

    default_due_dates = invoice_dates.apply(
        lambda value: (
            pd.NaT
            if pd.isna(value)
            else (
                value.to_period("M") + 1
            ).to_timestamp(how="end").normalize()
        )
    )
    due_dates = parse_standard_date_values(source_df["入金予定日"])
    empty_due_dates = source_df["入金予定日"].eq("")
    due_dates.loc[empty_due_dates] = default_due_dates.loc[
        empty_due_dates
    ]

    add_error(
        due_dates.isna(),
        "入金予定日を変換できません"
    )

    normalized_dates["請求日"] = invoice_dates.dt.strftime(
        "%Y-%m-%d"
    )
    normalized_dates["入金予定日"] = due_dates.dt.strftime(
        "%Y-%m-%d"
    )

    source_df.loc[
        source_df["未収科目"].eq(""),
        "未収科目"
    ] = "未収運賃"
    source_df.loc[
        source_df["未収補助"].eq(""),
        "未収補助"
    ] = source_df.loc[
        source_df["未収補助"].eq(""),
        "取引先"
    ]

    normalized_amounts = {}

    for column in ["請求額", "残高"]:

        values = source_df[column].str.replace(
            ",",
            "",
            regex=False
        )
        numeric_values = pd.to_numeric(
            values,
            errors="coerce"
        )
        invalid_amounts = (
            numeric_values.isna()
            | (numeric_values % 1 != 0)
        )

        add_error(
            invalid_amounts,
            f"{column}を数値化できません"
        )

        normalized_amounts[column] = (
            numeric_values
            .where(~invalid_amounts)
            .astype("Int64")
            .astype(str)
        )

    receivable_ids = [
        uuid.uuid4().hex
        for _ in range(len(source_df))
    ]

    standard_df = pd.DataFrame({
        "コード": [
            source_code if source_code else receivable_id
            for source_code, receivable_id in zip(
                source_codes,
                receivable_ids
            )
        ],
        "未収ID": receivable_ids,
        "得意先名": source_df["取引先"],
        "請求日": normalized_dates["請求日"],
        "入金予定日": normalized_dates["入金予定日"],
        "未収科目": source_df["未収科目"],
        "未収補助": source_df["未収補助"],
        "部門": source_df["部門"],
        "摘要": source_df["摘要"],
        "請求金額": normalized_amounts["請求額"],
        "入金済額": "0",
        "残高": normalized_amounts["残高"],
        "ステータス": "未完了"
    })[CURRENT_RECEIVABLE_COLUMNS]

    valid_rows = error_messages == ""

    error_df = source_df.loc[~valid_rows].copy()
    if not error_df.empty:
        source_row_numbers = pd.Series(
            range(2, len(source_df) + 2),
            index=source_df.index
        )
        error_df.insert(
            0,
            "CSV行",
            source_row_numbers.loc[~valid_rows]
        )
        error_df["エラー"] = error_messages.loc[~valid_rows]

    return (
        standard_df.loc[valid_rows].reset_index(drop=True),
        error_df.reset_index(drop=True)
    )

# =========================================
# 請求一覧Excel形式変換
# =========================================
def convert_company_billing_excel(
    raw_df,
    invoice_date,
    payment_due_date,
    default_account,
    department=""
):

    required_headers = {
        "コード",
        "得意先名１",
        "繰越し"
    }
    header_index = None
    header_values = None

    for index, row in raw_df.iterrows():

        values = [
            ""
            if pd.isna(value)
            else str(value).strip()
            for value in row.tolist()
        ]

        if required_headers.issubset(set(values)):
            header_index = index
            header_values = values
            break

    if header_index is None:
        raise ValueError(
            "見出し行にコード・得意先名１・繰越しがありません"
        )

    detail_df = raw_df.loc[
        raw_df.index > header_index
    ].copy()
    detail_df.columns = header_values
    detail_df = detail_df.reset_index(drop=False)

    invoice_date = pd.Timestamp(invoice_date)

    if payment_due_date is None:
        payment_due_date = (
            invoice_date.to_period("M") + 1
        ).to_timestamp("M")
    else:
        payment_due_date = pd.Timestamp(payment_due_date)

    invoice_date_text = invoice_date.strftime("%Y-%m-%d")
    payment_due_date_text = payment_due_date.strftime(
        "%Y-%m-%d"
    )

    standard_rows = []
    error_rows = []

    for detail_index, row in detail_df.iterrows():

        code_value = row.get("コード", "")
        customer_value = row.get("得意先名１", "")
        balance_value = row.get("繰越し", "")

        values = [code_value, customer_value, balance_value]
        if all(
            pd.isna(value) or str(value).strip() == ""
            for value in values
        ):
            continue

        code_numeric = pd.to_numeric(
            str(code_value).strip(),
            errors="coerce"
        )
        balance_numeric = pd.to_numeric(
            str(balance_value)
            .replace(",", "")
            .replace("¥", "")
            .strip(),
            errors="coerce"
        )
        customer_name = (
            ""
            if pd.isna(customer_value)
            else str(customer_value).strip()
        )

        errors = []

        if pd.isna(code_numeric):
            errors.append("コードが数値ではありません")

        if not customer_name:
            errors.append("得意先名１が空欄です")

        if pd.isna(balance_numeric):
            errors.append("繰越しを数値化できません")
        elif balance_numeric <= 0:
            errors.append("繰越しが0以下です")
        elif balance_numeric % 1 != 0:
            errors.append("繰越しに小数があります")

        if errors:
            error_rows.append({
                "Excel行": int(row["index"]) + 1,
                "コード": code_value,
                "得意先名１": customer_value,
                "繰越し": balance_value,
                "エラー": "、".join(errors)
            })
            continue

        source_code = (
            str(int(code_numeric))
            if code_numeric % 1 == 0
            else str(code_numeric)
        )
        balance_text = str(int(balance_numeric))

        standard_rows.append({
            "コード": source_code,
            "取引先": customer_name,
            "請求日": invoice_date_text,
            "入金予定日": payment_due_date_text,
            "未収科目": str(default_account).strip(),
            "未収補助": customer_name,
            "部門": str(department).strip(),
            "摘要": f"{customer_name} 繰越未収",
            "請求額": balance_text,
            "残高": balance_text
        })

    standard_source_df = pd.DataFrame(
        standard_rows,
        columns=["コード"] + STANDARD_RECEIVABLE_COLUMNS
    )
    error_df = pd.DataFrame(
        error_rows,
        columns=[
            "Excel行",
            "コード",
            "得意先名１",
            "繰越し",
            "エラー"
        ]
    )

    return standard_source_df, error_df

# =========================================
# 標準未収データ追記
# =========================================
def exclude_duplicate_receivables(
    standard_df,
    duplicate_columns
):

    current_df = load_receivables()

    def row_key(row):

        values = []

        for column in duplicate_columns:
            value = str(row.get(column, "")).strip()

            if column in ["請求日", "入金予定日"]:
                normalized_date = None

                try:
                    if len(value) == 8 and value.isdigit():
                        normalized_date = datetime.strptime(
                            value,
                            "%Y%m%d"
                        )
                    else:
                        date_parts = value.replace(
                            "/",
                            "-"
                        ).split("-")

                        if len(date_parts) == 3:
                            normalized_date = datetime(
                                int(date_parts[0]),
                                int(date_parts[1]),
                                int(date_parts[2])
                            )
                except ValueError:
                    normalized_date = None

                if normalized_date is not None:
                    value = normalized_date.strftime(
                        "%Y-%m-%d"
                    )

            if column in ["請求金額", "残高"]:
                value = value.replace(",", "")
            values.append(value)

        return tuple(values)

    registered_keys = {
        row_key(row)
        for _, row in current_df.iterrows()
    }
    append_indexes = []
    duplicate_indexes = []

    for index, row in standard_df.iterrows():

        key = row_key(row)

        if key in registered_keys:
            duplicate_indexes.append(index)
            continue

        registered_keys.add(key)
        append_indexes.append(index)

    append_df = standard_df.loc[append_indexes].copy()
    duplicate_df = standard_df.loc[duplicate_indexes].copy()

    if not duplicate_df.empty:
        duplicate_df["エラー"] = (
            "既に取り込み済みの請求です"
        )

    return append_df, duplicate_df


def append_standard_receivables(
    standard_df,
    duplicate_columns=None
):

    current_path = "data/receivables/current.csv"
    current_df = load_receivables()

    if duplicate_columns is None:
        duplicate_columns = [
            "得意先名",
            "請求日",
            "入金予定日",
            "未収科目",
            "未収補助",
            "部門",
            "摘要",
            "請求金額",
            "残高"
        ]

    append_df, duplicate_df = exclude_duplicate_receivables(
        standard_df,
        duplicate_columns
    )
    duplicate_count = len(duplicate_df)

    if append_df.empty:
        return 0, duplicate_count

    save_columns = CURRENT_RECEIVABLE_COLUMNS + [
        column
        for column in current_df.columns
        if column not in CURRENT_RECEIVABLE_COLUMNS
    ]

    current_df = current_df.reindex(
        columns=save_columns,
        fill_value=""
    )
    append_df = append_df.reindex(
        columns=save_columns,
        fill_value=""
    )

    save_df = pd.concat(
        [current_df, append_df],
        ignore_index=True
    )

    save_df, _ = remove_empty_receivable_rows(save_df)

    save_df.to_csv(
        current_path,
        index=False,
        encoding="utf-8-sig"
    )

    return len(append_df), duplicate_count

# =========================================
# 古いログ削除
# =========================================
def cleanup_logs():

    log_dir = "data/receivables/logs"

    keep_months = 6

    now = datetime.now()

    if not os.path.exists(log_dir):
        return

    for file in os.listdir(log_dir):

        if not file.endswith(".csv"):
            continue

        try:

            year, month = file.replace(".csv", "").split("_")

            file_date = datetime(
                int(year),
                int(month),
                1
            )

            diff = (
                (now.year - file_date.year) * 12
                + (now.month - file_date.month)
            )

            if diff > keep_months:

                os.remove(
                    os.path.join(log_dir, file)
                )

                print("削除:", file)

        except Exception as e:

            print("ログ削除エラー:", e)

# =========================================
# 未収消込（完全一致）
# =========================================
def match_receivable(receivables_df, customer_name, payment_amount):

    try:

        # 数値化
        payment_amount = int(payment_amount)

        # 取引先一致
        target_df = receivables_df[
            (receivables_df["得意先名"] == customer_name)
            &
            (receivables_df["ステータス"] != "完了")
        ].copy()

        if target_df.empty:

            return None

        # 金額一致
        for _, row in target_df.iterrows():

            try:

                balance = int(row["残高"])

                if balance == payment_amount:

                    return row.to_dict()

            except:
                continue

        return None

    except Exception as e:

        print("未収照合エラー:", e)

        return None
    
# =========================================
# FIFO消込
# =========================================
def match_receivable_fifo(
    receivables_df,
    customer_name,
    payment_amount
):

    try:

        payment_amount = int(payment_amount)

        # 取引先一致
        target_df = receivables_df[
            (receivables_df["得意先名"] == customer_name)
            &
            (receivables_df["ステータス"] != "完了")
        ].copy()

        if target_df.empty:
            return []

        # 残高数値化
        target_df["残高_num"] = (
            target_df["残高"]
            .astype(int)
        )

        result = []

        remaining = payment_amount

        # 上から順に処理（FIFO）
        for _, row in target_df.iterrows():

            balance = row["残高_num"]

            if remaining <= 0:
                break

            # 全額消込
            if balance <= remaining:

                result.append({
                    "コード": row["コード"],
                    "得意先名": row["得意先名"],
                    "消込額": balance,
                    "残高": balance,
                    "状態": "全額"
                })

                remaining -= balance

            # 部分消込
            else:

                new_balance = balance - remaining

                result.append({
                    "コード": row["コード"],
                    "得意先名": row["得意先名"],
                    "消込額": remaining,
                    "元残高": balance,
                    "消込後残高": new_balance,
                    "状態": "部分"
                })

                remaining = 0

        return {
            "matched": result,
            "remaining": remaining
        }

    except Exception as e:

        print("FIFO消込エラー:", e)

        return []
    
# =========================================
# current.csv 更新
# =========================================
def update_receivables(receivables_df, matched_result):

    try:

        for item in matched_result:

            code = item["コード"]

            status = item["状態"]

            # 対象行取得
            idx = receivables_df[
                receivables_df["コード"] == code
            ].index

            if len(idx) == 0:
                continue

            idx = idx[0]

            # 全額消込
            if status == "全額":

                receivables_df.at[idx, "残高"] = "0"
                receivables_df.at[idx, "ステータス"] = "完了"

            # 部分消込
            elif status == "部分":

                receivables_df.at[
                    idx,
                    "残高"
                ] = str(item["消込後残高"])

                receivables_df.at[
                    idx,
                    "ステータス"
                ] = "部分消込"

        # 保存
        receivables_df, _ = remove_empty_receivable_rows(
            receivables_df
        )
        receivables_df.to_csv(
            "data/receivables/current.csv",
            index=False,
            encoding="utf-8-sig"
        )

        save_receivable_history(matched_result)

        print("未収更新完了")

    except Exception as e:

        print("未収更新エラー:", e)

# =========================================
# FIFO候補反映
# =========================================
def apply_receivable_candidates(
    candidates,
    settlement_date=None,
    settlement_id=None
):

    receivables_df = load_receivables()
    history_rows = []

    if settlement_id is None:
        settlement_id = uuid.uuid4().hex

    if settlement_date is None:
        settlement_date = datetime.now()

    if hasattr(settlement_date, "strftime"):
        settlement_date = settlement_date.strftime("%Y/%m/%d")
    else:
        settlement_date = str(settlement_date).replace("-", "/")

    for candidate in candidates:

        code = str(candidate["コード"])
        receivable_id = str(candidate.get("未収ID", ""))
        scheduled_amount = int(candidate["消込予定"])

        if receivable_id and "未収ID" in receivables_df.columns:
            target_indexes = receivables_df[
                receivables_df["未収ID"] == receivable_id
            ].index
        else:
            target_indexes = receivables_df[
                receivables_df["コード"] == code
            ].index

        if len(target_indexes) != 1:
            raise ValueError(
                f"未収明細を特定できません: {code}"
            )

        target_index = target_indexes[0]

        current_balance = int(
            str(
                receivables_df.at[target_index, "残高"]
            ).replace(",", "")
        )

        if scheduled_amount <= 0:
            raise ValueError(
                f"消込予定額が不正です: {code}"
            )

        if scheduled_amount > current_balance:
            raise ValueError(
                f"残高が変更されています: {code}"
            )

        new_balance = current_balance - scheduled_amount

        history_rows.append({
            "消込ID": settlement_id,
            "消込日": settlement_date,
            "得意先名": receivables_df.at[
                target_index,
                "得意先名"
            ],
            "コード": code,
            "消込額": scheduled_amount,
            "消込前残高": current_balance,
            "消込後残高": new_balance,
            "仕訳登録済": "0"
        })

        receivables_df.at[
            target_index,
            "残高"
        ] = str(new_balance)

        receivables_df.at[
            target_index,
            "ステータス"
        ] = (
            "完了"
            if new_balance == 0
            else "部分消込"
        )

    receivables_df, _ = remove_empty_receivable_rows(
        receivables_df
    )
    receivables_df.to_csv(
        "data/receivables/current.csv",
        index=False,
        encoding="utf-8-sig"
    )

    save_receivable_history(history_rows)

    return settlement_id


def calculate_receivable_difference(payment_amount, target_total):

    return int(payment_amount) - int(target_total)


def build_receivable_journal_rows(
    candidates,
    payment_amount,
    receipt_account,
    customer_name,
    difference_account=None,
    difference_side=None
):

    payment_amount = int(payment_amount)
    target_total = sum(
        int(candidate["消込予定"])
        for candidate in candidates
    )
    difference = calculate_receivable_difference(
        payment_amount,
        target_total
    )

    def allocate_candidates(amount, offset=0):

        allocated = []
        remaining_offset = int(offset)
        remaining_amount = int(amount)

        for candidate in candidates:

            candidate_amount = int(candidate["消込予定"])

            if remaining_offset >= candidate_amount:
                remaining_offset -= candidate_amount
                continue

            available_amount = candidate_amount - remaining_offset
            remaining_offset = 0
            allocated_amount = min(available_amount, remaining_amount)

            if allocated_amount > 0:
                allocated_item = candidate.copy()
                allocated_item["消込予定"] = allocated_amount
                allocated.append(allocated_item)
                remaining_amount -= allocated_amount

            if remaining_amount <= 0:
                break

        return allocated

    def group_candidates(target_candidates):

        journal_groups = {}

        for item in target_candidates:

            amount = int(item["消込予定"])
            if amount <= 0:
                continue

            group_key = (
                item["未収科目"],
                item["未収補助"],
                item["部門"]
            )
            journal_groups[group_key] = (
                journal_groups.get(group_key, 0) + amount
            )

        return journal_groups

    def build_receivable_rows(target_candidates, debit_account, summary):

        return [
            {
                "借方科目": debit_account,
                "貸方科目": account,
                "貸方補助": sub_account,
                "部門": department,
                "金額": amount,
                "摘要": summary
            }
            for (
                account,
                sub_account,
                department
            ), amount in group_candidates(target_candidates).items()
        ]

    if difference == 0:
        return build_receivable_rows(
            candidates,
            receipt_account,
            f"{customer_name}入金"
        )

    if not difference_account or not difference_side:
        raise ValueError("差額処理の科目を選択してください")

    rows = []
    difference_amount = abs(difference)

    if difference_side == "debit":

        rows.extend(
            build_receivable_rows(
                allocate_candidates(payment_amount),
                receipt_account,
                f"{customer_name}入金"
            )
        )
        rows.extend(
            build_receivable_rows(
                allocate_candidates(
                    difference_amount,
                    offset=payment_amount
                ),
                difference_account,
                f"{customer_name}差額処理"
            )
        )

    elif difference_side == "credit":

        rows.extend(
            build_receivable_rows(
                candidates,
                receipt_account,
                f"{customer_name}入金"
            )
        )
        rows.append({
            "借方科目": receipt_account,
            "貸方科目": difference_account,
            "貸方補助": "",
            "部門": "",
            "金額": difference_amount,
            "摘要": f"{customer_name}差額処理"
        })
    else:
        raise ValueError("差額処理区分が不正です")

    return rows

# =========================================
# 消込履歴
# =========================================
HISTORY_COLUMNS = [
    "消込ID",
    "消込日",
    "得意先名",
    "コード",
    "消込額",
    "消込前残高",
    "消込後残高",
    "仕訳登録済"
]


def normalize_receivable_history(history_df):

    def get_column(*names):

        for name in names:
            if name in history_df.columns:
                return history_df[name].astype(str)

        return pd.Series(
            "",
            index=history_df.index,
            dtype=str
        )

    settlement_dates = get_column("消込日", "日時")
    settlement_dates = (
        settlement_dates
        .str[:10]
        .str.replace("-", "/", regex=False)
    )

    return pd.DataFrame({
        "消込ID": get_column("消込ID"),
        "消込日": settlement_dates,
        "得意先名": get_column("得意先名"),
        "コード": get_column("コード"),
        "消込額": get_column("消込額", "消込予定"),
        "消込前残高": get_column("消込前残高", "元残高", "残高"),
        "消込後残高": get_column("消込後残高"),
        "仕訳登録済": get_column("仕訳登録済")
    })[HISTORY_COLUMNS]


def load_receivable_history():

    history_path = "data/receivables/receivable_history.csv"

    if not os.path.exists(history_path):
        return pd.DataFrame(columns=HISTORY_COLUMNS)

    history_df = pd.read_csv(
        history_path,
        dtype=str
    ).fillna("")

    return normalize_receivable_history(history_df)


def save_receivable_history(history_rows):

    history_path = "data/receivables/receivable_history.csv"

    old_df = load_receivable_history()
    new_df = normalize_receivable_history(
        pd.DataFrame(history_rows)
    )

    if not new_df.empty:
        empty_dates = new_df["消込日"] == ""
        new_df.loc[
            empty_dates,
            "消込日"
        ] = datetime.now().strftime("%Y/%m/%d")

    save_df = pd.concat(
        [old_df, new_df],
        ignore_index=True
    )

    save_df.to_csv(
        history_path,
        index=False,
        encoding="utf-8-sig"
    )


def is_receivable_journal_registered(settlement_id):

    history_df = load_receivable_history()

    target_df = history_df[
        history_df["消込ID"] == settlement_id
    ]

    if target_df.empty:
        raise ValueError("消込履歴を確認できません")

    if (target_df["仕訳登録済"] == "1").any():
        return True

    transaction_path = "data/transactions.csv"

    if os.path.exists(transaction_path):

        transaction_df = pd.read_csv(
            transaction_path,
            dtype=str,
            usecols=["証番号"]
        ).fillna("")

        if (
            transaction_df["証番号"] == settlement_id
        ).any():
            mark_receivable_journal_registered(
                settlement_id
            )
            return True

    return False


def mark_receivable_journal_registered(settlement_id):

    history_df = load_receivable_history()

    target_mask = (
        history_df["消込ID"] == settlement_id
    )

    if not target_mask.any():
        raise ValueError("消込履歴を確認できません")

    if (
        history_df.loc[
            target_mask,
            "仕訳登録済"
        ] == "1"
    ).any():
        raise ValueError("この消込仕訳は登録済みです")

    history_df.loc[
        target_mask,
        "仕訳登録済"
    ] = "1"

    history_df.to_csv(
        "data/receivables/receivable_history.csv",
        index=False,
        encoding="utf-8-sig"
    )

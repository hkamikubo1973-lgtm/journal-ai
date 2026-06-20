import pandas as pd
import shutil
import os
import uuid

from datetime import datetime

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

        defaults = {
            "未収科目": "売掛金",
            "未収補助": "",
            "部門": ""
        }

        for column, default_value in defaults.items():
            if column not in df.columns:
                df[column] = default_value

        return df

    except Exception as e:

        print("未収CSV読込エラー:", e)

        return pd.DataFrame(
            columns=[
                "コード",
                "得意先名",
                "請求金額",
                "残高",
                "ステータス",
                "未収科目",
                "未収補助",
                "部門"
            ]
        )

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

    cleanup_logs()

    print("未収CSV取込完了")

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
    settlement_date=None
):

    receivables_df = load_receivables()
    history_rows = []
    settlement_id = uuid.uuid4().hex

    if settlement_date is None:
        settlement_date = datetime.now()

    if hasattr(settlement_date, "strftime"):
        settlement_date = settlement_date.strftime("%Y/%m/%d")
    else:
        settlement_date = str(settlement_date).replace("-", "/")

    for candidate in candidates:

        code = str(candidate["コード"])
        scheduled_amount = int(candidate["消込予定"])

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

    receivables_df.to_csv(
        "data/receivables/current.csv",
        index=False,
        encoding="utf-8-sig"
    )

    save_receivable_history(history_rows)

    return settlement_id

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

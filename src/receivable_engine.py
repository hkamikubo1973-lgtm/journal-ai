import pandas as pd
import shutil
import os

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

        return df

    except Exception as e:

        print("未収CSV読込エラー:", e)

        return pd.DataFrame(
            columns=[
                "コード",
                "得意先名",
                "請求金額",
                "残高",
                "ステータス"
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
# 消込履歴保存
# =========================================
def save_receivable_history(matched_result):

    try:

        import os
        from datetime import datetime

        history_path = "data/receivables/receivable_history.csv"

        history_rows = []

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for item in matched_result:

            row = {
                "日時": now,
                "コード": item.get("コード", ""),
                "得意先名": item.get("得意先名", ""),
                "消込額": item.get("消込額", ""),
                "元残高": item.get("元残高", ""),
                "消込後残高": item.get("消込後残高", ""),
                "状態": item.get("状態", "")
            }

            history_rows.append(row)

        new_df = pd.DataFrame(history_rows)

        # 履歴CSV存在確認
        if os.path.exists(history_path):

            old_df = pd.read_csv(
                history_path,
                dtype=str
            ).fillna("")

            save_df = pd.concat(
                [old_df, new_df],
                ignore_index=True
            )

        else:

            save_df = new_df

        save_df.to_csv(
            history_path,
            index=False,
            encoding="utf-8-sig"
        )

        print("消込履歴保存完了")

    except Exception as e:

        print("履歴保存エラー:", e)
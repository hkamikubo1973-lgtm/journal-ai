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

    print("未収CSV取込完了")
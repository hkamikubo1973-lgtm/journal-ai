"""Streamlit-compatible historical CSV import; no master updates or row sorting."""

import io
import pandas as pd
from columns import COL_DATE, COL_DEBIT, COL_CREDIT, COL_DEBIT_AMOUNT, COL_CREDIT_AMOUNT, COL_SUMMARY

TRANSACTIONS_PATH = 'data/transactions.csv'


def read_past_journal_csv(content):

    last_error = None

    for encoding in [
        "utf-8-sig",
        "cp932",
    ]:
        try:
            df = pd.read_csv(
                io.BytesIO(content),
                dtype=str,
                encoding=encoding,
            ).fillna("")
            df.columns = [
                str(column).strip()
                for column in df.columns
            ]
            return df, encoding, None
        except Exception as e:
            last_error = e

    return None, None, last_error


def load_transactions_df(path=TRANSACTIONS_PATH):

    return pd.read_csv(
        path,
        dtype=str,
        encoding="utf-8-sig",
    ).fillna("")


def normalize_import_value(value, amount=False, date=False):

    value = " ".join(
        str(value or "").split()
    )

    if date:
        value = value.replace("/", "").replace("-", "")

    if amount:
        value = value.replace(",", "")

    return value


def journal_import_key(row):

    amount = row.get(COL_DEBIT_AMOUNT, "")

    if not normalize_import_value(amount, amount=True):
        amount = row.get(COL_CREDIT_AMOUNT, "")

    return (
        normalize_import_value(row.get(COL_DATE, ""), date=True),
        normalize_import_value(row.get("借方科目", "")),
        normalize_import_value(row.get(COL_DEBIT, "")),
        normalize_import_value(row.get("貸方科目", "")),
        normalize_import_value(row.get(COL_CREDIT, "")),
        normalize_import_value(amount, amount=True),
        normalize_import_value(row.get(COL_SUMMARY, "")),
    )


def prepare_past_journal_import(upload_df, existing_df):

    existing_columns = list(existing_df.columns)

    if len(upload_df.columns) != 45:
        return None, f"45列CSVではありません（{len(upload_df.columns)}列）"

    if list(upload_df.columns) != existing_columns:
        return None, "CSVの列構造がtransactions.csvと一致しません"

    upload_df = upload_df[
        upload_df.apply(
            lambda row: any(
                str(value).strip()
                for value in row
            ),
            axis=1,
        )
    ]

    existing_keys = {
        journal_import_key(row)
        for _, row in existing_df.iterrows()
    }

    seen_import_keys = set()
    new_rows = []
    duplicate_count = 0

    for _, row in upload_df.iterrows():
        key = journal_import_key(row)

        if key in existing_keys or key in seen_import_keys:
            duplicate_count += 1
            continue

        seen_import_keys.add(key)
        new_rows.append(row.to_dict())

    new_df = pd.DataFrame(
        new_rows,
        columns=existing_columns,
    )

    return {
        "new_df": new_df,
        "duplicate_count": duplicate_count,
    }, None


def append_past_journals_to_transactions(new_df, path=TRANSACTIONS_PATH, *, existing_df=None):

    if new_df.empty:
        return 0

    if existing_df is None:
        existing_df = load_transactions_df(path)
    combined_df = pd.concat(
        [
            existing_df,
            new_df,
        ],
        ignore_index=True,
    )

    combined_df.to_csv(
        path,
        index=False,
        encoding="utf-8-sig",
    )

    return len(new_df)


PREVIEW_COLUMNS = [COL_DATE, "借方科目", COL_DEBIT, "貸方科目", COL_CREDIT,
                   COL_DEBIT_AMOUNT, COL_SUMMARY]


def import_past_journal_csv(content, path=TRANSACTIONS_PATH, *, execute=False):
    """Reparse upload and read the current DB on every call, including Execute.

    uploaded_count and preview describe the parsed upload before blank/duplicate
    filtering, matching the old UI. Only duplicate keys are normalized.
    """
    upload, encoding, read_error = read_past_journal_csv(content)
    result = {
        "detected_encoding": encoding, "column_count": 0, "uploaded_count": 0,
        "new_count": 0, "duplicate_count": 0, "preview_rows": [], "errors": [],
        "imported_count": 0,
    }
    if read_error is not None:
        result["errors"] = ["CSVを読み込めませんでした。文字コードとCSV形式を確認してください。"]
        return result
    result.update(column_count=len(upload.columns), uploaded_count=len(upload))
    # Missing DB is an error; never create a first-time database here.
    existing = load_transactions_df(path)
    prepared, error = prepare_past_journal_import(upload, existing)
    if error:
        result["errors"] = [error]
        return result
    result.update(
        new_count=len(prepared["new_df"]),
        duplicate_count=prepared["duplicate_count"],
        preview_rows=upload[[c for c in PREVIEW_COLUMNS if c in upload.columns]]
        .head(50).to_dict(orient="records"),
    )
    if execute:
        result["imported_count"] = append_past_journals_to_transactions(
            prepared["new_df"], path, existing_df=existing,
        )
    return result

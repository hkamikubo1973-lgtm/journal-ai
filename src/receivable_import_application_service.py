"""Expose the Streamlit billing import pipeline without changing engine rules."""

import io
import json
from pathlib import Path

import pandas as pd

from receivable_engine import (
    append_standard_receivables,
    convert_company_billing_excel,
    exclude_duplicate_receivables,
    normalize_standard_receivable_csv,
)


BILLING_DUPLICATE_COLUMNS = [
    "コード", "得意先名", "請求日", "請求金額", "未収科目", "未収補助",
]


def _prepare(file_bytes, invoice_date, payment_due_date, default_account, department, current_path):
    with pd.ExcelFile(io.BytesIO(file_bytes)) as workbook:
        sheet_name = "プリント用" if "プリント用" in workbook.sheet_names else workbook.sheet_names[0]
        raw = pd.read_excel(workbook, sheet_name=sheet_name, header=None, dtype=object)
    source, conversion_errors = convert_company_billing_excel(
        raw, invoice_date, payment_due_date, default_account, department,
    )
    normalized, validation_errors = normalize_standard_receivable_csv(source)
    valid, duplicates = exclude_duplicate_receivables(
        normalized, BILLING_DUPLICATE_COLUMNS, current_path=current_path,
    )
    errors = pd.concat([
        conversion_errors, validation_errors,
        duplicates.drop(columns=["未収ID"], errors="ignore"),
    ], ignore_index=True)
    exclusions = []
    # JSON conversion preserves the existing Excel行 / CSV行 labels and blanks.
    for row in json.loads(errors.to_json(orient="records", force_ascii=False)):
        label = "Excel行" if row.get("Excel行") is not None else "CSV行" if row.get("CSV行") is not None else None
        exclusions.append({
            "source_row": int(row[label]) if label else None,
            "source_row_label": label,
            "reason": row["エラー"],
            "values": row,
        })
    result = {
        "sheet_name": sheet_name,
        "valid_rows": json.loads(valid.drop(columns=["未収ID"], errors="ignore").to_json(orient="records", force_ascii=False)),
        "importable_count": len(valid),
        "excluded_count": len(errors),
        "duplicate_count": len(duplicates),
        "exclusions": exclusions,
    }
    return valid, result


def preview_receivable_import(
    file_bytes, *, invoice_date, default_account, department="", payment_due_date=None,
    receivables_directory=Path("data/receivables"),
):
    """No append; legacy load-time maintenance is intentionally retained."""
    _, result = _prepare(
        file_bytes, invoice_date, payment_due_date, default_account, department,
        Path(receivables_directory) / "current.csv",
    )
    return result


def execute_receivable_import(
    file_bytes, *, invoice_date, default_account, department="", payment_due_date=None,
    receivables_directory=Path("data/receivables"),
):
    current_path = Path(receivables_directory) / "current.csv"
    valid, result = _prepare(
        file_bytes, invoice_date, payment_due_date, default_account, department, current_path,
    )
    imported, duplicates = append_standard_receivables(
        valid, duplicate_columns=BILLING_DUPLICATE_COLUMNS, current_path=current_path,
    )
    duplicate_count = result["duplicate_count"] + duplicates
    message = f"未収一覧へ{imported}件取り込みました" if imported else "追加対象の未収明細はありません"
    if duplicate_count:
        message += f"（重複{duplicate_count}件を除外）"
    return {
        "imported_count": imported,
        "excluded_count": result["excluded_count"] + duplicates,
        "duplicate_count": duplicate_count,
        "message": message,
    }

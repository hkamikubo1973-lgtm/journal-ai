"""通常1行仕訳の登録予定データを、副作用なしで整形・検証する。"""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
import re
import unicodedata
from typing import Any


NO_SAVE_WARNING = "この画面ではまだ保存・DB登録は行っていません。"
COMPLEX_JOURNAL_ERROR = (
    "この候補は資金複合または諸口を含むため、"
    "Phase 3-1の通常1行仕訳登録準備では未対応です。"
)
EDIT_FORM_FIELDS = (
    "voucher_date",
    "voucher_no",
    "voucher_summary",
    "debit_account_code",
    "debit_account_name",
    "debit_sub_code",
    "debit_sub_name",
    "debit_dept_code",
    "debit_dept_name",
    "credit_account_code",
    "credit_account_name",
    "credit_sub_code",
    "credit_sub_name",
    "credit_dept_code",
    "credit_dept_name",
    "amount",
    "summary",
    "source_debit_amount",
    "source_credit_amount",
)
COMPLEX_FLAGS = (
    "has_fukugo",
    "has_sundry",
    "contains_fukugo_or_sundry",
    "show_block_rows",
    "is_complex",
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return unicodedata.normalize("NFKC", str(value)).strip()


def _normalize_date(value: Any) -> tuple[str | None, str | None]:
    date_text = _text(value)
    if not date_text:
        return None, "伝票日付を入力してください。"

    compact = date_text.replace("-", "")
    if not re.fullmatch(r"\d{8}", compact):
        return None, "伝票日付はYYYYMMDDまたはYYYY-MM-DD形式で入力してください。"
    try:
        datetime.strptime(compact, "%Y%m%d")
    except ValueError:
        return None, "伝票日付に正しい日付を入力してください。"
    return compact, None


def _normalize_amount(value: Any) -> tuple[int | None, str | None]:
    amount_text = _text(value)
    if not amount_text:
        return None, "金額を入力してください。"

    compact = re.sub(r"[,\s]", "", amount_text)
    if not re.fullmatch(r"[+-]?\d+", compact):
        return None, "金額は整数で入力してください。"
    amount = int(compact)
    if amount <= 0:
        return None, "金額は1円以上で入力してください。"
    return amount, None


def _blocked_response(errors: list[str]) -> dict[str, Any]:
    return {
        "ok": False,
        "blocked": True,
        "errors": errors,
        "warnings": [],
        "registration_id": None,
        "prepared_journal": None,
        "epson_preview_row": None,
    }


def prepare_registration(payload: dict) -> dict:
    """登録予定仕訳を検証・整形する。ファイルやDBへの書き込みは行わない。"""

    edit_form = payload.get("edit_form")
    candidate_meta = payload.get("candidate_meta")
    if not isinstance(edit_form, dict):
        return _blocked_response(["編集フォーム情報がありません。"])
    if not isinstance(candidate_meta, dict):
        return _blocked_response(["候補メタ情報がありません。"])

    errors: list[str] = []
    editable_row_count = candidate_meta.get("editable_row_count", 1)
    if editable_row_count != 1:
        if editable_row_count == 0:
            errors.append("編集対象行がありません。")
        else:
            errors.append("編集対象行が複数あるため、Phase 3-1では登録準備できません。")

    if any(bool(candidate_meta.get(flag, False)) for flag in COMPLEX_FLAGS):
        errors.append(COMPLEX_JOURNAL_ERROR)

    voucher_date, date_error = _normalize_date(edit_form.get("voucher_date"))
    if date_error:
        errors.append(date_error)

    amount, amount_error = _normalize_amount(edit_form.get("amount"))
    if amount_error:
        errors.append(amount_error)

    debit_account_code = _text(edit_form.get("debit_account_code"))
    credit_account_code = _text(edit_form.get("credit_account_code"))
    if not debit_account_code:
        errors.append("借方科目コードを入力してください。")
    if not credit_account_code:
        errors.append("貸方科目コードを入力してください。")

    if errors:
        return _blocked_response(errors)

    normalized = {field: _text(edit_form.get(field)) for field in EDIT_FORM_FIELDS}
    normalized["voucher_date"] = voucher_date
    normalized["debit_account_code"] = debit_account_code
    normalized["credit_account_code"] = credit_account_code
    normalized["amount"] = amount
    prepared_journal = normalized

    epson_preview_row = {
        "伝票日付": voucher_date,
        "伝票摘要": normalized["voucher_summary"],
        "借方部門": normalized["debit_dept_code"],
        "借方部門名": normalized["debit_dept_name"],
        "借方科目": debit_account_code,
        "借方科目名": normalized["debit_account_name"],
        "借方補助": normalized["debit_sub_code"],
        "借方補助科目名": normalized["debit_sub_name"],
        "借方金額": str(amount),
        "貸方部門": normalized["credit_dept_code"],
        "貸方部門名": normalized["credit_dept_name"],
        "貸方科目": credit_account_code,
        "貸方科目名": normalized["credit_account_name"],
        "貸方補助": normalized["credit_sub_code"],
        "貸方補助科目名": normalized["credit_sub_name"],
        "貸方金額": str(amount),
        "摘要": normalized["summary"],
        "証番号": normalized["voucher_no"],
    }

    id_material = {
        key: prepared_journal[key]
        for key in (
            "voucher_date",
            "debit_account_code",
            "debit_sub_code",
            "debit_dept_code",
            "credit_account_code",
            "credit_sub_code",
            "credit_dept_code",
            "amount",
            "summary",
        )
    }
    serialized = json.dumps(
        id_material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    registration_id = sha256(serialized.encode("utf-8")).hexdigest()

    return {
        "ok": True,
        "blocked": False,
        "errors": [],
        "warnings": [NO_SAVE_WARNING],
        "registration_id": registration_id,
        "prepared_journal": prepared_journal,
        "epson_preview_row": epson_preview_row,
    }

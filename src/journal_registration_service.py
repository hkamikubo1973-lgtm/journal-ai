"""通常1行仕訳の登録予定データを、副作用なしで整形・検証する。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from hashlib import sha256
import json
import re
import unicodedata
from typing import Any

from columns import EPSON_COLUMNS
from journal_master_service import load_journal_masters


NO_SAVE_WARNING = "この画面ではまだ保存・DB登録は行っていません。"
MASTER_LOAD_ERROR = "マスターデータを読み込めないため、登録準備できません。"
SUB_ACCOUNT_RELATION_LOAD_ERROR = (
    "補助科目親子関係マスターを確認できないため、"
    "補助付き仕訳を登録準備できません。"
)
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


def _blocked_response(
    errors: list[str],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "blocked": True,
        "errors": errors,
        "warnings": warnings or [],
        "registration_id": None,
        "prepared_journal": None,
        "epson_preview_row": None,
        "epson_base_row": None,
    }


def extract_epson_source_row(
    source_row: Any,
) -> tuple[dict[str, Any] | None, str | None]:
    """EPSON_COLUMNS順の45列だけを元行から非破壊で抽出する。"""

    if not isinstance(source_row, Mapping):
        return None, "正式EPSON生成元行がありません。"

    missing_columns = [
        column for column in EPSON_COLUMNS if column not in source_row
    ]
    if missing_columns:
        return None, (
            "正式EPSON生成元行に必要な45列が不足しています: "
            + "、".join(missing_columns)
        )

    return {
        column: source_row[column]
        for column in EPSON_COLUMNS
    }, None


def build_registration_id(
    prepared_journal: dict[str, Any],
    epson_base_row: dict[str, Any],
) -> str:
    """検証済み仕訳とEPSON_COLUMNS順の45列から決定的なIDを作る。"""

    id_material = [
        [
            "prepared_journal",
            [
                [field, prepared_journal[field]]
                for field in EDIT_FORM_FIELDS
            ],
        ],
        [
            "epson_base_row",
            [
                [column, epson_base_row[column]]
                for column in EPSON_COLUMNS
            ],
        ],
    ]
    serialized = json.dumps(
        id_material,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return sha256(serialized.encode("utf-8")).hexdigest()


def _items_by_code(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        code = _text(item.get("code"))
        if code:
            result.setdefault(code, []).append(item)
    return result


def _validate_account(
    side: str,
    code: str,
    name: str,
    accounts_by_code: dict[str, list[dict[str, Any]]],
    errors: list[str],
) -> None:
    if not code:
        errors.append(f"{side}科目コードを入力してください。")
        return

    matches = accounts_by_code.get(code, [])
    if not matches:
        errors.append(f"{side}科目コード {code} は科目マスターに存在しません。")
        return

    matching_name = next(
        (item for item in matches if _text(item.get("name")) == name),
        None,
    )
    if matching_name is None:
        master_names = "、".join(
            f"「{master_name}」"
            for master_name in sorted({_text(item.get("name")) for item in matches})
        )
        errors.append(
            f"{side}科目コード {code} のマスター名称{master_names}と"
            f"フォーム名称「{name}」が一致しません。"
        )
        return

    if not bool(matching_name.get("selectable", False)):
        errors.append(f"{side}科目コード {code} は通常仕訳で直接選択できません。")


def _validate_optional_master_pair(
    *,
    side: str,
    item_label: str,
    code: str,
    name: str,
    items_by_code: dict[str, list[dict[str, Any]]],
    errors: list[str],
) -> list[dict[str, Any]]:
    if not code and not name:
        return []
    if code and not name:
        errors.append(f"{side}{item_label}コードが入力されていますが、{item_label}名が空です。")
        return []
    if name and not code:
        errors.append(f"{side}{item_label}名が入力されていますが、{item_label}コードが空です。")
        return []

    matches = items_by_code.get(code, [])
    if not matches:
        errors.append(f"{side}{item_label}コード {code} は{item_label}マスターに存在しません。")
        return []

    names = sorted({_text(item.get("name")) for item in matches})
    if name not in names:
        errors.append(
            f"{side}{item_label}コード {code} の{item_label}マスター名称候補に"
            f"「{name}」が存在しません。"
        )
    return matches


def _sub_account_relations_by_key(
    masters: dict[str, Any],
) -> tuple[dict[tuple[str, str], dict[str, Any]], bool]:
    relations = masters.get("sub_account_relations")
    diagnostics = masters.get("diagnostics")
    if not isinstance(relations, list) or not isinstance(diagnostics, dict):
        return {}, False
    if (
        diagnostics.get("duplicate_sub_account_relation_keys")
        or diagnostics.get("invalid_sub_account_relation_rows")
    ):
        return {}, False

    relations_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for relation in relations:
        if not isinstance(relation, dict):
            return {}, False
        account_code = _text(relation.get("account_code"))
        sub_code = _text(relation.get("sub_code"))
        sub_name = relation.get("sub_name")
        if not account_code or not sub_code or not _text(sub_name):
            return {}, False
        key = (account_code, sub_code)
        if key in relations_by_key:
            return {}, False
        relations_by_key[key] = relation

    return relations_by_key, bool(relations_by_key)


def _validate_sub_account(
    *,
    side: str,
    account_code: str,
    sub_code: str,
    sub_name: str,
    relations_by_key: dict[tuple[str, str], dict[str, Any]],
    relations_usable: bool,
    errors: list[str],
    warnings: list[str],
    normalized_sub_names: dict[str, str],
    prefix: str,
) -> None:
    if not sub_code and not sub_name:
        return
    if sub_code and not sub_name:
        errors.append(f"{side}補助コードが入力されていますが、補助名が空です。")
        return
    if sub_name and not sub_code:
        errors.append(f"{side}補助名が入力されていますが、補助コードが空です。")
        return
    if not relations_usable:
        if SUB_ACCOUNT_RELATION_LOAD_ERROR not in errors:
            errors.append(SUB_ACCOUNT_RELATION_LOAD_ERROR)
        return

    relation = relations_by_key.get((account_code, sub_code))
    if relation is None:
        errors.append(
            f"{side}科目 {account_code} では補助コード {sub_code} は使用できません。"
        )
        return

    current_name = str(relation["sub_name"])
    normalized_sub_names[f"{prefix}_sub_name"] = current_name
    if _text(current_name) != sub_name:
        warnings.append(
            f"{side}補助名称を現在のマスターに合わせて"
            f"「{sub_name}」から「{current_name}」へ更新しました。"
        )


def _validate_masters(
    edit_form: dict[str, Any],
) -> tuple[list[str], list[str], dict[str, str]]:
    try:
        masters = load_journal_masters()
    except Exception:
        return [MASTER_LOAD_ERROR], [], {}

    errors: list[str] = []
    warnings: list[str] = []
    normalized_sub_names: dict[str, str] = {}
    accounts_by_code = _items_by_code(masters.get("accounts", []))
    departments_by_code = _items_by_code(masters.get("departments", []))
    relations_by_key, relations_usable = _sub_account_relations_by_key(masters)

    for side, prefix in (("借方", "debit"), ("貸方", "credit")):
        _validate_account(
            side,
            _text(edit_form.get(f"{prefix}_account_code")),
            _text(edit_form.get(f"{prefix}_account_name")),
            accounts_by_code,
            errors,
        )

        sub_code = _text(edit_form.get(f"{prefix}_sub_code"))
        sub_name = _text(edit_form.get(f"{prefix}_sub_name"))
        _validate_sub_account(
            side=side,
            account_code=_text(edit_form.get(f"{prefix}_account_code")),
            sub_code=sub_code,
            sub_name=sub_name,
            relations_by_key=relations_by_key,
            relations_usable=relations_usable,
            errors=errors,
            warnings=warnings,
            normalized_sub_names=normalized_sub_names,
            prefix=prefix,
        )

        _validate_optional_master_pair(
            side=side,
            item_label="部門",
            code=_text(edit_form.get(f"{prefix}_dept_code")),
            name=_text(edit_form.get(f"{prefix}_dept_name")),
            items_by_code=departments_by_code,
            errors=errors,
        )

    return errors, warnings, normalized_sub_names


def prepare_registration(payload: dict) -> dict:
    """登録予定仕訳を検証・整形する。ファイルやDBへの書き込みは行わない。"""

    edit_form = payload.get("edit_form")
    candidate_meta = payload.get("candidate_meta")
    source_row = payload.get("source_row")
    if not isinstance(edit_form, dict):
        return _blocked_response(["編集フォーム情報がありません。"])
    if not isinstance(candidate_meta, dict):
        return _blocked_response(["候補メタ情報がありません。"])

    errors: list[str] = []
    warnings: list[str] = []
    epson_base_row, source_row_error = extract_epson_source_row(source_row)
    if source_row_error:
        errors.append(source_row_error)

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
    master_errors, master_warnings, normalized_sub_names = _validate_masters(
        edit_form
    )
    errors.extend(master_errors)
    warnings.extend(master_warnings)

    if errors:
        return _blocked_response(errors, warnings)

    normalized = {field: _text(edit_form.get(field)) for field in EDIT_FORM_FIELDS}
    normalized.update(normalized_sub_names)
    normalized["voucher_date"] = voucher_date
    normalized["debit_account_code"] = debit_account_code
    normalized["credit_account_code"] = credit_account_code
    normalized["amount"] = amount
    prepared_journal = normalized

    epson_edit_values = {
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
    epson_preview_row = dict(epson_edit_values)

    if epson_base_row is None:
        return _blocked_response(
            ["正式EPSON生成元行を準備できませんでした。"],
            warnings,
        )

    epson_base_row.update(epson_edit_values)
    registration_id = build_registration_id(
        prepared_journal,
        epson_base_row,
    )

    return {
        "ok": True,
        "blocked": False,
        "errors": [],
        "warnings": [NO_SAVE_WARNING, *warnings],
        "registration_id": registration_id,
        "prepared_journal": prepared_journal,
        "epson_preview_row": epson_preview_row,
        "epson_base_row": epson_base_row,
    }

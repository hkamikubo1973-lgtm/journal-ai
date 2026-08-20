"""登録予定からEPSON CSVを副作用なしで生成する。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from hmac import compare_digest
from typing import Any

from epson_export_service import build_epson_csv_bytes, build_epson_rows
from journal_master_service import load_journal_masters
from journal_registration_service import (
    EDIT_FORM_FIELDS,
    build_registration_id,
    extract_epson_source_row,
)
from system_settings import load_system_settings


class EpsonExportValidationError(ValueError):
    """CSVを一部出力せず、request全体を中止する検証エラー。"""


@dataclass(frozen=True)
class EpsonCsvExport:
    content: bytes
    filename: str
    epson_base_rows: tuple[dict[str, Any], ...] = ()


def _validated_prepared_journal(
    value: Any,
    item_number: int,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EpsonExportValidationError(
            f"{item_number}件目のprepared_journalがありません。"
        )

    missing_fields = [
        field for field in EDIT_FORM_FIELDS if field not in value
    ]
    if missing_fields:
        raise EpsonExportValidationError(
            f"{item_number}件目のprepared_journalに必要な項目が不足しています: "
            + "、".join(missing_fields)
        )

    return {
        field: value[field]
        for field in EDIT_FORM_FIELDS
    }


def validate_epson_export_items(
    items: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """全itemの45列とregistration_idを検証し、カート順で返す。"""

    if not items:
        raise EpsonExportValidationError(
            "EPSON CSVの出力対象がありません。"
        )

    validated_rows: list[dict[str, Any]] = []
    for item_number, item in enumerate(items, start=1):
        if not isinstance(item, Mapping):
            raise EpsonExportValidationError(
                f"{item_number}件目の登録予定が不正です。"
            )

        prepared_journal = _validated_prepared_journal(
            item.get("prepared_journal"),
            item_number,
        )
        epson_base_row, source_error = extract_epson_source_row(
            item.get("epson_base_row")
        )
        if source_error or epson_base_row is None:
            raise EpsonExportValidationError(
                f"{item_number}件目: {source_error or 'EPSON45列を確認できません。'}"
            )

        registration_id = item.get("registration_id")
        if not isinstance(registration_id, str) or not registration_id:
            raise EpsonExportValidationError(
                f"{item_number}件目のregistration_idがありません。"
            )

        expected_id = build_registration_id(
            prepared_journal,
            epson_base_row,
        )
        if not compare_digest(registration_id, expected_id):
            raise EpsonExportValidationError(
                f"{item_number}件目のregistration_idが内容と一致しません。"
            )

        validated_rows.append(epson_base_row)

    return validated_rows


def _load_export_context() -> tuple[dict[str, str], dict[str, str], str]:
    masters = load_journal_masters()
    account_master = {
        str(item.get("name", "")).strip(): str(item.get("code", "")).strip()
        for item in masters.get("accounts", [])
        if str(item.get("name", "")).strip()
    }
    sub_master = {
        str(item.get("name", "")): str(item.get("code", ""))
        for item in masters.get("sub_accounts", [])
        if str(item.get("name", ""))
    }
    company_name = str(
        load_system_settings().get("company_name", "")
    ).strip()
    return account_master, sub_master, company_name


def export_epson_csv(
    items: Sequence[Mapping[str, Any]],
    *,
    account_master: Mapping[str, Any] | None = None,
    sub_master: Mapping[str, Any] | None = None,
    company_name: str | None = None,
    export_datetime: datetime | None = None,
    machine_name: str | None = None,
    user_name: str | None = None,
    input_date: str | None = None,
) -> EpsonCsvExport:
    """登録予定を全件検証し、保存やDB更新をせずCSV bytesを返す。"""

    epson_base_rows = validate_epson_export_items(items)

    if account_master is None or sub_master is None or company_name is None:
        loaded_accounts, loaded_subs, loaded_company = _load_export_context()
        if account_master is None:
            account_master = loaded_accounts
        if sub_master is None:
            sub_master = loaded_subs
        if company_name is None:
            company_name = loaded_company

    epson_rows = build_epson_rows(
        epson_base_rows,
        company_name,
        account_master,
        sub_master,
        {},
        machine_name=machine_name,
        user_name=user_name,
        input_date=input_date,
    )
    csv_bytes = build_epson_csv_bytes(epson_rows)
    current_datetime = export_datetime or datetime.now()
    filename = (
        "epson_output_"
        f"{current_datetime.strftime('%Y%m%d_%H%M')}.csv"
    )
    return EpsonCsvExport(
        content=csv_bytes,
        filename=filename,
        epson_base_rows=tuple(epson_base_rows),
    )

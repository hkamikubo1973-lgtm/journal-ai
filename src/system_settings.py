"""既存のローカルJSON方式でシステム設定を読み書きする。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fiscal_year import validate_fiscal_year_start_month


SETTINGS_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.json"
DEFAULT_FISCAL_YEAR_START_MONTH = 2


def _fiscal_year_start_month(value: Any) -> int:
    if isinstance(value, bool):
        return DEFAULT_FISCAL_YEAR_START_MONTH
    if isinstance(value, int):
        month = value
    elif isinstance(value, str) and value.strip().isdigit():
        month = int(value.strip())
    else:
        return DEFAULT_FISCAL_YEAR_START_MONTH

    try:
        return validate_fiscal_year_start_month(month)
    except ValueError:
        return DEFAULT_FISCAL_YEAR_START_MONTH


def load_system_settings() -> dict[str, Any]:
    """ローカル設定を読み、未設定・不正な開始月には正式既定値2を使う。"""

    try:
        with SETTINGS_PATH.open("r", encoding="utf-8") as file:
            settings = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        settings = {}

    if not isinstance(settings, dict):
        settings = {}

    return {
        "company_name": str(settings.get("company_name", "") or ""),
        "csv_export_dir": str(settings.get("csv_export_dir", "") or ""),
        "fiscal_year_start_month": _fiscal_year_start_month(
            settings.get("fiscal_year_start_month")
        ),
    }


def save_system_settings(
    company_name: Any,
    csv_export_dir: Any,
    fiscal_year_start_month: Any = DEFAULT_FISCAL_YEAR_START_MONTH,
) -> tuple[bool, str]:
    """既存設定ファイルへ全システム設定を保存する。"""

    if isinstance(fiscal_year_start_month, bool):
        return False, "会計年度開始月は1～12で指定してください"
    if isinstance(fiscal_year_start_month, int):
        start_month = fiscal_year_start_month
    elif (
        isinstance(fiscal_year_start_month, str)
        and fiscal_year_start_month.strip().isdigit()
    ):
        start_month = int(fiscal_year_start_month.strip())
    else:
        return False, "会計年度開始月は1～12で指定してください"

    try:
        start_month = validate_fiscal_year_start_month(start_month)
    except ValueError:
        return False, "会計年度開始月は1～12で指定してください"

    settings = {
        "company_name": str(company_name or ""),
        "csv_export_dir": str(csv_export_dir or ""),
        "fiscal_year_start_month": start_month,
    }

    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with SETTINGS_PATH.open("w", encoding="utf-8") as file:
            json.dump(settings, file, ensure_ascii=False, indent=2)
    except OSError as error:
        return False, f"システム設定を保存できませんでした: {error}"

    return True, ""

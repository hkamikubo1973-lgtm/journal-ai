"""既存のローカルJSON方式でシステム設定を読み書きする。"""

from __future__ import annotations

import json
import ntpath
import os
from pathlib import Path
import tempfile
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


class OutputFolderSettingsError(ValueError):
    """The output folder is invalid or its settings cannot be updated."""


class OutputFolderPersistenceError(OutputFolderSettingsError):
    """The existing settings file could not be read or written."""


def save_output_folder(value: Any) -> str:
    """Update only the existing csv_export_dir setting.

    The base folder is checked for existence by the existing export services at
    save time, just as it is for the Streamlit setting.
    """
    if not isinstance(value, str):
        raise OutputFolderSettingsError("保存先フォルダを入力してください。")
    folder = value.strip()
    if not folder or any(ord(char) < 32 for char in folder):
        raise OutputFolderSettingsError("保存先フォルダを確認してください。")
    if os.name == "nt":
        drive, tail = ntpath.splitdrive(folder)
        if any(char in '<>"|?*' for char in folder) or ":" in tail:
            raise OutputFolderSettingsError("保存先フォルダを確認してください。")

    try:
        with SETTINGS_PATH.open("r", encoding="utf-8") as source:
            settings = json.load(source)
    except FileNotFoundError:
        settings = {}
    except (OSError, json.JSONDecodeError) as error:
        raise OutputFolderPersistenceError("設定を読み込めませんでした。") from error
    if not isinstance(settings, dict):
        raise OutputFolderPersistenceError("設定を読み込めませんでした。")

    settings["csv_export_dir"] = folder
    temporary_path: Path | None = None
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=SETTINGS_PATH.parent,
            prefix=".settings-", suffix=".tmp", delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(settings, temporary, ensure_ascii=False, indent=2)
        os.replace(temporary_path, SETTINGS_PATH)
    except OSError as error:
        raise OutputFolderPersistenceError("保存先フォルダの設定を保存できませんでした。") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return folder

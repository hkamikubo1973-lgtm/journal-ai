"""設定済み基準フォルダ配下へ出力ファイルを保存する共有処理。"""

from __future__ import annotations

import os
from pathlib import Path


class ExportFileError(OSError):
    """保存先検証・フォルダ作成・bytes書込みの失敗。"""


def resolve_export_target_dir(
    export_dir: str,
    subdir_name: str | None = None,
) -> Path:
    base_dir = str(export_dir or "").strip()
    if not base_dir:
        raise ExportFileError("CSV保存先フォルダを入力してください")

    base_path = Path(base_dir)
    if not base_path.is_dir():
        raise ExportFileError("CSV保存先フォルダが存在しません")
    if not subdir_name:
        return base_path

    output_path = base_path / subdir_name
    try:
        output_path.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ExportFileError(
            f"保存先フォルダを作成できませんでした: {error}"
        ) from error
    return output_path


def save_csv_bytes_to_export_dir(
    csv_bytes: bytes,
    filename: str,
    export_dir: str,
    subdir_name: str | None = None,
) -> Path:
    target_dir = resolve_export_target_dir(export_dir, subdir_name)
    save_path = target_dir / os.path.basename(filename)
    try:
        with save_path.open("wb") as file:
            file.write(csv_bytes)
    except OSError as error:
        raise ExportFileError(f"CSVを保存できませんでした: {error}") from error

    try:
        saved_size = save_path.stat().st_size
    except OSError as error:
        raise ExportFileError(f"CSVの保存結果を確認できませんでした: {error}") from error
    if saved_size != len(csv_bytes):
        raise ExportFileError("CSVの保存サイズが生成内容と一致しません")
    return save_path


def ensure_output_subdir(base_dir, subdir_name):
    try:
        return True, str(resolve_export_target_dir(base_dir, subdir_name))
    except ExportFileError as error:
        return False, str(error)


def get_export_target_dir(export_dir, subdir_name=None):
    try:
        return True, str(resolve_export_target_dir(export_dir, subdir_name))
    except ExportFileError as error:
        return False, str(error)


def save_csv_to_export_dir(
    csv_bytes,
    filename,
    export_dir,
    subdir_name=None,
):
    try:
        save_path = save_csv_bytes_to_export_dir(
            csv_bytes,
            filename,
            export_dir,
            subdir_name,
        )
    except ExportFileError as error:
        return False, str(error)
    return True, f"保存しました：{save_path}"

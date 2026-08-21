"""入力用Excelを設定済み保存先へ保存する。DB更新は行わない。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from export_file_service import ExportFileError, save_csv_bytes_to_export_dir
from input_excel_service import InputExcelExport, export_input_excel
from system_settings import load_system_settings


INPUT_EXCEL_EXPORT_SUBDIR = "02_入力用Excel"


class InputExcelSaveError(RuntimeError):
    """入力用Excelの生成または保存に失敗したエラー。"""


@dataclass(frozen=True)
class InputExcelSaveResult:
    success: bool
    filename: str
    saved_path: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def save_input_excel(
    items: Sequence[Mapping[str, Any]],
    *,
    export_dir: str | None = None,
    export_datetime: datetime | None = None,
    export_builder: Callable[..., InputExcelExport] | None = None,
    file_saver: Callable[..., Path] | None = None,
) -> InputExcelSaveResult:
    """同じ生成serviceのxlsx bytesを02_入力用Excelへ保存する。"""

    builder = export_builder or export_input_excel
    saver = file_saver or save_csv_bytes_to_export_dir
    generated = builder(items, export_datetime=export_datetime)

    if export_dir is None:
        export_dir = str(
            load_system_settings().get("csv_export_dir", "")
        ).strip()

    try:
        save_path = saver(
            generated.content,
            generated.filename,
            export_dir,
            INPUT_EXCEL_EXPORT_SUBDIR,
        )
    except (ExportFileError, OSError) as error:
        raise InputExcelSaveError(
            "入力用Excelの保存に失敗しました。"
            f"検索DBは更新していません：{error}"
        ) from error

    return InputExcelSaveResult(
        success=True,
        filename=generated.filename,
        saved_path=str(save_path),
        message="入力用Excelを保存しました。検索DBは更新していません。",
    )

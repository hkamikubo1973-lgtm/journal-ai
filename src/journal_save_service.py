"""EPSON CSV正式保存後に検索DBへ確定登録する処理を直列化する。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from threading import Lock
from typing import Any, Callable

from export_file_service import ExportFileError, save_csv_bytes_to_export_dir
from journal_export_service import EpsonCsvExport, export_epson_csv
from journal_persistence_service import (
    TRANSACTIONS_PATH,
    is_normal_journal_batch_in_transactions,
    register_epson_rows_to_search_db,
)
from system_settings import load_system_settings


EPSON_EXPORT_SUBDIR = "01_エプソン取込CSV"
_SAVE_LOCK = Lock()


class EpsonSaveError(RuntimeError):
    """CSV保存前後で停止し、検索DBを更新していないエラー。"""


@dataclass(frozen=True)
class EpsonSaveResult:
    ok: bool
    csv_saved: bool
    db_registered: bool
    already_registered: bool
    partial_failure: bool
    filename: str
    save_path: str
    appended_count: int
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def save_and_register_epson_csv(
    items: Sequence[Mapping[str, Any]],
    *,
    export_dir: str | None = None,
    transactions_path: str | Path = TRANSACTIONS_PATH,
    today: date | None = None,
    start_month: int | None = None,
    export_builder: Callable[..., EpsonCsvExport] | None = None,
    csv_saver: Callable[..., Path] | None = None,
    duplicate_checker: Callable[..., bool] | None = None,
    db_registrar: Callable[..., tuple[bool, int | str]] | None = None,
) -> EpsonSaveResult:
    """全件検証・CSV保存成功後にだけ、base rowを検索DBへ登録する。"""

    builder = export_builder or export_epson_csv
    saver = csv_saver or save_csv_bytes_to_export_dir
    checker = duplicate_checker or is_normal_journal_batch_in_transactions
    registrar = db_registrar or register_epson_rows_to_search_db

    generated = builder(items)
    epson_base_rows = [dict(row) for row in generated.epson_base_rows]
    if not epson_base_rows:
        raise EpsonSaveError(
            "EPSON CSVの検証済み45列行を取得できません。検索DBは更新していません。"
        )

    if export_dir is None:
        export_dir = str(
            load_system_settings().get("csv_export_dir", "")
        ).strip()

    with _SAVE_LOCK:
        try:
            save_path = saver(
                generated.content,
                generated.filename,
                export_dir,
                EPSON_EXPORT_SUBDIR,
            )
        except ExportFileError as error:
            raise EpsonSaveError(
                "EPSON CSVの保存に失敗しました。"
                f"検索DBは更新していません：{error}"
            ) from error
        except OSError as error:
            raise EpsonSaveError(
                "EPSON CSVの保存に失敗しました。"
                f"検索DBは更新していません：{error}"
            ) from error

        try:
            already_registered = checker(
                epson_base_rows,
                transactions_path=transactions_path,
            )
        except Exception:
            # 現行Streamlitと同じく、重複確認不能時は登録処理側で判定する。
            already_registered = False

        if already_registered:
            return EpsonSaveResult(
                ok=True,
                csv_saved=True,
                db_registered=False,
                already_registered=True,
                partial_failure=False,
                filename=generated.filename,
                save_path=str(save_path),
                appended_count=0,
                message=(
                    "EPSON CSVを保存しました。"
                    "検索DBには登録済みのため再登録していません。"
                ),
            )

        try:
            registered, register_result = registrar(
                epson_base_rows,
                transactions_path=transactions_path,
                today=today,
                start_month=start_month,
            )
        except Exception as error:
            registered, register_result = False, str(error)
        if not registered:
            return EpsonSaveResult(
                ok=False,
                csv_saved=True,
                db_registered=False,
                already_registered=False,
                partial_failure=True,
                filename=generated.filename,
                save_path=str(save_path),
                appended_count=0,
                message=(
                    "EPSON CSVは保存しましたが、検索DB登録に失敗しました："
                    f"{register_result}"
                ),
            )

        return EpsonSaveResult(
            ok=True,
            csv_saved=True,
            db_registered=True,
            already_registered=False,
            partial_failure=False,
            filename=generated.filename,
            save_path=str(save_path),
            appended_count=int(register_result),
            message="EPSON CSVを保存しました。検索DBへ登録しました。",
        )

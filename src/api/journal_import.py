"""Historical EPSON CSV upload endpoints, independent of Streamlit."""

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from past_journal_import_service import import_past_journal_csv

router = APIRouter(prefix="/api/journal/import")


def get_transactions_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "transactions.csv"


async def _import(file: UploadFile, path: Path, *, execute: bool):
    try:
        return import_past_journal_csv(await file.read(), path, execute=execute)
    except FileNotFoundError as error:
        raise HTTPException(400, "既存の検索DBがありません。") from error
    except Exception as error:
        raise HTTPException(500, "検索DBの取込処理に失敗しました。DBを確認して再試行してください。") from error


@router.post("/preview")
async def preview(file: UploadFile = File(...), path: Path = Depends(get_transactions_path)):
    return await _import(file, path, execute=False)


@router.post("/execute")
async def execute(file: UploadFile = File(...), path: Path = Depends(get_transactions_path)):
    return await _import(file, path, execute=True)

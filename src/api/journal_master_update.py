from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from journal_master_update_service import MasterUpdateError, add_account, update_masters

router = APIRouter(prefix="/api/journal/masters")


def get_master_directory():
    return Path(__file__).resolve().parents[2] / "data"


class UpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["account", "department", "sub"]


class AddAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    name: str
    category: Literal["資産", "負債", "純資産", "収益", "費用"]
    add_to_payment: bool = False


def _run(action):
    try:
        return action()
    except MasterUpdateError as error:
        raise HTTPException(422, str(error)) from error
    except Exception as error:
        raise HTTPException(500, "マスター処理に失敗しました。ファイルを確認してください。") from error


@router.post("/update-preview")
def preview(request: UpdateRequest, directory: Path = Depends(get_master_directory)):
    return _run(lambda: update_masters(directory, request.kind))


@router.post("/update")
def execute(request: UpdateRequest, directory: Path = Depends(get_master_directory)):
    return _run(lambda: update_masters(directory, request.kind, execute=True))


@router.post("/accounts/add")
def add(request: AddAccountRequest, directory: Path = Depends(get_master_directory)):
    return _run(lambda: add_account(directory, request.code, request.name, request.category,
                                   add_to_payment=request.add_to_payment))

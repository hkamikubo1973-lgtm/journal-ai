from typing import Any
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from api.receivable import get_receivables_directory, get_receivable_account_master_snapshot
from receivable_cart_service import edit_cart_item

router = APIRouter(prefix="/api/journal")


class CartEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item: dict[str, Any]
    edits: dict[str, Any]


@router.post("/receivable-cart/edit")
def edit(request: CartEditRequest, directory: Path = Depends(get_receivables_directory),
         masters: dict = Depends(get_receivable_account_master_snapshot)):
    try:
        return edit_cart_item(request.item, request.edits, directory, masters)
    except Exception as error:
        raise HTTPException(422, "未収仕訳を更新できません。科目・補助・部門・金額と元データを確認してください。") from error

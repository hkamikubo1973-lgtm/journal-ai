"""通常仕訳検索API。"""

from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from engine import load_data
from journal_search_service import search_journals


class JournalSearchRequest(BaseModel):
    keyword: str = ""
    department: Optional[str] = None
    amount: Optional[int] = Field(default=None, ge=1)
    limit: Literal[5, 10, 20] = 5


class JournalSearchQuery(BaseModel):
    keyword: str
    department: Optional[str]
    amount: Optional[int]
    limit: Literal[5, 10, 20]


class JournalSearchCandidate(BaseModel):
    rank: int
    score: int
    pattern_key: list[str]
    pattern_rank: Optional[int]
    search_reason: list[str]
    matched_amount_row: Optional[dict[str, Any]]
    source_rows: list[dict[str, Any]]
    editable_rows: list[dict[str, Any]]
    block_rows: list[dict[str, Any]]
    has_fukugo: bool
    has_sundry: bool
    contains_fukugo_or_sundry: bool
    show_block_rows: bool
    is_complex: bool


class JournalSearchResponse(BaseModel):
    query: JournalSearchQuery
    count: int
    candidates: list[JournalSearchCandidate]


app = FastAPI(title="journal-ai API")


@app.post(
    "/api/journal/search",
    response_model=JournalSearchResponse,
)
def post_journal_search(request: JournalSearchRequest):
    try:
        records, _, freq = load_data()
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="検索データを読み込めませんでした",
        ) from error

    try:
        return search_journals(
            records,
            freq,
            keyword=request.keyword,
            department=request.department,
            amount=request.amount,
            limit=request.limit,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="仕訳を検索できませんでした",
        ) from error

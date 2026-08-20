"""通常仕訳検索API。"""

from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from engine import load_data
from journal_master_service import load_journal_masters
from journal_registration_service import prepare_registration
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


class JournalEditFormRequest(BaseModel):
    voucher_date: str
    voucher_no: Optional[str] = None
    voucher_summary: Optional[str] = None
    debit_account_code: str
    debit_account_name: Optional[str] = None
    debit_sub_code: Optional[str] = None
    debit_sub_name: Optional[str] = None
    debit_dept_code: Optional[str] = None
    debit_dept_name: Optional[str] = None
    credit_account_code: str
    credit_account_name: Optional[str] = None
    credit_sub_code: Optional[str] = None
    credit_sub_name: Optional[str] = None
    credit_dept_code: Optional[str] = None
    credit_dept_name: Optional[str] = None
    amount: str
    summary: Optional[str] = None
    source_debit_amount: Optional[str] = None
    source_credit_amount: Optional[str] = None


class JournalCandidateMetaRequest(BaseModel):
    rank: Optional[int] = None
    score: Optional[int] = None
    pattern_key: list[str] = Field(default_factory=list)
    pattern_rank: Optional[int] = None
    editable_row_count: int = 1
    source_row_count: int = 0
    block_row_count: int = 0
    has_fukugo: bool = False
    has_sundry: bool = False
    contains_fukugo_or_sundry: bool = False
    show_block_rows: bool = False
    is_complex: bool = False


class PrepareRegistrationRequest(BaseModel):
    edit_form: JournalEditFormRequest
    candidate_meta: JournalCandidateMetaRequest
    source_row: dict[str, Any]


class PrepareRegistrationResponse(BaseModel):
    ok: bool
    blocked: bool = False
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    registration_id: Optional[str] = None
    prepared_journal: Optional[dict[str, Any]] = None
    epson_preview_row: Optional[dict[str, Any]] = None
    epson_base_row: Optional[dict[str, Any]] = None


class AccountMasterItem(BaseModel):
    code: str
    name: str
    category: str = ""
    label: str
    selectable: bool
    unselectable_reason: Optional[str] = None


class SubAccountMasterItem(BaseModel):
    code: str
    name: str
    label: str


class DepartmentMasterItem(BaseModel):
    code: str
    name: str
    label: str


class SubAccountRelationItem(BaseModel):
    account_code: str
    sub_code: str
    sub_name: str


class DuplicateAccountName(BaseModel):
    name: str
    codes: list[str] = Field(default_factory=list)


class DuplicateSubCode(BaseModel):
    code: str
    names: list[str] = Field(default_factory=list)


class DuplicateSubAccountRelationKey(BaseModel):
    account_code: str
    sub_code: str
    row_numbers: list[int] = Field(default_factory=list)


class InvalidSubAccountRelationRow(BaseModel):
    row_number: int
    reason: str


class JournalMasterDiagnostics(BaseModel):
    account_count: int
    selectable_account_count: int
    unselectable_account_count: int
    sub_account_count: int
    department_count: int
    sub_account_relation_count: int = 0
    duplicate_account_names: list[DuplicateAccountName] = Field(
        default_factory=list
    )
    duplicate_sub_codes: list[DuplicateSubCode] = Field(
        default_factory=list
    )
    duplicate_sub_account_relation_keys: list[
        DuplicateSubAccountRelationKey
    ] = Field(default_factory=list)
    invalid_sub_account_relation_rows: list[
        InvalidSubAccountRelationRow
    ] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class FiscalYearSystemInfo(BaseModel):
    fiscal_year_start_month: int
    fiscal_year_end_month: int
    current_fiscal_year: int
    current_fiscal_year_start: str
    current_fiscal_year_end: str
    retention_start_date: str
    keep_past_fiscal_years: int


class JournalMastersResponse(BaseModel):
    accounts: list[AccountMasterItem]
    sub_accounts: list[SubAccountMasterItem]
    departments: list[DepartmentMasterItem]
    sub_account_relations: list[SubAccountRelationItem] = Field(
        default_factory=list
    )
    diagnostics: JournalMasterDiagnostics
    system: FiscalYearSystemInfo


app = FastAPI(title="journal-ai API")


@app.get(
    "/api/journal/masters",
    response_model=JournalMastersResponse,
)
def get_journal_masters():
    try:
        return load_journal_masters()
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="マスターデータを読み込めませんでした",
        ) from error


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


@app.post(
    "/api/journal/prepare-registration",
    response_model=PrepareRegistrationResponse,
)
def post_prepare_registration(request: PrepareRegistrationRequest):
    payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
    return prepare_registration(payload)

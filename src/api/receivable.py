"""Read-only receivable query, options, and Preview API."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from journal_master_service import load_journal_masters
from receivable_account_validation_service import (
    ReceivableSettlementMasterValidationError,
)
from receivable_options_service import (
    PAYMENT_ACCOUNTS_PATH,
    load_receipt_account_options,
)
from receivable_persistence_service import (
    DEFAULT_RECEIVABLES_DIRECTORY,
    ReceivableLedgerConflictError,
    ReceivableLedgerLockTimeout,
    ReceivableLedgerMalformedError,
    ReceivableLedgerMissingError,
    ReceivableLedgerRecoveryRequired,
    ReceivableLedgerSchemaError,
    ReceivableLedgerSettlementUnavailableError,
    read_receivable_current_snapshot_when_ready,
)
from receivable_preview_application_service import (
    ReceivablePreviewCustomerNotFoundError,
    ReceivablePreviewValidationError,
    build_receivable_preview_application_result,
)
from receivable_query_service import (
    ReceivableCustomerNotFoundError,
    build_receivable_customer_detail,
    build_receivable_summary,
)


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/receivables",
    tags=["receivables"],
)

LedgerStatus = Literal[
    "ready",
    "recovery_pending",
    "recovery_required",
]
ReceivablePreviewMode = Literal["partial", "difference_account"]
ReceivablePreviewPattern = Literal[
    "exact_match",
    "partial_settlement",
    "shortage_difference",
    "overpayment",
]


class ReceivableCustomerSummaryItem(BaseModel):
    customer_name: str
    outstanding_count: int
    outstanding_balance: int


class ReceivableSummaryResponse(BaseModel):
    ledger_revision: str
    ledger_status: LedgerStatus
    settlement_available: bool
    customer_count: int
    outstanding_count: int
    outstanding_balance: int
    customers: list[ReceivableCustomerSummaryItem]


class ReceivableDetailItem(BaseModel):
    code: str
    receivable_id: str
    customer_name: str
    billing_date: str
    planned_payment_date: str
    receivable_account: str
    receivable_sub_account: str
    department: str
    summary: str
    billed_amount: int
    paid_amount: int
    balance: int
    status: str


class ReceivableCustomerDetailResponse(BaseModel):
    ledger_revision: str
    ledger_status: LedgerStatus
    settlement_available: bool
    customer_name: str
    outstanding_count: int
    outstanding_balance: int
    receivables: list[ReceivableDetailItem]


class ReceivableAccountOption(BaseModel):
    code: str
    name: str


class ReceivableOptionsResponse(BaseModel):
    receipt_accounts: list[ReceivableAccountOption]
    default_receipt_account: str | None


class ReceivablePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ledger_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    customer_name: str
    settlement_date: date
    payment_amount: int = Field(gt=0, strict=True)
    receipt_account: str
    mode: ReceivablePreviewMode | None = None
    difference_account: str | None = None
    difference_summary: str | None = None

    @field_validator("customer_name", "receipt_account")
    @classmethod
    def reject_blank_required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class ReceivablePreviewCandidate(BaseModel):
    code: str
    receivable_id: str
    billing_date: str
    billed_amount: int
    balance: int
    scheduled_amount: int
    receivable_account: str
    receivable_sub_account: str
    department: str
    customer_name: str
    summary: str


class ReceivablePreviewJournalRow(BaseModel):
    debit_account: str
    credit_account: str
    credit_sub_account: str
    department: str
    amount: int
    summary: str


class ReceivableRecommendedAccount(BaseModel):
    code: str
    name: str


class ReceivablePreviewResponse(BaseModel):
    ledger_revision: str
    customer_name: str
    settlement_date: str
    payment_amount: int
    receipt_account: str
    total_receivable_balance: int
    original_difference: int
    target_total: int
    difference: int
    pattern: ReceivablePreviewPattern
    mode: ReceivablePreviewMode | None
    available_modes: list[ReceivablePreviewMode]
    difference_account_required: bool
    preview_complete: bool
    source_candidates: list[ReceivablePreviewCandidate]
    rows: list[ReceivablePreviewJournalRow]
    recommended_difference_accounts: list[ReceivableRecommendedAccount]


def get_receivables_directory() -> Path:
    """Small dependency overridden by tests; resolving it writes nothing."""

    return DEFAULT_RECEIVABLES_DIRECTORY


def get_receivable_account_master_snapshot() -> dict[str, Any]:
    """Load the existing journal master snapshot without route-level CSV I/O."""

    try:
        return load_journal_masters()
    except Exception as error:
        logger.exception("Could not load account master for receivables")
        raise HTTPException(
            status_code=503,
            detail="マスターデータを安全に読み込めません。",
        ) from error


def get_receivable_preview_transactions_snapshot():
    """Use the B1 production wrapper unless tests inject a transaction snapshot."""

    return None


def get_receivable_payment_accounts_path() -> Path:
    """Return the production payment-account source; tests override this path."""

    return PAYMENT_ACCOUNTS_PATH


def _load_ready_current_snapshot(receivables_directory: Path):
    try:
        return read_receivable_current_snapshot_when_ready(
            receivables_directory
        )
    except ReceivableLedgerLockTimeout as error:
        raise HTTPException(
            status_code=423,
            detail="未収台帳をほかの処理が使用中です。",
        ) from error
    except ReceivableLedgerRecoveryRequired as error:
        raise HTTPException(
            status_code=503,
            detail="未収台帳の復旧確認が必要です。",
        ) from error
    except (
        ReceivableLedgerMissingError,
        ReceivableLedgerMalformedError,
        ReceivableLedgerSchemaError,
    ) as error:
        raise HTTPException(
            status_code=503,
            detail="未収台帳を安全に読み込めません。",
        ) from error
    except Exception as error:
        logger.exception("Unexpected error while reading receivable ledger")
        raise HTTPException(
            status_code=500,
            detail="未収処理中にエラーが発生しました。",
        ) from error


@router.get(
    "/summary",
    response_model=ReceivableSummaryResponse,
)
def get_receivable_summary(
    receivables_directory: Path = Depends(get_receivables_directory),
):
    snapshot = _load_ready_current_snapshot(receivables_directory)
    summary = build_receivable_summary(snapshot.current_df)
    return {
        "ledger_revision": snapshot.ledger_revision,
        "ledger_status": "ready",
        "settlement_available": snapshot.settlement_available,
        **summary,
    }


@router.get(
    "/customers/detail",
    response_model=ReceivableCustomerDetailResponse,
)
def get_receivable_customer_detail(
    customer_name: str = Query(min_length=1),
    receivables_directory: Path = Depends(get_receivables_directory),
):
    snapshot = _load_ready_current_snapshot(receivables_directory)
    try:
        detail = build_receivable_customer_detail(
            snapshot.current_df,
            customer_name,
        )
    except ReceivableCustomerNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail="指定した取引先の未収データがありません。",
        ) from error
    return {
        "ledger_revision": snapshot.ledger_revision,
        "ledger_status": "ready",
        "settlement_available": snapshot.settlement_available,
        **detail,
    }


@router.get(
    "/options",
    response_model=ReceivableOptionsResponse,
)
def get_receivable_options(
    account_master_snapshot: dict[str, Any] = Depends(
        get_receivable_account_master_snapshot
    ),
    payment_accounts_path: Path = Depends(
        get_receivable_payment_accounts_path
    ),
):
    try:
        result = load_receipt_account_options(
            account_master_snapshot,
            payment_accounts_path,
        )
    except Exception as error:
        logger.exception("Could not load receivable account options")
        raise HTTPException(
            status_code=503,
            detail="未収入金科目候補を安全に読み込めません。",
        ) from error
    return {
        "receipt_accounts": result["receipt_accounts"],
        "default_receipt_account": result["default_receipt_account"],
    }


@router.post(
    "/preview-settlement",
    response_model=ReceivablePreviewResponse,
)
def post_receivable_preview_settlement(
    request: ReceivablePreviewRequest,
    receivables_directory: Path = Depends(get_receivables_directory),
    account_master_snapshot: dict[str, Any] = Depends(
        get_receivable_account_master_snapshot
    ),
    transactions_snapshot=Depends(
        get_receivable_preview_transactions_snapshot
    ),
):
    try:
        return build_receivable_preview_application_result(
            receivables_directory,
            ledger_revision=request.ledger_revision,
            customer_name=request.customer_name,
            settlement_date=request.settlement_date.isoformat(),
            payment_amount=request.payment_amount,
            receipt_account=request.receipt_account,
            mode=request.mode,
            difference_account=request.difference_account,
            difference_summary=request.difference_summary,
            account_master_snapshot=account_master_snapshot,
            transactions_snapshot=transactions_snapshot,
        )
    except ReceivableLedgerLockTimeout as error:
        raise HTTPException(
            status_code=423,
            detail="未収台帳をほかの処理が使用中です。",
        ) from error
    except ReceivableLedgerConflictError as error:
        raise HTTPException(
            status_code=409,
            detail="未収データが更新されています。内容を再確認してください。",
        ) from error
    except ReceivablePreviewCustomerNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail="指定した取引先の未収データがありません。",
        ) from error
    except ReceivableSettlementMasterValidationError as error:
        raise HTTPException(
            status_code=422,
            detail="選択した科目を現在のマスターで確認できません。",
        ) from error
    except ReceivablePreviewValidationError as error:
        raise HTTPException(
            status_code=422,
            detail="入力内容を確認してください。",
        ) from error
    except (
        ReceivableLedgerRecoveryRequired,
        ReceivableLedgerSettlementUnavailableError,
    ) as error:
        raise HTTPException(
            status_code=503,
            detail="未収台帳の復旧確認が必要です。",
        ) from error
    except (
        ReceivableLedgerMissingError,
        ReceivableLedgerMalformedError,
        ReceivableLedgerSchemaError,
    ) as error:
        raise HTTPException(
            status_code=503,
            detail="未収台帳を安全に読み込めません。",
        ) from error
    except Exception as error:
        logger.exception("Unexpected error during receivable Preview")
        raise HTTPException(
            status_code=500,
            detail="未収Preview処理中にエラーが発生しました。",
        ) from error

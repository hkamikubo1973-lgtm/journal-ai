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
    ReceivableIdempotencyConflictError,
    ReceivableLedgerConflictError,
    ReceivableLedgerLockTimeout,
    ReceivableLedgerMalformedError,
    ReceivableLedgerMissingError,
    ReceivableLedgerRecoveryRequired,
    ReceivableLedgerSchemaError,
    ReceivableLedgerSettlementUnavailableError,
    ReceivableLedgerWriteError,
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
from receivable_preview_service import (
    DIFFERENCE_ACCOUNT_MODE,
    PARTIAL_SETTLEMENT_MODE,
)
from receivable_settlement_execute_service import (
    execute_receivable_settlement,
)
from receivable_settlement_service import (
    ReceivableSettlementConflictError,
    ReceivableSettlementValidationError,
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


class ReceivableSettlementExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str
    preview_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    customer_name: str
    settlement_date: date
    payment_amount: int = Field(gt=0, strict=True)
    receipt_account: str
    mode: ReceivablePreviewMode | None = None
    difference_account: str | None = None
    difference_summary: str | None = None

    @field_validator("idempotency_key", "customer_name", "receipt_account")
    @classmethod
    def reject_blank_execute_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class ReceivableExecutedSettlement(BaseModel):
    settlement_id: str
    settlement_date: str
    customer_name: str
    payment_amount: int
    target_total: int
    difference: int
    source_candidates: list[ReceivablePreviewCandidate]
    rows: list[ReceivablePreviewJournalRow]
    created_at: str


class ReceivableSettlementExecuteResponse(BaseModel):
    replayed: bool
    settlement_id: str
    transaction_id: str
    ledger_revision: str
    history_revision: str
    settlement: ReceivableExecutedSettlement
    message: str


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


def _execute_internal_mode(mode: ReceivablePreviewMode | None) -> str | None:
    if mode == "partial":
        return PARTIAL_SETTLEMENT_MODE
    if mode == "difference_account":
        return DIFFERENCE_ACCOUNT_MODE
    return None


def _settlement_candidate_response(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": candidate["コード"],
        "receivable_id": candidate["未収ID"],
        "billing_date": candidate["請求日"],
        "billed_amount": candidate["請求額"],
        "balance": candidate["残高"],
        "scheduled_amount": candidate["消込予定"],
        "receivable_account": candidate["未収科目"],
        "receivable_sub_account": candidate["未収補助"],
        "department": candidate["部門"],
        "customer_name": candidate["取引先"],
        "summary": candidate["摘要"],
    }


def _settlement_journal_row_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "debit_account": row["借方科目"],
        "credit_account": row["貸方科目"],
        "credit_sub_account": row["貸方補助"],
        "department": row["部門"],
        "amount": row["金額"],
        "summary": row["摘要"],
    }


def _settlement_response(settlement: dict[str, Any]) -> dict[str, Any]:
    return {
        "settlement_id": settlement["settlement_id"],
        "settlement_date": settlement["settlement_date"],
        "customer_name": settlement["customer_name"],
        "payment_amount": settlement["payment_amount"],
        "target_total": settlement["target_total"],
        "difference": settlement["difference"],
        "source_candidates": [
            _settlement_candidate_response(candidate)
            for candidate in settlement["source_candidates"]
        ],
        "rows": [
            _settlement_journal_row_response(row)
            for row in settlement["rows"]
        ],
        "created_at": settlement["created_at"],
    }


@router.post(
    "/execute-settlement",
    response_model=ReceivableSettlementExecuteResponse,
)
def post_receivable_execute_settlement(
    request: ReceivableSettlementExecuteRequest,
    receivables_directory: Path = Depends(get_receivables_directory),
    account_master_snapshot: dict[str, Any] = Depends(
        get_receivable_account_master_snapshot
    ),
):
    try:
        result = execute_receivable_settlement(
            receivables_directory,
            idempotency_key=request.idempotency_key,
            preview_revision=request.preview_revision,
            customer_name=request.customer_name,
            settlement_date=request.settlement_date.isoformat(),
            payment_amount=request.payment_amount,
            receipt_account=request.receipt_account,
            mode=_execute_internal_mode(request.mode),
            difference_account=request.difference_account,
            difference_summary=request.difference_summary,
            account_master_snapshot=account_master_snapshot,
        )
        return {
            "replayed": result.replayed,
            "settlement_id": result.settlement_id,
            "transaction_id": result.transaction_id,
            "ledger_revision": result.current_after_hash,
            "history_revision": result.history_after_hash,
            "settlement": _settlement_response(result.settlement),
            "message": "未収消込が完了しました",
        }
    except ReceivableLedgerLockTimeout as error:
        raise HTTPException(
            status_code=423,
            detail="未収台帳をほかの処理が使用中です。",
        ) from error
    except ReceivableIdempotencyConflictError as error:
        raise HTTPException(
            status_code=409,
            detail="同じ操作IDが別の内容で使用されています。",
        ) from error
    except ReceivableLedgerConflictError as error:
        raise HTTPException(
            status_code=409,
            detail="未収データが更新されています。内容を再確認してください。",
        ) from error
    except ReceivableSettlementConflictError as error:
        raise HTTPException(
            status_code=409,
            detail="消込対象が変更されています。内容を再確認してください。",
        ) from error
    except ReceivableSettlementMasterValidationError as error:
        raise HTTPException(
            status_code=422,
            detail="選択した科目を現在のマスターで確認できません。",
        ) from error
    except ReceivableSettlementValidationError as error:
        raise HTTPException(
            status_code=422,
            detail="入力内容を確認してください。",
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
    except ReceivableLedgerWriteError as error:
        raise HTTPException(
            status_code=503,
            detail="未収台帳を安全に更新できませんでした。",
        ) from error
    except Exception as error:
        logger.exception("Unexpected error during receivable settlement")
        raise HTTPException(
            status_code=500,
            detail="未収消込処理中にエラーが発生しました。",
        ) from error

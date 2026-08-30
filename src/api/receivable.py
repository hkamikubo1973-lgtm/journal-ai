"""Read-only receivable summary and customer detail API."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from receivable_persistence_service import (
    DEFAULT_RECEIVABLES_DIRECTORY,
    ReceivableLedgerLockTimeout,
    ReceivableLedgerMalformedError,
    ReceivableLedgerMissingError,
    ReceivableLedgerRecoveryRequired,
    ReceivableLedgerSchemaError,
    read_receivable_current_snapshot_when_ready,
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


def get_receivables_directory() -> Path:
    """Small dependency overridden by tests; resolving it writes nothing."""

    return DEFAULT_RECEIVABLES_DIRECTORY


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

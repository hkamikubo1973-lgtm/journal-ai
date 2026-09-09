"""Trust-boundary orchestration for union-source EPSON exports."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from hmac import compare_digest
from pathlib import Path
from typing import Any

from journal_export_service import (
    EpsonCsvExport,
    EpsonExportValidationError,
    export_epson_csv,
    validate_epson_export_items,
)
from journal_master_service import load_journal_masters
from journal_persistence_service import load_transactions_df
from journal_save_service import EpsonSaveResult, save_and_register_epson_csv
from receivable_epson_materialization_service import (
    materialize_receivable_epson_items,
)
from receivable_registration_handoff_application_service import (
    build_receivable_registration_handoff_application_result,
)
from receivable_registration_handoff_service import (
    ReceivableRegistrationHandoffValidationError,
    build_settlement_row_id,
)


SEARCHED_JOURNAL_SOURCE_TYPE = "searched_journal"
RECEIVABLE_SETTLEMENT_SOURCE_TYPE = "receivable_settlement"
PROVENANCE_MISMATCH = "provenance_mismatch"
MASTER_VALIDATION_FAILED = "master_validation_failed"


class EpsonExportApplicationValidationError(EpsonExportValidationError):
    """Reject a union request before generating or saving a partial CSV."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def load_epson_transactions_snapshot() -> list[dict[str, Any]]:
    """Read the current 45-column search DB once for one application call."""

    return load_transactions_df().to_dict(orient="records")


def _searched_ready_item(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "registration_id": item.get("registration_id"),
        "prepared_journal": item.get("prepared_journal"),
        "epson_base_row": item.get("epson_base_row"),
    }


def _receivable_provenance(
    item: Mapping[str, Any],
    item_number: int,
) -> tuple[str, str, int, int, str]:
    provenance = item.get("provenance")
    if not isinstance(provenance, Mapping):
        raise EpsonExportApplicationValidationError(
            PROVENANCE_MISMATCH,
            f"{item_number}件目の未収provenanceがありません。",
        )

    settlement_id = provenance.get("settlement_id")
    receipt_ref = provenance.get("receipt_ref")
    row_index = provenance.get("row_index")
    row_count = provenance.get("row_count")
    settlement_row_id = provenance.get("settlement_row_id")
    if not isinstance(settlement_id, str) or not settlement_id:
        raise EpsonExportApplicationValidationError(
            PROVENANCE_MISMATCH,
            f"{item_number}件目のsettlement_idがありません。",
        )
    if not isinstance(receipt_ref, str) or not receipt_ref:
        raise EpsonExportApplicationValidationError(
            PROVENANCE_MISMATCH,
            f"{item_number}件目のreceipt_refがありません。",
        )
    if type(row_index) is not int or row_index < 0:
        raise EpsonExportApplicationValidationError(
            PROVENANCE_MISMATCH,
            f"{item_number}件目のrow_indexが不正です。",
        )
    if type(row_count) is not int or row_count < 1:
        raise EpsonExportApplicationValidationError(
            PROVENANCE_MISMATCH,
            f"{item_number}件目のrow_countが不正です。",
        )
    if not isinstance(settlement_row_id, str) or not settlement_row_id:
        raise EpsonExportApplicationValidationError(
            PROVENANCE_MISMATCH,
            f"{item_number}件目のsettlement_row_idがありません。",
        )
    return settlement_id, receipt_ref, row_index, row_count, settlement_row_id


def resolve_epson_export_items(
    items: Sequence[Mapping[str, Any]],
    *,
    receivables_directory: str | Path,
    journal_master_snapshot: Any = None,
    transactions_snapshot: Any = None,
    journal_master_loader: Callable[[], Any] | None = None,
    transactions_snapshot_loader: Callable[[], Any] | None = None,
) -> list[dict[str, Any]]:
    """Resolve a complete mixed cart into existing EPSON-ready items."""

    if not items:
        raise EpsonExportValidationError("EPSON CSVの出力対象がありません。")

    ready_items: list[dict[str, Any] | None] = [None] * len(items)
    receivable_groups: dict[
        tuple[str, str], list[tuple[int, int, int, str]]
    ] = {}
    receipt_refs_by_settlement: dict[str, str] = {}

    for position, item in enumerate(items):
        item_number = position + 1
        if not isinstance(item, Mapping):
            raise EpsonExportValidationError(
                f"{item_number}件目の登録予定が不正です。"
            )
        source_type = item.get("source_type", SEARCHED_JOURNAL_SOURCE_TYPE)
        if source_type == SEARCHED_JOURNAL_SOURCE_TYPE:
            ready_items[position] = _searched_ready_item(item)
            continue
        if source_type != RECEIVABLE_SETTLEMENT_SOURCE_TYPE:
            raise EpsonExportValidationError(
                f"{item_number}件目のsource_typeが不正です。"
            )

        (
            settlement_id,
            receipt_ref,
            row_index,
            row_count,
            settlement_row_id,
        ) = _receivable_provenance(item, item_number)
        previous_ref = receipt_refs_by_settlement.setdefault(
            settlement_id, receipt_ref
        )
        if previous_ref != receipt_ref:
            raise EpsonExportApplicationValidationError(
                PROVENANCE_MISMATCH,
                f"{item_number}件目のreceipt_refが同じsettlementと一致しません。",
            )
        receivable_groups.setdefault(
            (settlement_id, receipt_ref), []
        ).append((position, row_index, row_count, settlement_row_id))

    if receivable_groups:
        if journal_master_snapshot is None:
            loader = journal_master_loader or load_journal_masters
            journal_master_snapshot = loader()
        if transactions_snapshot is None:
            loader = (
                transactions_snapshot_loader
                or load_epson_transactions_snapshot
            )
            transactions_snapshot = loader()

    for (settlement_id, receipt_ref), requested_rows in receivable_groups.items():
        try:
            handoff = build_receivable_registration_handoff_application_result(
                receivables_directory,
                settlement_id=settlement_id,
                receipt_ref=receipt_ref,
                journal_master_snapshot=journal_master_snapshot,
            )
        except ReceivableRegistrationHandoffValidationError as exc:
            raise EpsonExportApplicationValidationError(
                MASTER_VALIDATION_FAILED,
                "未収仕訳を現在のマスターで解決できません。",
            ) from exc

        trusted_items = handoff["items"]
        trusted_row_count = handoff["row_count"]
        requested_indexes = [row[1] for row in requested_rows]
        if sorted(requested_indexes) != list(range(trusted_row_count)):
            raise EpsonExportApplicationValidationError(
                PROVENANCE_MISMATCH,
                "未収settlementのrow構成がreceiptと一致しません。",
            )

        for _, row_index, row_count, settlement_row_id in requested_rows:
            if row_count != trusted_row_count:
                raise EpsonExportApplicationValidationError(
                    PROVENANCE_MISMATCH,
                    "未収settlementのrow_countがreceiptと一致しません。",
                )
            expected_row_id = build_settlement_row_id(
                receipt_ref, settlement_id, row_index
            )
            trusted_row_id = trusted_items[row_index]["provenance"][
                "settlement_row_id"
            ]
            if not settlement_row_id.isascii() or not compare_digest(settlement_row_id, expected_row_id) or not (
                compare_digest(trusted_row_id, expected_row_id)
            ):
                raise EpsonExportApplicationValidationError(
                    PROVENANCE_MISMATCH,
                    "未収settlementのrow integrityが一致しません。",
                )

        materialized = materialize_receivable_epson_items(
            trusted_items,
            transactions_snapshot,
            customer_name=handoff["customer_name"],
            settlement_date=handoff["settlement_date"],
        )
        for requested in requested_rows:
            ready = materialized[requested[1]]
            ready_items[requested[0]] = {
                "registration_id": ready["registration_id"],
                "prepared_journal": ready["prepared_journal"],
                "epson_base_row": ready["epson_base_row"],
            }

    normalized = [item for item in ready_items if item is not None]
    if len(normalized) != len(items):
        raise EpsonExportValidationError(
            "EPSON CSVの全itemを準備できませんでした。"
        )
    validate_epson_export_items(normalized)
    return normalized


def _shared_master_arguments(items, resolve_arguments):
    """Reuse the converter's current masters for the final CSV overlay."""
    if not any(
        isinstance(item, Mapping)
        and item.get("source_type") == RECEIVABLE_SETTLEMENT_SOURCE_TYPE
        for item in items
    ):
        return {}
    if resolve_arguments.get("journal_master_snapshot") is None:
        loader = resolve_arguments.get("journal_master_loader") or load_journal_masters
        resolve_arguments["journal_master_snapshot"] = loader()
    return {"journal_master_snapshot": resolve_arguments["journal_master_snapshot"]}


def export_epson_csv_application_result(
    items: Sequence[Mapping[str, Any]],
    *,
    receivables_directory: str | Path,
    export_builder: Callable[..., EpsonCsvExport] | None = None,
    **resolve_arguments: Any,
) -> EpsonCsvExport:
    export_arguments = _shared_master_arguments(items, resolve_arguments)
    ready_items = resolve_epson_export_items(
        items,
        receivables_directory=receivables_directory,
        **resolve_arguments,
    )
    return (export_builder or export_epson_csv)(ready_items, **export_arguments)


def save_epson_csv_application_result(
    items: Sequence[Mapping[str, Any]],
    *,
    receivables_directory: str | Path,
    save_builder: Callable[..., EpsonSaveResult] | None = None,
    **resolve_arguments: Any,
) -> EpsonSaveResult:
    export_arguments = _shared_master_arguments(items, resolve_arguments)
    ready_items = resolve_epson_export_items(
        items,
        receivables_directory=receivables_directory,
        **resolve_arguments,
    )
    return (save_builder or save_and_register_epson_csv)(ready_items, **export_arguments)

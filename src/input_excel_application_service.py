"""Trust-boundary orchestration for union-source Input Excel exports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from hmac import compare_digest
from pathlib import Path
from typing import Any

from input_excel_save_service import InputExcelSaveResult, save_input_excel
from input_excel_service import (
    InputExcelExport,
    InputExcelValidationError,
    export_validated_input_excel,
    validate_input_excel_items,
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


class InputExcelReceivableValidationError(InputExcelValidationError):
    """Raised when receivable provenance cannot identify a trusted row."""


def _receivable_provenance(
    item: Mapping[str, Any],
    item_number: int,
) -> tuple[str, str, int, int, str]:
    provenance = item.get("provenance")
    if not isinstance(provenance, Mapping):
        raise InputExcelReceivableValidationError(
            f"{item_number}件目のprovenanceがありません。"
        )

    settlement_id = provenance.get("settlement_id")
    receipt_ref = provenance.get("receipt_ref")
    row_index = provenance.get("row_index")
    row_count = provenance.get("row_count")
    settlement_row_id = provenance.get("settlement_row_id")
    if not isinstance(settlement_id, str) or not settlement_id:
        raise InputExcelReceivableValidationError(
            f"{item_number}件目のsettlement_idがありません。"
        )
    if not isinstance(receipt_ref, str) or not receipt_ref:
        raise InputExcelReceivableValidationError(
            f"{item_number}件目のreceipt_refがありません。"
        )
    if type(row_index) is not int or row_index < 0:
        raise InputExcelReceivableValidationError(
            f"{item_number}件目のrow_indexが不正です。"
        )
    if type(row_count) is not int or row_count < 1:
        raise InputExcelReceivableValidationError(
            f"{item_number}件目のrow_countが不正です。"
        )
    if not isinstance(settlement_row_id, str) or not settlement_row_id:
        raise InputExcelReceivableValidationError(
            f"{item_number}件目のsettlement_row_idがありません。"
        )
    return (
        settlement_id,
        receipt_ref,
        row_index,
        row_count,
        settlement_row_id,
    )


def resolve_input_excel_items(
    items: Sequence[Mapping[str, Any]],
    *,
    receivables_directory: str | Path,
    journal_master_snapshot: Any,
) -> list[dict[str, Any]]:
    """Resolve all cart items without producing a partial workbook."""

    if not items:
        raise InputExcelValidationError(
            "入力用Excelの出力対象がありません。"
        )

    resolved_items: list[dict[str, Any]] = []
    handoffs: dict[tuple[str, str], dict[str, Any]] = {}
    receipt_refs_by_settlement: dict[str, str] = {}
    last_row_indexes: dict[tuple[str, str], int] = {}

    for item_number, item in enumerate(items, start=1):
        if not isinstance(item, Mapping):
            raise InputExcelValidationError(
                f"{item_number}件目の登録予定が不正です。"
            )
        source_type = item.get("source_type", SEARCHED_JOURNAL_SOURCE_TYPE)
        if source_type == SEARCHED_JOURNAL_SOURCE_TYPE:
            resolved_items.extend(validate_input_excel_items([item]))
            continue
        if source_type != RECEIVABLE_SETTLEMENT_SOURCE_TYPE:
            raise InputExcelValidationError(
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
            settlement_id,
            receipt_ref,
        )
        if previous_ref != receipt_ref:
            raise InputExcelReceivableValidationError(
                f"{item_number}件目のreceipt_refが同じsettlementと一致しません。"
            )

        key = (settlement_id, receipt_ref)
        previous_index = last_row_indexes.get(key)
        if previous_index is not None and row_index <= previous_index:
            raise InputExcelReceivableValidationError(
                f"{item_number}件目のrow_indexがreceipt順ではありません。"
            )
        last_row_indexes[key] = row_index

        handoff = handoffs.get(key)
        if handoff is None:
            try:
                handoff = build_receivable_registration_handoff_application_result(
                    receivables_directory,
                    settlement_id=settlement_id,
                    receipt_ref=receipt_ref,
                    journal_master_snapshot=journal_master_snapshot,
                )
            except ReceivableRegistrationHandoffValidationError as exc:
                raise InputExcelReceivableValidationError(
                    f"{item_number}件目を現在のマスターで解決できません。"
                ) from exc
            handoffs[key] = handoff

        trusted_items = handoff["items"]
        trusted_row_count = handoff["row_count"]
        if row_count != trusted_row_count:
            raise InputExcelReceivableValidationError(
                f"{item_number}件目のrow_countがreceiptと一致しません。"
            )
        if row_index >= trusted_row_count:
            raise InputExcelReceivableValidationError(
                f"{item_number}件目のrow_indexがreceipt範囲外です。"
            )

        expected_row_id = build_settlement_row_id(
            receipt_ref,
            settlement_id,
            row_index,
        )
        if not compare_digest(settlement_row_id, expected_row_id):
            raise InputExcelReceivableValidationError(
                f"{item_number}件目のsettlement_row_idが一致しません。"
            )
        trusted_item = trusted_items[row_index]
        trusted_provenance = trusted_item["provenance"]
        if not compare_digest(
            trusted_provenance["settlement_row_id"],
            expected_row_id,
        ):
            raise InputExcelReceivableValidationError(
                f"{item_number}件目のreceipt row integrityを確認できません。"
            )

        resolved_items.append({
            "prepared_journal": dict(trusted_item["prepared_journal"]),
            "print_category": trusted_item["print_metadata"][
                "print_category"
            ],
            "print_warnings": list(trusted_item["print_warnings"]),
        })

    return resolved_items


def export_input_excel_application_result(
    items: Sequence[Mapping[str, Any]],
    *,
    receivables_directory: str | Path,
    journal_master_snapshot: Any,
    export_datetime: datetime | None = None,
) -> InputExcelExport:
    """Resolve the complete union cart before creating workbook bytes."""

    resolved_items = resolve_input_excel_items(
        items,
        receivables_directory=receivables_directory,
        journal_master_snapshot=journal_master_snapshot,
    )
    return export_validated_input_excel(
        resolved_items,
        export_datetime=export_datetime,
    )


def save_input_excel_application_result(
    items: Sequence[Mapping[str, Any]],
    *,
    receivables_directory: str | Path,
    journal_master_snapshot: Any,
    export_dir: str | None = None,
    export_datetime: datetime | None = None,
) -> InputExcelSaveResult:
    """Resolve the complete union cart before writing one workbook."""

    resolved_items = resolve_input_excel_items(
        items,
        receivables_directory=receivables_directory,
        journal_master_snapshot=journal_master_snapshot,
    )
    return save_input_excel(
        resolved_items,
        export_dir=export_dir,
        export_datetime=export_datetime,
        export_builder=export_validated_input_excel,
    )

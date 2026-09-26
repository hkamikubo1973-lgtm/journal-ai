"""Trust-boundary orchestration for union-source Input Excel exports."""



from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from input_excel_save_service import InputExcelSaveResult, save_input_excel
from input_excel_service import (
    InputExcelExport,
    InputExcelValidationError,
    export_validated_input_excel,
    validate_input_excel_items,
)
from receivable_cart_service import resolve_cart_item
from receivable_registration_handoff_service import ReceivableRegistrationHandoffValidationError


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

    resolved_items = []
    receipt_cache = {}
    last_indexes = {}
    references = {}
    for item_number, item in enumerate(items, start=1):
        if not isinstance(item, Mapping):
            raise InputExcelValidationError("登録予定が不正です。")
        source = item.get("source_type", SEARCHED_JOURNAL_SOURCE_TYPE)
        if source == SEARCHED_JOURNAL_SOURCE_TYPE:
            resolved_items.extend(validate_input_excel_items([item]))
        elif source == RECEIVABLE_SETTLEMENT_SOURCE_TYPE:
            sid, ref, index, count, row_id = _receivable_provenance(item, item_number)
            if references.setdefault(sid, ref) != ref or index <= last_indexes.get((sid, ref), -1):
                raise InputExcelReceivableValidationError("未収receiptの参照または行順が不正です。")
            last_indexes[(sid, ref)] = index
            try:
                ready = resolve_cart_item(item, receivables_directory, cache=receipt_cache)
            except (ValueError, ReceivableRegistrationHandoffValidationError) as exc:
                raise InputExcelReceivableValidationError("未収仕訳の内容またはprovenanceを確認できません。") from exc
            resolved_items.extend(validate_input_excel_items([ready]))
        else:
            raise InputExcelValidationError("source_typeが不正です。")
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

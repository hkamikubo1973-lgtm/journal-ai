"""Receipt provenance identifies origin; confirmed cart content identifies output."""
from copy import deepcopy
from datetime import datetime
import getpass
import platform

from columns import EPSON_COLUMNS
from journal_registration_service import build_epson_edit_values, build_registration_id, build_print_metadata, build_print_warnings, prepare_registration
from journal_export_service import validate_epson_export_items, EpsonExportValidationError
from receivable_receipt_service import read_receivable_settlement_receipt
from receivable_registration_handoff_service import build_settlement_row_id
from receivable_legacy_template_service import find_receivable_template_match
from system_settings import load_system_settings


def make_cart_ready(items, settlement, transactions):
    records = [{"rows": [{k: str(v).strip() for k, v in row.items()} for row in transactions]}]
    ready = []
    for item, journal in zip(items, settlement["rows"]):
        template, diagnostic = find_receivable_template_match(
            journal, settlement["customer_name"], settlement.get("source_candidates", []), records)
        base = {column: (template or {}).get(column, "") for column in EPSON_COLUMNS}
        prepared = dict(item["prepared_journal"])
        prepared["voucher_summary"] = base["伝票摘要"]
        base.update(build_epson_edit_values(prepared))
        base.update({"入力マシン": platform.node(), "入力ユーザ": getpass.getuser(),
                     "入力アプリ": "仕訳検索システム", "入力会社": load_system_settings().get("company_name", ""),
                     "入力日付": datetime.now().strftime("%Y%m%d")})
        ready.append({**item, "prepared_journal": prepared, "epson_base_row": base,
                      "epson_preview_row": build_epson_edit_values(prepared),
                      "registration_id": build_registration_id(prepared, base),
                      "settlement_row_id": item["provenance"]["settlement_row_id"],
                      "print_metadata": build_print_metadata(base),
                      "print_warnings": build_print_warnings(prepared, base),
                      "epson_capability": {"status": "ready"},
                      "template_diagnostics": diagnostic})
    return ready


def validate_provenance(item, directory, cache=None):
    p = item.get("provenance")
    if not isinstance(p, dict):
        raise EpsonExportValidationError("未収provenanceがありません。")
    sid, ref = p.get("settlement_id"), p.get("receipt_ref")
    if not isinstance(sid, str) or not isinstance(ref, str):
        raise EpsonExportValidationError("未収provenanceが不正です。")
    key = (sid, ref)
    if cache is None:
        cache = {}
    if key not in cache:
        cache[key] = read_receivable_settlement_receipt(directory, ref, expected_settlement_id=sid)
    receipt = cache[key]
    index, count = p.get("row_index"), p.get("row_count")
    if type(index) is not int or type(count) is not int or count != len(receipt.settlement["rows"]) or not 0 <= index < count:
        raise EpsonExportValidationError("未収receiptの行構成が一致しません。")
    if p.get("settlement_row_id") != build_settlement_row_id(ref, sid, index):
        raise EpsonExportValidationError("未収receiptの行識別子が一致しません。")
    return receipt


def resolve_cart_item(item, directory, *, cache=None):
    validate_provenance(item, directory, cache)
    validate_epson_export_items([item])
    result = deepcopy(item)
    result["print_metadata"] = build_print_metadata(result["epson_base_row"])
    result["print_warnings"] = build_print_warnings(result["prepared_journal"], result["epson_base_row"])
    return result


def edit_cart_item(item, edits, directory, masters):
    ready = resolve_cart_item(item, directory)
    prepared = dict(ready["prepared_journal"])
    allowed = {"debit_account_code", "credit_account_code", "debit_sub_code", "credit_sub_code",
               "debit_dept_code", "credit_dept_code", "amount", "summary"}
    if not isinstance(edits, dict) or set(edits) - allowed:
        raise EpsonExportValidationError("編集項目が不正です。")
    prepared.update(edits)
    for side in ("debit", "credit"):
        for kind, collection in (("account", "accounts"), ("dept", "departments")):
            code = str(prepared[f"{side}_{kind}_code"] or "").strip()
            matches = [r for r in masters[collection] if str(r["code"]) == code]
            if not code and kind == "dept":
                name = ""
            elif len(matches) != 1:
                raise EpsonExportValidationError("現在のマスターで科目・部門を解決できません。")
            else:
                name = matches[0]["name"]
            prepared[f"{side}_{kind}_code"] = code
            prepared[f"{side}_{kind}_name"] = name
        sub = str(prepared[f"{side}_sub_code"] or "").strip()
        matches = [r for r in masters.get("sub_account_relations", [])
                   if r["account_code"] == prepared[f"{side}_account_code"] and r["sub_code"] == sub]
        if sub and len(matches) != 1:
            raise EpsonExportValidationError("現在のマスターで補助科目を解決できません。")
        prepared[f"{side}_sub_code"] = sub
        prepared[f"{side}_sub_name"] = matches[0]["sub_name"] if sub else ""
    prepared["source_debit_amount"] = str(prepared["amount"])
    prepared["source_credit_amount"] = str(prepared["amount"])
    result = prepare_registration({"edit_form": prepared, "candidate_meta": {"editable_row_count": 1},
                                   "source_row": ready["epson_base_row"]}, master_snapshot=masters)
    if not result["ok"]:
        raise EpsonExportValidationError("編集内容を確認してください。" + " ".join(result["errors"]))
    return {**ready, **{k: result[k] for k in ("prepared_journal", "epson_base_row", "epson_preview_row",
                                               "registration_id", "print_metadata", "print_warnings")}}

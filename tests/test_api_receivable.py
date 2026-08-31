import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import api.receivable as receivable_api  # noqa: E402
import receivable_persistence_service as persistence  # noqa: E402
from receivable_account_validation_service import (  # noqa: E402
    ReceivableSettlementMasterValidationError,
)
from api.journal import app  # noqa: E402
from receivable_preview_application_service import (  # noqa: E402
    ReceivablePreviewCustomerNotFoundError,
    ReceivablePreviewValidationError,
)
from receivable_engine import (  # noqa: E402
    CURRENT_RECEIVABLE_COLUMNS,
    HISTORY_COLUMNS,
)


def current_row(
    customer="A商事",
    balance="1,000",
    *,
    code="C001",
    receivable_id="R001",
):
    return {
        "コード": code,
        "未収ID": receivable_id,
        "得意先名": customer,
        "請求日": "2026-08-01",
        "入金予定日": "2026-08-31",
        "未収科目": "未収運賃",
        "未収補助": "",
        "部門": "営業部",
        "摘要": "8月分",
        "請求金額": "1,200",
        "入金済額": "200",
        "残高": balance,
        "ステータス": "未処理",
    }


class ReceivableApiTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        self.paths = persistence.resolve_receivable_ledger_paths(
            self.directory
        )
        self.write_current([current_row()])
        self.write_history([])
        self.revision = persistence.calculate_current_revision(
            self.paths.current_path.read_bytes()
        )
        self.account_master_snapshot = {
            "accounts": [
                {"code": "114", "name": "普通預金", "category": "資産"},
                {"code": "115", "name": "当座預金", "category": "資産"},
                {"code": "751", "name": "支払手数料", "category": "費用"},
                {"code": "251", "name": "仮受金", "category": "負債"},
                {"code": "999", "name": "未収運賃", "category": "資産"},
            ]
        }
        self.payment_accounts_path = self.directory / "payment_accounts.csv"
        self.write_payment_accounts(["普通預金", "当座預金"])
        app.dependency_overrides[
            receivable_api.get_receivables_directory
        ] = lambda: self.directory
        app.dependency_overrides[
            receivable_api.get_receivable_account_master_snapshot
        ] = lambda: self.account_master_snapshot
        app.dependency_overrides[
            receivable_api.get_receivable_payment_accounts_path
        ] = lambda: self.payment_accounts_path
        app.dependency_overrides[
            receivable_api.get_receivable_preview_transactions_snapshot
        ] = lambda: []
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.temporary_directory.cleanup()

    def write_current(self, rows, columns=None):
        if columns is None:
            columns = CURRENT_RECEIVABLE_COLUMNS
        pd.DataFrame(rows, columns=columns).to_csv(
            self.paths.current_path,
            index=False,
            encoding="utf-8-sig",
        )

    def write_history(self, rows):
        pd.DataFrame(rows, columns=HISTORY_COLUMNS).to_csv(
            self.paths.history_path,
            index=False,
            encoding="utf-8-sig",
        )

    def write_payment_accounts(self, account_names):
        pd.DataFrame({"科目": account_names}).to_csv(
            self.payment_accounts_path,
            index=False,
            encoding="utf-8-sig",
        )

    def preview_payload(self, **overrides):
        payload = {
            "ledger_revision": self.revision,
            "customer_name": "A商事",
            "settlement_date": "2026-08-30",
            "payment_amount": 1000,
            "receipt_account": "普通預金",
            "mode": None,
            "difference_account": None,
            "difference_summary": None,
        }
        payload.update(overrides)
        return payload

    def post_preview(self, **overrides):
        return self.client.post(
            "/api/receivables/preview-settlement",
            json=self.preview_payload(**overrides),
        )

    def create_pending_transaction(self, transaction_id="api-pending"):
        current_before = self.paths.current_path.read_bytes()
        history_before = self.paths.history_path.read_bytes()
        paths, _ = persistence.prepare_transaction_artifacts(
            self.directory,
            transaction_id,
            current_before_bytes=current_before,
            current_after_bytes=current_before + b"after",
            history_before_bytes=history_before,
            history_after_bytes=history_before + b"after",
        )
        return paths

    def test_summary_returns_200_and_formal_dto(self):
        response = self.client.get("/api/receivables/summary")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["ledger_status"], "ready")
        self.assertTrue(body["settlement_available"])
        self.assertEqual(body["customer_count"], 1)
        self.assertEqual(body["outstanding_count"], 1)
        self.assertEqual(body["outstanding_balance"], 1000)
        self.assertEqual(body["customers"][0]["customer_name"], "A商事")

    def test_summary_revision_hashes_exact_parsed_current_bytes(self):
        expected = hashlib.sha256(
            self.paths.current_path.read_bytes()
        ).hexdigest()
        response = self.client.get("/api/receivables/summary")
        self.assertEqual(response.json()["ledger_revision"], expected)

    def test_detail_returns_200_and_mapped_item(self):
        response = self.client.get(
            "/api/receivables/customers/detail",
            params={"customer_name": "A商事"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["customer_name"], "A商事")
        self.assertEqual(body["outstanding_count"], 1)
        self.assertEqual(body["outstanding_balance"], 1000)
        self.assertEqual(body["receivables"][0]["receivable_id"], "R001")
        self.assertIs(type(body["receivables"][0]["balance"]), int)

    def test_detail_missing_customer_is_404(self):
        response = self.client.get(
            "/api/receivables/customers/detail",
            params={"customer_name": "不存在"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {"detail": "指定した取引先の未収データがありません。"},
        )

    def test_detail_missing_query_parameter_is_422(self):
        response = self.client.get("/api/receivables/customers/detail")
        self.assertEqual(response.status_code, 422)

    def test_current_missing_is_503_not_empty_summary(self):
        self.paths.current_path.unlink()
        response = self.client.get("/api/receivables/summary")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "未収台帳を安全に読み込めません。"},
        )

    def test_current_malformed_is_503(self):
        self.paths.current_path.write_bytes(b"\xff\xfeinvalid")
        response = self.client.get("/api/receivables/summary")
        self.assertEqual(response.status_code, 503)

    def test_current_schema_error_is_503(self):
        self.write_current([{"コード": "C001"}], columns=["コード"])
        response = self.client.get("/api/receivables/summary")
        self.assertEqual(response.status_code, 503)

    def test_valid_history_keeps_settlement_available_true(self):
        response = self.client.get("/api/receivables/summary")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["settlement_available"])

    def test_missing_history_keeps_available_true_without_creation(self):
        self.paths.history_path.unlink()
        response = self.client.get("/api/receivables/summary")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["settlement_available"])
        self.assertFalse(self.paths.history_path.exists())

    def test_history_malformed_keeps_summary_200_but_disables_settlement(self):
        self.paths.history_path.write_bytes(b"\xff\xfeinvalid-history")
        response = self.client.get("/api/receivables/summary")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ledger_status"], "ready")
        self.assertFalse(response.json()["settlement_available"])

    def test_history_schema_error_keeps_summary_200_but_disables_settlement(self):
        pd.DataFrame([{"消込ID": "S001"}]).to_csv(
            self.paths.history_path,
            index=False,
            encoding="utf-8-sig",
        )
        response = self.client.get("/api/receivables/summary")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ledger_status"], "ready")
        self.assertFalse(response.json()["settlement_available"])

    def test_history_malformed_keeps_detail_200_but_disables_settlement(self):
        self.paths.history_path.write_bytes(b"\xff\xfeinvalid-history")
        response = self.client.get(
            "/api/receivables/customers/detail",
            params={"customer_name": "A商事"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ledger_status"], "ready")
        self.assertFalse(response.json()["settlement_available"])

    def test_history_malformed_bytes_are_unchanged_after_read_routes(self):
        self.paths.history_path.write_bytes(b"\xff\xfeinvalid-history")
        history_before = self.paths.history_path.read_bytes()

        summary = self.client.get("/api/receivables/summary")
        detail = self.client.get(
            "/api/receivables/customers/detail",
            params={"customer_name": "A商事"},
        )

        self.assertEqual(summary.status_code, 200)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(self.paths.history_path.read_bytes(), history_before)

    def test_recovery_pending_is_503_without_recovery(self):
        paths = self.create_pending_transaction()
        marker_before = paths.marker_path.read_bytes()

        response = self.client.get("/api/receivables/summary")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "未収台帳の復旧確認が必要です。"},
        )
        self.assertEqual(paths.marker_path.read_bytes(), marker_before)
        self.assertTrue(paths.workspace_directory.exists())

    def test_recovery_required_is_503(self):
        paths = self.create_pending_transaction("api-required")
        persistence.mark_transaction_recovery_required(
            paths.marker_path, "manual"
        )
        marker_before = paths.marker_path.read_bytes()

        response = self.client.get("/api/receivables/summary")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(paths.marker_path.read_bytes(), marker_before)

    def test_lock_timeout_is_423(self):
        with patch.object(
            receivable_api,
            "read_receivable_current_snapshot_when_ready",
            side_effect=persistence.ReceivableLedgerLockTimeout("locked"),
        ):
            response = self.client.get("/api/receivables/summary")

        self.assertEqual(response.status_code, 423)
        self.assertEqual(
            response.json(),
            {"detail": "未収台帳をほかの処理が使用中です。"},
        )

    def test_valid_empty_current_is_200(self):
        self.write_current([])
        response = self.client.get("/api/receivables/summary")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["customer_count"], 0)
        self.assertEqual(response.json()["outstanding_count"], 0)
        self.assertEqual(response.json()["outstanding_balance"], 0)
        self.assertEqual(response.json()["customers"], [])

    def test_receivable_routes_and_models_are_in_openapi(self):
        schema = self.client.get("/openapi.json").json()
        self.assertIn("/api/receivables/summary", schema["paths"])
        self.assertIn(
            "/api/receivables/customers/detail", schema["paths"]
        )
        self.assertIn(
            "ReceivableSummaryResponse", schema["components"]["schemas"]
        )
        self.assertIn(
            "ReceivableCustomerDetailResponse",
            schema["components"]["schemas"],
        )

    def test_http_response_does_not_expose_japanese_csv_field_names(self):
        response = self.client.get(
            "/api/receivables/customers/detail",
            params={"customer_name": "A商事"},
        )
        text = json.dumps(response.json(), ensure_ascii=False)
        for field in CURRENT_RECEIVABLE_COLUMNS:
            self.assertNotIn(f'"{field}":', text)

    def test_routes_do_not_change_current_or_history_bytes(self):
        current_before = self.paths.current_path.read_bytes()
        history_before = self.paths.history_path.read_bytes()

        summary = self.client.get("/api/receivables/summary")
        detail = self.client.get(
            "/api/receivables/customers/detail",
            params={"customer_name": "A商事"},
        )

        self.assertEqual(summary.status_code, 200)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(self.paths.current_path.read_bytes(), current_before)
        self.assertEqual(self.paths.history_path.read_bytes(), history_before)

    def test_options_returns_200_and_formal_dto(self):
        response = self.client.get("/api/receivables/options")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "receipt_accounts": [
                {"code": "115", "name": "当座預金"},
                {"code": "114", "name": "普通預金"},
            ],
            "default_receipt_account": "普通預金",
        })
        self.assertNotIn("invalid_receipt_account_names", response.json())

    def test_options_without_ordinary_deposit_uses_first_valid_option(self):
        self.write_payment_accounts(["当座預金", "現金"])
        self.account_master_snapshot["accounts"].append(
            {"code": "101", "name": "現金", "category": "資産"}
        )

        body = self.client.get("/api/receivables/options").json()

        self.assertNotIn("普通預金", [item["name"] for item in body["receipt_accounts"]])
        self.assertEqual(
            body["default_receipt_account"],
            body["receipt_accounts"][0]["name"],
        )

    def test_options_zero_candidates_is_200_with_null_default(self):
        self.write_payment_accounts(["存在しない科目"])

        response = self.client.get("/api/receivables/options")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "receipt_accounts": [],
            "default_receipt_account": None,
        })

    def test_options_does_not_publish_orphan_or_ambiguous_accounts(self):
        self.write_payment_accounts(["孤立科目", "重複科目", "普通預金"])
        self.account_master_snapshot["accounts"].extend([
            {"code": "501", "name": "重複科目", "category": "費用"},
            {"code": "502", "name": "重複科目", "category": "費用"},
        ])

        body = self.client.get("/api/receivables/options").json()

        self.assertEqual(
            body["receipt_accounts"],
            [{"code": "114", "name": "普通預金"}],
        )

    def test_options_master_failure_is_safe_503(self):
        app.dependency_overrides.pop(
            receivable_api.get_receivable_account_master_snapshot
        )
        with patch.object(
            receivable_api,
            "load_journal_masters",
            side_effect=OSError("C:/secret/account_master.csv"),
        ):
            response = self.client.get("/api/receivables/options")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "マスターデータを安全に読み込めません。"},
        )
        self.assertNotIn("secret", response.text)

    def test_options_payment_source_failure_is_safe_503(self):
        with patch.object(
            receivable_api,
            "load_receipt_account_options",
            side_effect=OSError("C:/secret/payment_accounts.csv"),
        ):
            response = self.client.get("/api/receivables/options")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "未収入金科目候補を安全に読み込めません。"},
        )
        self.assertNotIn("secret", response.text)

    def test_preview_exact_returns_200(self):
        response = self.post_preview()

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["pattern"], "exact_match")
        self.assertIsNone(body["mode"])
        self.assertEqual(body["available_modes"], [])
        self.assertTrue(body["preview_complete"])

    def test_preview_shortage_none_is_partial_complete(self):
        response = self.post_preview(payment_amount=900)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["pattern"], "partial_settlement")
        self.assertEqual(body["mode"], "partial")
        self.assertEqual(body["original_difference"], -100)
        self.assertEqual(body["difference"], 0)
        self.assertEqual(
            body["available_modes"],
            ["partial", "difference_account"],
        )
        self.assertTrue(body["preview_complete"])

    def test_preview_explicit_partial_returns_200(self):
        response = self.post_preview(payment_amount=900, mode="partial")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mode"], "partial")

    def test_preview_shortage_difference_returns_200(self):
        response = self.post_preview(
            payment_amount=900,
            mode="difference_account",
            difference_account="支払手数料",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["pattern"], "shortage_difference")
        self.assertTrue(response.json()["preview_complete"])

    def test_preview_overpayment_none_returns_incomplete_200(self):
        response = self.post_preview(payment_amount=1200)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["pattern"], "overpayment")
        self.assertIsNone(body["mode"])
        self.assertEqual(body["available_modes"], ["difference_account"])
        self.assertTrue(body["difference_account_required"])
        self.assertFalse(body["preview_complete"])
        self.assertEqual(body["rows"], [])
        self.assertEqual(body["original_difference"], 200)

    def test_preview_overpayment_difference_returns_complete_200(self):
        response = self.post_preview(
            payment_amount=1200,
            mode="difference_account",
            difference_account="仮受金",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["preview_complete"])
        self.assertEqual(response.json()["rows"][-1]["credit_account"], "仮受金")

    def test_preview_revision_conflict_is_409(self):
        response = self.post_preview(ledger_revision="0" * 64)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {
            "detail": "未収データが更新されています。内容を再確認してください。"
        })

    def test_preview_missing_customer_is_404(self):
        response = self.post_preview(customer_name="B商事")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {
            "detail": "指定した取引先の未収データがありません。"
        })

    def test_preview_invalid_receipt_master_is_422(self):
        response = self.post_preview(receipt_account="存在しない科目")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json(), {
            "detail": "選択した科目を現在のマスターで確認できません。"
        })

    def test_preview_invalid_difference_master_is_422(self):
        response = self.post_preview(
            payment_amount=900,
            mode="difference_account",
            difference_account="存在しない科目",
        )
        self.assertEqual(response.status_code, 422)

    def test_preview_explicit_difference_without_account_is_422(self):
        response = self.post_preview(
            payment_amount=900,
            mode="difference_account",
        )
        self.assertEqual(response.status_code, 422)

    def test_preview_lock_timeout_is_423(self):
        with patch.object(
            receivable_api,
            "build_receivable_preview_application_result",
            side_effect=persistence.ReceivableLedgerLockTimeout("secret lock"),
        ):
            response = self.post_preview()
        self.assertEqual(response.status_code, 423)
        self.assertNotIn("secret", response.text)

    def test_preview_recovery_pending_is_503(self):
        paths = self.create_pending_transaction("preview-pending")
        marker_before = paths.marker_path.read_bytes()

        response = self.post_preview()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(paths.marker_path.read_bytes(), marker_before)

    def test_preview_recovery_required_is_503(self):
        paths = self.create_pending_transaction("preview-required")
        persistence.mark_transaction_recovery_required(paths.marker_path, "manual")
        marker_before = paths.marker_path.read_bytes()

        response = self.post_preview()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(paths.marker_path.read_bytes(), marker_before)

    def test_preview_settlement_unavailable_is_503(self):
        self.paths.history_path.write_bytes(b"\xff\xfeinvalid-history")
        response = self.post_preview()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {
            "detail": "未収台帳の復旧確認が必要です。"
        })

    def test_preview_current_missing_is_503(self):
        self.paths.current_path.unlink()
        response = self.post_preview()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {
            "detail": "未収台帳を安全に読み込めません。"
        })

    def test_preview_application_errors_do_not_expose_raw_detail(self):
        cases = [
            (ReceivablePreviewValidationError("C:/secret/input"), 422),
            (ReceivablePreviewCustomerNotFoundError("secret"), 404),
            (ReceivableSettlementMasterValidationError("secret"), 422),
            (RuntimeError("C:/secret/internal"), 500),
        ]
        for error, status in cases:
            with self.subTest(error=type(error).__name__), patch.object(
                receivable_api,
                "build_receivable_preview_application_result",
                side_effect=error,
            ):
                response = self.post_preview()
            self.assertEqual(response.status_code, status)
            self.assertNotIn("secret", response.text)

    def test_preview_request_rejects_extra_field(self):
        response = self.client.post(
            "/api/receivables/preview-settlement",
            json={**self.preview_payload(), "unexpected": True},
        )
        self.assertEqual(response.status_code, 422)

    def test_preview_request_rejects_invalid_revision(self):
        for revision in ("A" * 64, "a" * 63, "not-a-hash"):
            with self.subTest(revision=revision):
                response = self.post_preview(ledger_revision=revision)
                self.assertEqual(response.status_code, 422)

    def test_preview_request_rejects_nonpositive_bool_and_decimal_amount(self):
        for amount in (0, -1, True, 1.5):
            with self.subTest(amount=amount):
                response = self.post_preview(payment_amount=amount)
                self.assertEqual(response.status_code, 422)

    def test_preview_request_rejects_invalid_mode_and_blank_required_text(self):
        for overrides in (
            {"mode": "差額を科目で処理する"},
            {"customer_name": "   "},
            {"receipt_account": "   "},
        ):
            with self.subTest(overrides=overrides):
                response = self.post_preview(**overrides)
                self.assertEqual(response.status_code, 422)

    def test_preview_route_passes_settlement_date_as_iso_string(self):
        formal_result = self.post_preview().json()
        with patch.object(
            receivable_api,
            "build_receivable_preview_application_result",
            return_value=formal_result,
        ) as service:
            response = self.post_preview(settlement_date="2026-09-01")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(service.call_args.kwargs["settlement_date"], "2026-09-01")

    def test_preview_response_dto_has_exact_fields_and_json_types(self):
        response = self.post_preview(payment_amount=900)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body["source_candidates"][0]), {
            "code", "receivable_id", "billing_date", "billed_amount",
            "balance", "scheduled_amount", "receivable_account",
            "receivable_sub_account", "department", "customer_name", "summary",
        })
        self.assertEqual(set(body["rows"][0]), {
            "debit_account", "credit_account", "credit_sub_account",
            "department", "amount", "summary",
        })
        self.assertEqual(
            set(body["recommended_difference_accounts"][0]),
            {"code", "name"},
        )
        self.assertIs(type(body["source_candidates"][0]["balance"]), int)
        self.assertIs(type(body["rows"][0]["amount"]), int)
        json.dumps(body, ensure_ascii=False, allow_nan=False)
        text = json.dumps(body, ensure_ascii=False)
        for field in CURRENT_RECEIVABLE_COLUMNS:
            self.assertNotIn(f'"{field}":', text)

    def test_options_and_preview_contracts_are_in_openapi(self):
        schema = self.client.get("/openapi.json").json()
        self.assertIn("/api/receivables/options", schema["paths"])
        self.assertIn("/api/receivables/preview-settlement", schema["paths"])
        schemas = schema["components"]["schemas"]
        request = schemas["ReceivablePreviewRequest"]
        self.assertFalse(request["additionalProperties"])
        self.assertEqual(
            set(request["required"]),
            {"ledger_revision", "customer_name", "settlement_date", "payment_amount", "receipt_account"},
        )
        serialized = json.dumps(schemas, ensure_ascii=False)
        for value in (
            "partial", "difference_account", "exact_match",
            "partial_settlement", "shortage_difference", "overpayment",
        ):
            self.assertIn(f'"{value}"', serialized)

    def test_preview_route_is_read_only_and_uses_only_fixture_data(self):
        transactions_path = self.directory / "transactions.csv"
        transactions_path.write_bytes(b"fixture-transactions\r\n")
        before = {
            self.paths.current_path: self.paths.current_path.read_bytes(),
            self.paths.history_path: self.paths.history_path.read_bytes(),
            transactions_path: transactions_path.read_bytes(),
        }

        response = self.post_preview(payment_amount=900)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {path: path.read_bytes() for path in before},
            before,
        )
        self.assertFalse((self.directory / ".transactions").exists())
        self.assertFalse((self.directory / ".settlements").exists())

    def test_preview_missing_history_succeeds_without_generation(self):
        self.paths.history_path.unlink()
        response = self.post_preview()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.paths.history_path.exists())


if __name__ == "__main__":
    unittest.main()

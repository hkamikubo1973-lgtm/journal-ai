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
from api.journal import app  # noqa: E402
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
        app.dependency_overrides[
            receivable_api.get_receivables_directory
        ] = lambda: self.directory
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


if __name__ == "__main__":
    unittest.main()

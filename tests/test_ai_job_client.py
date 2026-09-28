"""The queued AI Job integration uses only a mocked HTTP transport."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import api.ai as ai_api  # noqa: E402
from ai_job_application_service import (  # noqa: E402
    AIJobApplicationService,
    AIJobExecutionModeNotAllowed,
    AIJobFailed,
    AIJobProfileNotAllowed,
)
from ai_job_client import (  # noqa: E402
    AIJobClient,
    AIJobConfigurationError,
    AIJobConnectionError,
    AIJobHTTPError,
    AIJobMalformedResponseError,
    AIJobMissingJobIdError,
    AIJobRequestError,
    AIJobTimeoutError,
    AIJobUnknownStatusError,
    normalize_ai_server_base_url,
)
from api.journal import app  # noqa: E402


BASE_URL = "http://ai-server.example:8000"


def client_for(handler, *, base_url=BASE_URL):
    return AIJobClient(base_url, transport=httpx.MockTransport(handler))


class AIJobClientTest(unittest.TestCase):
    def test_base_url_normalizes_whitespace_and_trailing_slashes(self):
        self.assertEqual(
            normalize_ai_server_base_url(f"  {BASE_URL}///  "), BASE_URL,
        )

    def test_existing_url_environment_variable_is_used_at_construction(self):
        with patch.dict(os.environ, {"JOURNAL_AI_SERVER_URL": BASE_URL + "/"}):
            self.assertEqual(AIJobClient().base_url, BASE_URL)

    def test_missing_invalid_and_8080_urls_are_rejected(self):
        for value in ("", "localhost:8000", "http://localhost:8080",
                      "http://localhost:8000/ai", "http://user:pass@localhost:8000",
                      "http://localhost:8000?token=secret"):
            with self.subTest(value=value), self.assertRaises(AIJobConfigurationError):
                normalize_ai_server_base_url(value)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(AIJobConfigurationError):
                AIJobClient()

    def test_timeout_configuration_must_be_finite_and_positive(self):
        for value in (0, -1, float("nan"), float("inf"), True):
            with self.subTest(value=value), self.assertRaises(AIJobConfigurationError):
                AIJobClient(BASE_URL, timeout_seconds=value)

    def test_submit_job_posts_only_contract_fields_and_returns_job_id(self):
        calls = []

        def handle(request):
            calls.append(request)
            return httpx.Response(202, json={"job_id": "job-123", "status": "QUEUED"})

        payload = {"context": {"source": "journal"}, "question": "候補を説明"}
        result = client_for(handle).submit_job(
            profile="journal_normal", execution_mode="interactive", payload=payload,
        )
        self.assertEqual(result["job_id"], "job-123")
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0].method, str(calls[0].url)),
                         ("POST", BASE_URL + "/ai/jobs"))
        self.assertEqual(json.loads(calls[0].content), {
            "profile": "journal_normal", "execution_mode": "interactive",
            "payload": payload,
        })
        self.assertNotIn("model", json.loads(calls[0].content))

    def test_submit_does_not_wait_or_poll_for_completion(self):
        calls = []

        def handle(request):
            calls.append(request.url.path)
            return httpx.Response(202, json={"job_id": "later"})

        self.assertEqual(client_for(handle).submit_job(
            profile="journal_normal", execution_mode="interactive", payload={},
        )["job_id"], "later")
        self.assertEqual(calls, ["/ai/jobs"])

    def test_submitted_job_id_must_be_a_nonempty_string(self):
        for response in ({}, {"job_id": ""}, {"job_id": 123}):
            with self.subTest(response=response):
                client = client_for(lambda request: httpx.Response(202, json=response))
                with self.assertRaises(AIJobMissingJobIdError):
                    client.submit_job(
                        profile="journal_normal", execution_mode="interactive",
                        payload={},
                    )

    def test_request_shape_is_checked_before_http(self):
        client = client_for(lambda request: self.fail("unexpected HTTP request"))
        with self.assertRaises(AIJobRequestError):
            client.submit_job(profile="", execution_mode="interactive", payload={})
        with self.assertRaises(AIJobRequestError):
            client.submit_job(profile="journal_normal", execution_mode="", payload={})
        with self.assertRaises(AIJobRequestError):
            client.submit_job(profile="journal_normal", execution_mode="interactive",
                              payload=None)
        with self.assertRaises(AIJobRequestError):
            client.get_job("")

    def test_get_job_keeps_each_documented_status_unchanged(self):
        for status in ("QUEUED", "RUNNING", "COMPLETED", "FAILED"):
            with self.subTest(status=status):
                calls = []

                def handle(request):
                    calls.append((request.method, str(request.url)))
                    return httpx.Response(200, json={"status": status, "extra": 1})

                result = client_for(handle).get_job("job-123")
                self.assertEqual(result, {"status": status, "extra": 1})
                self.assertEqual(calls, [("GET", BASE_URL + "/ai/jobs/job-123")])

    def test_job_id_is_encoded_as_one_path_component(self):
        paths = []

        def handle(request):
            paths.append(request.url.raw_path)
            return httpx.Response(200, json={"status": "QUEUED"})

        client_for(handle).get_job("id?part=1")
        self.assertEqual(paths, [b"/ai/jobs/id%3Fpart%3D1"])

    def test_unknown_or_missing_job_status_is_rejected(self):
        for status in ("PENDING", "done", None, []):
            with self.subTest(status=status):
                client = client_for(lambda request: httpx.Response(200, json={"status": status}))
                with self.assertRaises(AIJobUnknownStatusError):
                    client.get_job("job-123")

    def test_status_endpoint_returns_server_object_without_model_interpretation(self):
        calls = []
        status = {"server": "ready", "model_state": {"value": "server-owned"}}

        def handle(request):
            calls.append((request.method, str(request.url)))
            return httpx.Response(200, json=status)

        self.assertEqual(client_for(handle).get_status(), status)
        self.assertEqual(calls, [("GET", BASE_URL + "/ai/status")])

    def test_status_disables_environment_proxy_use(self):
        original_client = httpx.Client
        with patch.object(httpx, "Client", wraps=original_client) as factory:
            result = client_for(
                lambda request: httpx.Response(200, json={"state": "READY"}),
            ).get_status()
        self.assertEqual(result, {"state": "READY"})
        self.assertIs(factory.call_args.kwargs["trust_env"], False)

    def test_connection_error_and_timeout_are_distinct(self):
        errors = (
            (httpx.ConnectError("SECRET_URL"), AIJobConnectionError),
            (httpx.ReadTimeout("SECRET_URL"), AIJobTimeoutError),
        )
        for upstream, expected in errors:
            with self.subTest(expected=expected):
                def handle(request):
                    raise upstream

                with self.assertRaises(expected) as caught:
                    client_for(handle).get_status()
                self.assertNotIn("SECRET_URL", str(caught.exception))

    def test_non_2xx_response_is_distinct_without_exposing_body(self):
        client = client_for(lambda request: httpx.Response(
            503, text="SECRET_INTERNAL_PATH",
        ))
        with self.assertRaises(AIJobHTTPError) as caught:
            client.get_status()
        self.assertEqual(caught.exception.status_code, 503)
        self.assertNotIn("SECRET_INTERNAL_PATH", str(caught.exception))

    def test_malformed_json_and_non_object_response_are_rejected(self):
        responses = (httpx.Response(200, text="not-json"),
                     httpx.Response(200, json=["not", "object"]))
        for response in responses:
            with self.subTest(response=response.text):
                with self.assertRaises(AIJobMalformedResponseError):
                    client_for(lambda request: response).get_status()


class AIJobApplicationServiceTest(unittest.TestCase):
    def test_only_journal_normal_interactive_is_submitted(self):
        calls = []

        def handle(request):
            calls.append(json.loads(request.content))
            return httpx.Response(202, json={"job_id": "one"})

        service = AIJobApplicationService(client_for(handle))
        self.assertEqual(service.submit_job(
            profile="journal_normal", execution_mode="interactive", payload={"x": 1},
        )["job_id"], "one")
        self.assertEqual(calls, [{"profile": "journal_normal",
                                  "execution_mode": "interactive", "payload": {"x": 1}}])

    def test_other_profiles_are_rejected_before_http(self):
        service = AIJobApplicationService(
            client_for(lambda request: self.fail("unexpected HTTP request")),
        )
        for profile in ("journal_vision", "journal_exception", "journal_web",
                        "driver_work", "stock_analysis", "private_chat", "4B", []):
            with self.subTest(profile=profile), self.assertRaises(AIJobProfileNotAllowed):
                service.submit_job(profile=profile, execution_mode="interactive", payload={})

    def test_unapproved_execution_modes_are_rejected_before_http(self):
        service = AIJobApplicationService(
            client_for(lambda request: self.fail("unexpected HTTP request")),
        )
        for mode in ("realtime", "background", "not-a-mode", []):
            with self.subTest(mode=mode), self.assertRaises(AIJobExecutionModeNotAllowed):
                service.submit_job(profile="journal_normal", execution_mode=mode,
                                   payload={})

    def test_failed_job_has_a_distinct_error_with_original_status(self):
        service = AIJobApplicationService(client_for(
            lambda request: httpx.Response(200, json={"status": "FAILED", "detail": "x"}),
        ))
        with self.assertRaises(AIJobFailed) as caught:
            service.get_job("job-1")
        self.assertEqual(caught.exception.job["status"], "FAILED")
        self.assertEqual(str(caught.exception), "AI Job failed")

    def test_running_job_is_returned_without_waiting(self):
        service = AIJobApplicationService(client_for(
            lambda request: httpx.Response(200, json={"status": "RUNNING"}),
        ))
        self.assertEqual(service.get_job("job-1")["status"], "RUNNING")


class AIStatusApiTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(app.dependency_overrides.clear)
        self.api = TestClient(app)

    def use_client(self, handler):
        app.dependency_overrides[ai_api.get_ai_job_service] = lambda: (
            AIJobApplicationService(client_for(handler))
        )

    def test_status_api_is_read_only_and_returns_upstream_object(self):
        calls = []

        def handle(request):
            calls.append(request.method + " " + request.url.path)
            return httpx.Response(200, json={"status": "ok", "other": {"x": 1}})

        self.use_client(handle)
        response = self.api.get("/api/ai/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "other": {"x": 1}})
        self.assertEqual(calls, ["GET /ai/status"])

    def test_missing_configuration_is_safe(self):
        with patch.dict(os.environ, {}, clear=True):
            response = self.api.get("/api/ai/status")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("JOURNAL_AI_SERVER_URL", response.text)

    def test_connection_timeout_http_and_json_errors_are_safe(self):
        cases = (
            (lambda request: (_ for _ in ()).throw(httpx.ConnectError("SECRET_URL")), 503),
            (lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("SECRET_URL")), 504),
            (lambda request: httpx.Response(502, text="SECRET_URL"), 502),
            (lambda request: httpx.Response(200, text="SECRET_URL"), 502),
        )
        for handler, expected in cases:
            with self.subTest(expected=expected):
                self.use_client(handler)
                response = self.api.get("/api/ai/status")
                self.assertEqual(response.status_code, expected)
                self.assertNotIn("SECRET_URL", response.text)

    def test_no_public_job_submission_api_exists_yet(self):
        self.assertEqual(self.api.post("/api/ai/jobs", json={}).status_code, 404)


if __name__ == "__main__":
    unittest.main()

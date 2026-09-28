"""Small HTTP client for the AI Server's queued Job API.

The client knows neither journal contexts nor the server's model policy.
"""

from __future__ import annotations

import os
import math
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx


AI_SERVER_URL_ENV = "JOURNAL_AI_SERVER_URL"
DEFAULT_TIMEOUT_SECONDS = 30.0
JOB_STATES = frozenset({"QUEUED", "RUNNING", "COMPLETED", "FAILED"})


class AIJobClientError(Exception):
    """An AI Job API request could not be completed safely."""


class AIJobConfigurationError(AIJobClientError):
    """The existing AI Server URL is missing or invalid."""


class AIJobRequestError(AIJobClientError):
    """The caller supplied an invalid Job request or identifier."""


class AIJobConnectionError(AIJobClientError):
    """The AI Server could not be reached."""


class AIJobTimeoutError(AIJobClientError):
    """The AI Server request timed out."""


class AIJobHTTPError(AIJobClientError):
    """The AI Server returned a non-success HTTP response."""

    def __init__(self, status_code: int):
        super().__init__("AI Server returned an unsuccessful response")
        self.status_code = status_code


class AIJobMalformedResponseError(AIJobClientError):
    """The AI Server response is not a JSON object."""


class AIJobMissingJobIdError(AIJobClientError):
    """A Job response did not contain a usable job_id."""


class AIJobUnknownStatusError(AIJobClientError):
    """A Job response used a state outside the documented Job states."""


def normalize_ai_server_base_url(value: str) -> str:
    """Accept only an explicit FastAPI port-8000 base URL, not an API path."""

    if not isinstance(value, str) or not value.strip():
        raise AIJobConfigurationError("AI Server URL is not configured")
    try:
        parts = urlsplit(value.strip())
        valid = (
            parts.scheme in {"http", "https"}
            and bool(parts.hostname)
            and parts.port == 8000
            and parts.username is None
            and parts.password is None
            and not parts.path.strip("/")
            and not parts.query
            and not parts.fragment
        )
    except ValueError as error:
        raise AIJobConfigurationError("AI Server URL is invalid") from error
    if not valid:
        raise AIJobConfigurationError("AI Server URL is invalid")
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


class AIJobClient:
    """Submit and inspect Jobs without waiting for their completion."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        configured = (
            os.environ.get(AI_SERVER_URL_ENV, "")
            if base_url is None else base_url
        )
        self.base_url = normalize_ai_server_base_url(configured)
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise AIJobConfigurationError("AI Server timeout is invalid")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def _request(
        self, method: str, path: str, *, body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            with httpx.Client(
                transport=self.transport,
                timeout=self.timeout_seconds,
                trust_env=False,
            ) as client:
                response = client.request(
                    method, f"{self.base_url}{path}", json=body,
                )
        except httpx.TimeoutException as error:
            raise AIJobTimeoutError("AI Server request timed out") from error
        except httpx.RequestError as error:
            raise AIJobConnectionError("AI Server is unavailable") from error

        if not 200 <= response.status_code < 300:
            raise AIJobHTTPError(response.status_code)
        try:
            data = response.json()
        except ValueError as error:
            raise AIJobMalformedResponseError("AI Server response is invalid") from error
        if not isinstance(data, dict):
            raise AIJobMalformedResponseError("AI Server response is invalid")
        return data

    @staticmethod
    def _validate_job_response(response: dict[str, Any]) -> dict[str, Any]:
        job = response.get("job")
        if not isinstance(job, dict):
            raise AIJobMalformedResponseError("AI Server Job response is invalid")
        job_id = job.get("job_id")
        if not isinstance(job_id, str) or not job_id.strip():
            raise AIJobMissingJobIdError("AI Server did not return a job_id")
        state = job.get("state")
        if not isinstance(state, str) or state not in JOB_STATES:
            raise AIJobUnknownStatusError("AI Server returned an unknown Job state")
        result = job.get("result")
        if result is not None and not isinstance(result, dict):
            raise AIJobMalformedResponseError("AI Server Job result is invalid")
        if state == "COMPLETED" and (
            not isinstance(result, dict)
            or not isinstance(result.get("content"), str)
        ):
            raise AIJobMalformedResponseError("AI Server Job result is invalid")
        return job

    def submit_job(
        self,
        *,
        profile: str,
        execution_mode: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Submit once and return the server response; never poll here."""

        if not isinstance(profile, str) or not profile.strip():
            raise AIJobRequestError("AI Job profile is invalid")
        if not isinstance(execution_mode, str) or not execution_mode.strip():
            raise AIJobRequestError("AI Job execution mode is invalid")
        if not isinstance(payload, dict):
            raise AIJobRequestError("AI Job payload is invalid")
        result = self._request("POST", "/ai/jobs", body={
            "profile": profile,
            "execution_mode": execution_mode,
            "payload": payload,
        })
        self._validate_job_response(result)
        return result

    def get_job(self, job_id: str) -> dict[str, Any]:
        if not isinstance(job_id, str) or not job_id.strip():
            raise AIJobRequestError("AI Job identifier is invalid")
        result = self._request("GET", f"/ai/jobs/{quote(job_id, safe='')}")
        self._validate_job_response(result)
        return result

    def get_status(self) -> dict[str, Any]:
        """Return the server's status object without interpreting model state."""

        return self._request("GET", "/ai/status")

"""Journal-ai's permitted use of the common AI Job client."""

from __future__ import annotations

from typing import Any

from ai_job_client import AIJobClient


ALLOWED_PROFILES = frozenset({"journal_normal"})
ALLOWED_EXECUTION_MODES = frozenset({"interactive"})


class AIJobServiceError(Exception):
    """Journal-ai rejected or could not use a Job result."""


class AIJobProfileNotAllowed(AIJobServiceError):
    """This application is not allowed to submit the requested Profile."""


class AIJobExecutionModeNotAllowed(AIJobServiceError):
    """This application does not yet use the requested execution mode."""


class AIJobFailed(AIJobServiceError):
    """The AI Server reported a FAILED Job; its Job object is retained."""

    def __init__(self, job: dict[str, Any]):
        super().__init__("AI Job failed")
        self.job = job


class AIJobApplicationService:
    def __init__(self, client: AIJobClient | None = None) -> None:
        self._client = client

    @property
    def client(self) -> AIJobClient:
        if self._client is None:
            self._client = AIJobClient()
        return self._client

    def submit_job(
        self,
        *,
        profile: str,
        execution_mode: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(profile, str) or profile not in ALLOWED_PROFILES:
            raise AIJobProfileNotAllowed("AI Job Profile is not available")
        if (
            not isinstance(execution_mode, str)
            or execution_mode not in ALLOWED_EXECUTION_MODES
        ):
            raise AIJobExecutionModeNotAllowed("AI Job execution mode is not available")
        return self.client.submit_job(
            profile=profile,
            execution_mode=execution_mode,
            payload=payload,
        )

    def get_job(self, job_id: str) -> dict[str, Any]:
        response = self.client.get_job(job_id)
        job = response["job"]
        if job["state"] == "FAILED":
            raise AIJobFailed(job)
        return response

    def get_status(self) -> dict[str, Any]:
        return self.client.get_status()

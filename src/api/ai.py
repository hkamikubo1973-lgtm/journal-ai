"""Read-only connection status for the shared AI Server."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ai_job_application_service import AIJobApplicationService
from ai_job_client import (
    AIJobConfigurationError,
    AIJobConnectionError,
    AIJobHTTPError,
    AIJobMalformedResponseError,
    AIJobTimeoutError,
)


router = APIRouter(prefix="/api/ai", tags=["ai"])


def get_ai_job_service() -> AIJobApplicationService:
    return AIJobApplicationService()


@router.get("/status")
def get_ai_status(service: AIJobApplicationService = Depends(get_ai_job_service)):
    try:
        return service.get_status()
    except AIJobConfigurationError as error:
        raise HTTPException(status_code=503, detail="AIサーバーの接続先が設定されていません。") from error
    except AIJobConnectionError as error:
        raise HTTPException(status_code=503, detail="AIサーバーに接続できません。") from error
    except AIJobTimeoutError as error:
        raise HTTPException(status_code=504, detail="AIサーバーの応答がタイムアウトしました。") from error
    except (AIJobHTTPError, AIJobMalformedResponseError) as error:
        raise HTTPException(status_code=502, detail="AIサーバーの状態を取得できません。") from error

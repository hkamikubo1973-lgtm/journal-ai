"""Web access to the existing export base-folder setting."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from system_settings import (
    OutputFolderPersistenceError,
    OutputFolderSettingsError,
    load_system_settings,
    save_output_folder,
)


router = APIRouter(prefix="/api/settings/output", tags=["settings"])


class OutputSettingsResponse(BaseModel):
    csv_export_dir: str


class OutputSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    csv_export_dir: str


@router.get("", response_model=OutputSettingsResponse)
def get_output_settings():
    return {"csv_export_dir": load_system_settings()["csv_export_dir"]}


@router.put("", response_model=OutputSettingsResponse)
def put_output_settings(request: OutputSettingsUpdate):
    try:
        folder = save_output_folder(request.csv_export_dir)
    except OutputFolderPersistenceError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except OutputFolderSettingsError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"csv_export_dir": folder}

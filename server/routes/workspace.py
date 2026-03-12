"""Workspace routes (migration bridge)."""

from fastapi import APIRouter, File, UploadFile

from router import (
    upload_file as legacy_upload_file,
    download_file as legacy_download_file,
)

router = APIRouter(tags=["Workspace"])


@router.post("/workspace/upload")
async def upload_file(file: UploadFile = File(...)):
    return await legacy_upload_file(file)


@router.get("/workspace/download/{filename}")
def download_file(filename: str):
    return legacy_download_file(filename)


"""任务相关路由。"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import JobResponse
from app.services.jobs import create_job, get_job_or_404, job_payload
from app.storage import StorageError, get_storage

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.post("", response_model=JobResponse, status_code=201)
async def create(
    file: UploadFile = File(..., description="PPTX 演示文稿，最大 50 MB"),
    voice: str = Form("female", description="female | male | warm"),
    speed: float = Form(1.0, description="语速，0.75 ~ 1.25"),
    db: Session = Depends(get_db),
) -> dict:
    job = await create_job(db, file=file, voice=voice, speed=speed)
    return job_payload(job)


@router.get("/{job_id}", response_model=JobResponse)
def get(job_id: str, db: Session = Depends(get_db)) -> dict:
    job = get_job_or_404(db, job_id)
    return job_payload(job)


@router.get("/{job_id}/audio")
def get_audio(
    job_id: str,
    download: bool = Query(False, description="为 true 时以附件形式下载"),
    db: Session = Depends(get_db),
) -> FileResponse:
    job = get_job_or_404(db, job_id)
    if job.status != "completed" or not job.audio_name:
        raise HTTPException(status_code=404, detail="音频尚未生成")

    storage = get_storage()
    try:
        path = storage.audio_path(job.id, job.audio_name)
    except StorageError as exc:
        raise HTTPException(status_code=404, detail="音频文件不存在") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="音频文件不存在")

    media_type = job.content_type or mimetypes.guess_type(path.name)[0] or "audio/mpeg"
    stem = Path(job.filename).stem or "transcript"
    filename = f"{stem}{Path(job.audio_name).suffix or '.mp3'}"
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        content_disposition_type="attachment" if download else "inline",
    )

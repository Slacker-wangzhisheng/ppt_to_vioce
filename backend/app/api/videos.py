"""数字人视频相关路由。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Job, JobStatus
from app.schemas import VideoJobResponse
from app.services.jobs import get_job_or_404
from app.services.video_jobs import (
    create_video_job,
    get_video_job_or_404,
    video_job_payload,
)
from app.storage import StorageError, get_storage

router = APIRouter(prefix="/api", tags=["videos"])


@router.post(
    "/jobs/{audio_job_id}/videos",
    response_model=VideoJobResponse,
    status_code=201,
)
async def create(
    audio_job_id: str,
    avatar: UploadFile = File(..., description="数字人图片，PNG/JPG，最大 10 MB"),
    db: Session = Depends(get_db),
) -> dict:
    audio_job = get_job_or_404(db, audio_job_id)
    video = await create_video_job(db, audio_job=audio_job, avatar=avatar)
    return video_job_payload(video)


@router.get("/video-jobs/{video_job_id}", response_model=VideoJobResponse)
def get(video_job_id: str, db: Session = Depends(get_db)) -> dict:
    video = get_video_job_or_404(db, video_job_id)
    return video_job_payload(video)


@router.get("/video-jobs/{video_job_id}/video")
def get_video(
    video_job_id: str,
    download: bool = Query(False, description="为 true 时以附件形式下载"),
    db: Session = Depends(get_db),
) -> FileResponse:
    video = get_video_job_or_404(db, video_job_id)
    if video.status != JobStatus.COMPLETED or not video.video_path:
        raise HTTPException(status_code=409, detail="视频尚未生成完成")

    storage = get_storage()
    try:
        path = storage.video_path(video.audio_job_id, video.video_path)
    except StorageError as exc:
        raise HTTPException(status_code=404, detail="视频文件不存在") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="视频文件不存在")

    audio_job = db.get(Job, video.audio_job_id)
    stem = Path(audio_job.filename).stem if audio_job else ""
    filename = f"{stem or '口播'}-数字人.mp4"

    return FileResponse(
        path,
        media_type="video/mp4",
        filename=filename,
        content_disposition_type="attachment" if download else "inline",
    )

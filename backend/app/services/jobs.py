"""任务业务逻辑：上传校验、入库、序列化。"""

from __future__ import annotations

import logging
import uuid
import zipfile
from pathlib import Path

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Job, JobStatus
from app.storage import StorageError, get_storage, sanitize_filename

logger = logging.getLogger(__name__)

VOICES = ("female", "male", "warm")
SPEED_MIN = 0.75
SPEED_MAX = 1.25
CHUNK_SIZE = 1024 * 1024
PPTX_SUFFIX = ".pptx"


def validate_voice(voice: str) -> str:
    if voice not in VOICES:
        raise HTTPException(status_code=422, detail="不支持的音色，请选择 female、male 或 warm")
    return voice


def validate_speed(speed: float) -> float:
    try:
        value = float(speed)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="语速必须是数字") from exc
    if not SPEED_MIN <= value <= SPEED_MAX:
        raise HTTPException(status_code=422, detail="语速需要在 0.75 到 1.25 之间")
    return round(value, 2)


def validate_pptx(path: Path) -> None:
    """校验 ZIP 结构和 PPTX 必要成员，拒绝伪造文件。"""
    settings = get_settings()
    if not zipfile.is_zipfile(path):
        raise HTTPException(status_code=422, detail="文件不是有效的 PPTX")

    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if "ppt/presentation.xml" not in names:
                raise HTTPException(status_code=422, detail="文件不是有效的 PPTX")
            if len(names) > settings.max_pptx_entries:
                raise HTTPException(status_code=422, detail="PPTX 内部文件数量异常")
            uncompressed = sum(info.file_size for info in archive.infolist())
            if uncompressed > settings.max_pptx_uncompressed_bytes:
                raise HTTPException(status_code=422, detail="PPTX 解压后体积异常")
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=422, detail="文件不是有效的 PPTX") from exc


async def create_job(
    db: Session,
    *,
    file: UploadFile,
    voice: str,
    speed: float,
) -> Job:
    settings = get_settings()
    job_id = str(uuid.uuid4())
    storage = None

    try:
        filename = sanitize_filename(file.filename)
        if not filename.lower().endswith(PPTX_SUFFIX):
            raise HTTPException(status_code=422, detail="目前仅支持 .pptx 文件")

        voice = validate_voice(voice)
        speed = validate_speed(speed)

        try:
            storage = get_storage()
            target = storage.source_path(job_id)
        except StorageError as exc:
            logger.error("创建任务目录失败：%s", exc)
            raise HTTPException(
                status_code=500, detail="文件存储不可用，请稍后重试"
            ) from exc

        size = 0
        with target.open("wb") as output:
            while chunk := await file.read(CHUNK_SIZE):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件不能超过 {settings.upload_limit_mb} MB",
                    )
                output.write(chunk)

        if size == 0:
            raise HTTPException(status_code=422, detail="上传的文件为空")

        validate_pptx(target)

        job = Job(
            id=job_id,
            filename=filename,
            status=JobStatus.QUEUED,
            voice=voice,
            speed=speed,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        logger.info("创建任务 job=%s size=%s voice=%s speed=%s", job_id, size, voice, speed)
        return job
    except BaseException:
        db.rollback()
        _cleanup(storage, job_id)
        raise
    finally:
        await file.close()


def _cleanup(storage, job_id: str) -> None:
    if storage is None:
        return
    try:
        storage.remove_job(job_id)
    except (StorageError, OSError):
        logger.warning("清理失败的任务目录出错 job=%s", job_id)


def get_job_or_404(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


def job_payload(job: Job) -> dict:
    completed = job.status == JobStatus.COMPLETED and bool(job.audio_name)
    audio_url = f"/api/jobs/{job.id}/audio" if completed else None
    return {
        "id": job.id,
        "filename": job.filename,
        "status": job.status,
        "voice": job.voice,
        "speed": float(job.speed),
        "transcript": job.transcript,
        "error": job.error,
        "audio_name": job.audio_name,
        "audio_url": audio_url,
        "download_url": f"{audio_url}?download=1" if audio_url else None,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }

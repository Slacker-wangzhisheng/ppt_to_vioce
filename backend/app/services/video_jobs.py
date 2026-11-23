"""数字人视频任务业务逻辑：图片校验、入库、序列化。"""

from __future__ import annotations

import logging
import uuid
from io import BytesIO
from pathlib import Path

from fastapi import HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Job, JobStatus, VideoJob
from app.storage import StorageError, get_storage, sanitize_filename

logger = logging.getLogger(__name__)

AVATAR_SUFFIXES = {".png", ".jpg", ".jpeg"}
AVATAR_CONTENT_TYPES = {"image/png", "image/jpeg", "image/jpg"}
CHUNK_SIZE = 1024 * 1024


def sniff_image(data: bytes) -> tuple[str, str] | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    return None


def validate_avatar_meta(filename: str, content_type: str | None) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in AVATAR_SUFFIXES:
        raise HTTPException(status_code=422, detail="请上传 PNG、JPG 或 JPEG 格式的图片")
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    if normalized and normalized not in AVATAR_CONTENT_TYPES:
        raise HTTPException(status_code=422, detail="图片类型不支持，请上传 PNG 或 JPG")
    return suffix


def validate_image_data(data: bytes) -> None:
    """完整解码图片，拒绝损坏文件与超大尺寸，避免 FFmpeg 卡死。"""
    settings = get_settings()
    try:
        with Image.open(BytesIO(data)) as image:
            image.verify()
        with Image.open(BytesIO(data)) as image:
            if image.width * image.height > settings.avatar_max_pixels:
                raise HTTPException(status_code=422, detail="图片尺寸过大，请换一张图片")
            image.load()
    except HTTPException:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
        Image.DecompressionBombError,
    ) as exc:
        raise HTTPException(
            status_code=422, detail="图片文件已损坏或无法解析，请重新上传"
        ) from exc


async def create_video_job(
    db: Session, *, audio_job: Job, avatar: UploadFile
) -> VideoJob:
    settings = get_settings()
    if audio_job.status != JobStatus.COMPLETED or not audio_job.audio_name:
        raise HTTPException(status_code=409, detail="口播音频尚未生成完成，请稍后再试")

    filename = sanitize_filename(avatar.filename, fallback="avatar.png")
    suffix = validate_avatar_meta(filename, avatar.content_type)

    video_id = str(uuid.uuid4())
    data = bytearray()
    try:
        while chunk := await avatar.read(CHUNK_SIZE):
            data.extend(chunk)
            if len(data) > settings.avatar_max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"图片不能超过 {settings.avatar_limit_mb} MB",
                )
    finally:
        await avatar.close()

    if not data:
        raise HTTPException(status_code=422, detail="图片内容为空，请重新选择")

    sniffed = sniff_image(bytes(data))
    if sniffed is None:
        raise HTTPException(status_code=422, detail="图片内容无效，请上传 PNG 或 JPG")
    sniffed_suffix, _ = sniffed
    if (sniffed_suffix == ".png") != (suffix == ".png"):
        raise HTTPException(status_code=422, detail="图片内容与扩展名不一致")
    validate_image_data(bytes(data))

    storage = get_storage()
    try:
        avatar_stored, _ = storage.save_avatar(
            audio_job.id, video_id, bytes(data), sniffed_suffix
        )
    except (StorageError, OSError) as exc:
        logger.error("保存数字人图片失败 audio_job=%s：%s", audio_job.id, exc)
        raise HTTPException(status_code=500, detail="图片保存失败，请稍后重试") from exc

    video = VideoJob(
        id=video_id,
        audio_job_id=audio_job.id,
        avatar_name=filename,
        avatar_path=avatar_stored,
        status=JobStatus.QUEUED,
        progress=0,
    )
    try:
        db.add(video)
        db.commit()
        db.refresh(video)
    except Exception:
        db.rollback()
        storage.remove_avatar(audio_job.id, avatar_stored)
        logger.exception("创建视频任务失败 audio_job=%s", audio_job.id)
        raise HTTPException(status_code=500, detail="创建视频任务失败，请稍后重试")

    logger.info(
        "创建视频任务 video=%s audio_job=%s size=%s",
        video.id,
        audio_job.id,
        len(data),
    )
    return video


def get_video_job_or_404(db: Session, video_job_id: str) -> VideoJob:
    video = db.get(VideoJob, video_job_id)
    if not video:
        raise HTTPException(status_code=404, detail="视频任务不存在")
    return video


def video_job_payload(video: VideoJob) -> dict:
    completed = video.status == JobStatus.COMPLETED and bool(video.video_path)
    video_url = f"/api/video-jobs/{video.id}/video" if completed else None
    return {
        "id": video.id,
        "audio_job_id": video.audio_job_id,
        "status": video.status,
        "progress": int(video.progress or 0),
        "error": video.error,
        "video_url": video_url,
        "download_url": f"{video_url}?download=1" if video_url else None,
        "created_at": video.created_at,
        "updated_at": video.updated_at,
    }

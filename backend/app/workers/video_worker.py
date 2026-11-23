"""数字人视频 Worker：从 MySQL 抢占视频任务并调用渲染器生成 MP4。

与音频 Worker 的分工：
- API 创建 queued 视频任务后立即返回；
- video-worker 启动时把遗留 processing 恢复为 queued；
- 通过条件 UPDATE 原子抢占，避免多个 Worker 重复处理。
"""

from __future__ import annotations

import logging
import time

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal, engine
from app.models import Job, JobStatus, VideoJob, utcnow
from app.services.video_renderer import VideoRenderError, build_renderer
from app.storage import StorageError, get_storage

logger = logging.getLogger("app.video_worker")
MAX_ERROR_LENGTH = 500


def wait_for_database(timeout: float = 90.0, interval: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return
        except Exception as exc:  # noqa: BLE001 - 启动阶段需要持续重试
            if time.monotonic() >= deadline:
                raise RuntimeError("等待数据库连接超时") from exc
            logger.warning("数据库尚未就绪，稍后重试：%s", exc)
            time.sleep(interval)


def recover_stale_video_jobs(db: Session) -> int:
    result = db.execute(
        update(VideoJob)
        .where(VideoJob.status == JobStatus.PROCESSING)
        .values(status=JobStatus.QUEUED, error=None, progress=0, updated_at=utcnow())
    )
    db.commit()
    count = result.rowcount or 0
    if count:
        logger.warning("已将 %s 个遗留 processing 视频任务恢复为 queued", count)
    return count


def claim_next_video_job(db: Session) -> VideoJob | None:
    candidate = db.execute(
        select(VideoJob.id)
        .where(VideoJob.status == JobStatus.QUEUED)
        .order_by(VideoJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if not candidate:
        return None

    result = db.execute(
        update(VideoJob)
        .where(VideoJob.id == candidate, VideoJob.status == JobStatus.QUEUED)
        .values(
            status=JobStatus.PROCESSING,
            error=None,
            progress=5,
            updated_at=utcnow(),
        )
    )
    db.commit()
    if result.rowcount != 1:
        return None
    return db.get(VideoJob, candidate)


def _mark_failed(db: Session, video: VideoJob, message: str) -> None:
    video.status = JobStatus.FAILED
    video.error = message[:MAX_ERROR_LENGTH]
    video.video_path = None
    video.updated_at = utcnow()
    try:
        db.commit()
    except Exception:  # noqa: BLE001 - 必须记录，避免静默丢任务
        logger.exception("写入视频失败状态出错 video=%s", video.id)
        db.rollback()


def process_video_job(
    db: Session,
    video: VideoJob,
    *,
    renderer=None,
    storage=None,
) -> None:
    storage = storage or get_storage()
    renderer = renderer or build_renderer()

    audio_job = db.get(Job, video.audio_job_id)
    if (
        audio_job is None
        or audio_job.status != JobStatus.COMPLETED
        or not audio_job.audio_name
    ):
        logger.warning("视频任务缺少可用音频 video=%s", video.id)
        _mark_failed(db, video, "原始口播音频不可用，请重新生成口播后再试")
        return

    try:
        avatar_path = storage.avatar_path(video.audio_job_id, video.avatar_path)
        audio_path = storage.audio_path(video.audio_job_id, audio_job.audio_name)
        if not avatar_path.is_file():
            _mark_failed(db, video, "数字人图片不存在，请重新上传")
            return
        if not audio_path.is_file():
            _mark_failed(db, video, "原始口播音频不存在，请重新生成口播")
            return

        video_name = storage.video_name(video.id)
        output_path = storage.video_path(video.audio_job_id, video_name)

        video.progress = 15
        video.updated_at = utcnow()
        db.commit()

        renderer.render(avatar_path, audio_path, output_path)

        video.video_path = video_name
        video.status = JobStatus.COMPLETED
        video.progress = 100
        video.error = None
        video.updated_at = utcnow()
        db.commit()
        logger.info("视频任务完成 video=%s output=%s", video.id, video_name)
    except VideoRenderError as exc:
        logger.warning(
            "视频渲染失败 video=%s message=%s detail=%s", video.id, exc, exc.detail
        )
        _mark_failed(db, video, str(exc))
    except StorageError as exc:
        logger.exception("视频任务存储错误 video=%s：%s", video.id, exc)
        _mark_failed(db, video, "视频文件保存失败，请稍后重试")
    except Exception:  # noqa: BLE001 - Worker 不能因单个任务崩溃
        logger.exception("视频任务出现意外错误 video=%s", video.id)
        _mark_failed(db, video, "视频生成过程中出现意外错误，请稍后重试")


def run_forever() -> None:
    settings = get_settings()
    storage = get_storage()
    storage.ensure_base()
    wait_for_database()

    with SessionLocal() as db:
        recover_stale_video_jobs(db)

    logger.info("视频 Worker 已启动，轮询间隔 %.1fs", settings.worker_poll_interval)
    while True:
        try:
            with SessionLocal() as db:
                video = claim_next_video_job(db)
                if video is not None:
                    process_video_job(db, video)
                    continue
            time.sleep(settings.worker_poll_interval)
        except Exception:  # noqa: BLE001 - 主循环保持存活
            logger.exception("视频 Worker 主循环异常")
            time.sleep(settings.worker_poll_interval)


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, get_settings().log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    run_forever()


if __name__ == "__main__":
    main()

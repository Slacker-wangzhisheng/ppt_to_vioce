"""后台 Worker：从 MySQL 任务表抢占任务并调用 Dify。

- 启动时把遗留的 processing 任务恢复为 queued，避免 Worker 重启后任务卡死；
- 通过条件 UPDATE 抢占，避免多个 Worker 重复处理同一任务；
- 业务错误不重试；网络瞬时错误在 DifyClient 内部有限次重试 + 退避。
"""

from __future__ import annotations

import logging
import time

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal, engine
from app.models import Job, JobStatus, utcnow
from app.services.dify import DifyError, build_client
from app.storage import StorageError, get_storage

logger = logging.getLogger("app.worker")
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


def recover_stale_jobs(db: Session) -> int:
    result = db.execute(
        update(Job)
        .where(Job.status == JobStatus.PROCESSING)
        .values(status=JobStatus.QUEUED, error=None, updated_at=utcnow())
    )
    db.commit()
    count = result.rowcount or 0
    if count:
        logger.warning("已将 %s 个遗留 processing 任务恢复为 queued", count)
    return count


def claim_next_job(db: Session) -> Job | None:
    """原子抢占最早入队的任务。"""
    candidate = db.execute(
        select(Job.id)
        .where(Job.status == JobStatus.QUEUED)
        .order_by(Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if not candidate:
        return None

    result = db.execute(
        update(Job)
        .where(Job.id == candidate, Job.status == JobStatus.QUEUED)
        .values(status=JobStatus.PROCESSING, error=None, updated_at=utcnow())
    )
    db.commit()
    if result.rowcount != 1:
        return None
    return db.get(Job, candidate)


def _mark_failed(db: Session, job: Job, message: str) -> None:
    job.status = JobStatus.FAILED
    job.error = message[:MAX_ERROR_LENGTH]
    job.audio_name = None
    job.content_type = None
    job.updated_at = utcnow()
    try:
        db.commit()
    except Exception:  # noqa: BLE001 - 此处必须记录，避免静默丢任务
        logger.exception("写入失败状态出错 job=%s", job.id)
        db.rollback()


def process_job(db: Session, job: Job, *, client=None, storage=None) -> None:
    storage = storage or get_storage()
    client = client or build_client()
    try:
        result = client.generate(job, storage.source_path(job.id))
        audio_name, content_type = storage.save_audio(
            job.id, result.audio, result.content_type, result.source_url
        )
        job.transcript = result.transcript
        job.audio_name = audio_name
        job.content_type = content_type
        job.status = JobStatus.COMPLETED
        job.error = None
        job.updated_at = utcnow()
        db.commit()
        logger.info("任务完成 job=%s audio=%s", job.id, audio_name)
    except DifyError as exc:
        logger.warning(
            "任务失败 job=%s message=%s detail=%s", job.id, exc, exc.detail
        )
        _mark_failed(db, job, str(exc))
    except StorageError as exc:
        logger.exception("保存音频失败 job=%s：%s", job.id, exc)
        _mark_failed(db, job, "保存音频失败，请稍后重试")
    except Exception:  # noqa: BLE001 - Worker 不能因单个任务崩溃
        logger.exception("任务处理出现意外错误 job=%s", job.id)
        _mark_failed(db, job, "生成过程中出现意外错误，请稍后重试")
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


def run_forever() -> None:
    settings = get_settings()
    if settings.mock_dify:
        logger.warning("MOCK_DIFY=true：使用本地演示模式，不会调用真实 Dify")
    else:
        logger.info("Dify 服务地址：%s", settings.dify_base_url)

    storage = get_storage()
    storage.ensure_base()
    wait_for_database()

    with SessionLocal() as db:
        recover_stale_jobs(db)

    logger.info("Worker 已启动，轮询间隔 %.1fs", settings.worker_poll_interval)
    while True:
        try:
            with SessionLocal() as db:
                job = claim_next_job(db)
                if job is not None:
                    process_job(db, job)
                    continue
            time.sleep(settings.worker_poll_interval)
        except Exception:  # noqa: BLE001 - 主循环保持存活
            logger.exception("Worker 主循环异常")
            time.sleep(settings.worker_poll_interval)


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, get_settings().log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    run_forever()


if __name__ == "__main__":
    main()

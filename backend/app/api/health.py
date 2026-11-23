"""健康检查路由：确认 API、数据库与存储可用。"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.database import engine
from app.schemas import HealthResponse
from app.storage import StorageError, get_storage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health():
    settings = get_settings()
    database = "ok"
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - 健康检查需要兜底
        logger.error("数据库健康检查失败：%s", exc)
        database = "unavailable"

    storage_status = "ok"
    try:
        storage = get_storage()
        storage.ensure_base()
    except (StorageError, OSError) as exc:
        logger.error("存储健康检查失败：%s", exc)
        storage_status = "unavailable"

    payload = {
        "status": "ok" if database == "ok" and storage_status == "ok" else "degraded",
        "database": database,
        "storage": storage_status,
        "mock_dify": bool(settings.mock_dify),
    }
    if payload["status"] != "ok":
        return JSONResponse(status_code=503, content=payload)
    return payload

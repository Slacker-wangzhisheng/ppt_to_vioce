"""FastAPI 应用入口。

数据库结构由 Alembic 管理（容器启动时执行 alembic upgrade head），
此处不再使用 create_all。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import health, jobs, videos
from app.config import get_settings

logger = logging.getLogger(__name__)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # httpx 在 INFO 级别会输出请求行，避免噪音；API Key 在请求头中不会被记录
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def create_app() -> FastAPI:
    settings = get_settings()
    _configure_logging(settings.log_level)

    application = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    if settings.cors_origin_list:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origin_list),
            allow_methods=["GET", "POST"],
            allow_headers=["*"],
        )

    application.include_router(health.router)
    application.include_router(jobs.router)
    application.include_router(videos.router)

    @application.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        fields = sorted(
            {
                str(error["loc"][-1])
                for error in exc.errors()
                if error.get("loc")
            }
        )
        detail = "请求参数不合法" + (f"：{', '.join(fields)}" if fields else "")
        return JSONResponse(status_code=422, content={"detail": detail})

    return application


app = create_app()

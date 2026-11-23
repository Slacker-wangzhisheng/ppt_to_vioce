"""API 输入输出 Schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    database: str
    storage: str
    mock_dify: bool


class JobResponse(BaseModel):
    id: str
    filename: str
    status: str
    voice: str
    speed: float
    transcript: str | None = None
    error: str | None = None
    audio_name: str | None = None
    audio_url: str | None = None
    download_url: str | None = None
    created_at: datetime
    updated_at: datetime


class VideoJobResponse(BaseModel):
    id: str
    audio_job_id: str
    status: str
    progress: int
    error: str | None = None
    video_url: str | None = None
    download_url: str | None = None
    created_at: datetime
    updated_at: datetime

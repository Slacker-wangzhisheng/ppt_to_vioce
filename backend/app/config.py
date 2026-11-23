"""应用配置：数据库、存储、Dify 与 Worker 参数统一从这里读取。

所有敏感信息（数据库密码、Dify API Key）只来自环境变量 / .env，
不会写入镜像，也不会返回给前端。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "PPT 转口播 API"
    log_level: str = "INFO"

    # 数据库：生产使用 MySQL，测试使用独立的 SQLite 文件
    database_url: str = "sqlite:///./data/ppt_voice.db"

    # 存储：当前只实现 local，结构上预留 S3/MinIO
    storage_backend: str = "local"
    storage_dir: Path = Path("./data")

    max_upload_bytes: int = 50 * 1024 * 1024
    max_audio_bytes: int = 100 * 1024 * 1024
    max_pptx_entries: int = 5000
    max_pptx_uncompressed_bytes: int = 500 * 1024 * 1024

    # 数字人视频
    avatar_max_bytes: int = 10 * 1024 * 1024
    avatar_max_pixels: int = 25_000_000
    video_width: int = 1280
    video_height: int = 720
    video_max_bytes: int = 500 * 1024 * 1024
    video_render_timeout: float = 1800.0
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"

    # Dify
    dify_base_url: str = "https://api.dify.ai/v1"
    dify_api_key: str = ""
    dify_file_input: str = "ppt_file"
    dify_voice_input: str = "voice"
    dify_speed_input: str = "speed"
    dify_audio_output: str = "audio"
    dify_text_output: str = "transcript"
    # blocking 简单但长任务容易被网关按空闲超时断开；streaming 有 ping 保活
    dify_response_mode: Literal["blocking", "streaming"] = "blocking"
    dify_connect_timeout: float = 10.0
    dify_read_timeout: float = 600.0
    dify_total_timeout: float = 660.0
    dify_max_retries: int = 3
    dify_retry_backoff: float = 1.5
    # 除 Dify API 同源域名外，允许下载音频的额外域名（逗号分隔）
    dify_audio_allowed_hosts: str = ""

    # 本地演示模式：不调用真实 Dify
    mock_dify: bool = False
    mock_dify_delay: float = 1.5

    worker_poll_interval: float = 2.0

    # 同源部署无需 CORS；如前端独立域名可在此配置（逗号分隔）
    cors_origins: str = ""

    @field_validator("dify_base_url")
    @classmethod
    def _strip_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value:
            raise ValueError("DIFY_BASE_URL 不能为空")
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("DIFY_BASE_URL 必须是 http(s) 地址")
        return value

    @field_validator("storage_dir", mode="before")
    @classmethod
    def _expand_storage_dir(cls, value: object) -> object:
        if isinstance(value, str):
            return Path(value).expanduser()
        return value

    @property
    def upload_limit_mb(self) -> int:
        return self.max_upload_bytes // (1024 * 1024)

    @property
    def avatar_limit_mb(self) -> int:
        return self.avatar_max_bytes // (1024 * 1024)

    @property
    def audio_allowed_hosts(self) -> tuple[str, ...]:
        return _split_csv(self.dify_audio_allowed_hosts)

    @property
    def cors_origin_list(self) -> tuple[str, ...]:
        return _split_csv(self.cors_origins)

    @property
    def dify_enabled(self) -> bool:
        return bool(self.dify_api_key) and not self.dify_api_key.startswith("app-your-")


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in (value or "").split(",") if item.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

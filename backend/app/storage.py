"""文件存储抽象。

当前实现为本地目录（Docker Volume），接口保持简单，便于后续替换为
MinIO / S3：只需新增一个实现 Storage 协议的后端并调整 get_storage()。
"""

from __future__ import annotations

import re
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.config import get_settings

AUDIO_CONTENT_TYPES = {
    "audio/mpeg": (".mp3", "audio/mpeg"),
    "audio/mp3": (".mp3", "audio/mpeg"),
    "audio/mpeg3": (".mp3", "audio/mpeg"),
    "audio/x-mpeg-3": (".mp3", "audio/mpeg"),
    "audio/wav": (".wav", "audio/wav"),
    "audio/wave": (".wav", "audio/wav"),
    "audio/x-wav": (".wav", "audio/wav"),
    "audio/vnd.wave": (".wav", "audio/wav"),
    "audio/ogg": (".ogg", "audio/ogg"),
    "audio/mp4": (".m4a", "audio/mp4"),
    "audio/x-m4a": (".m4a", "audio/mp4"),
    "audio/aac": (".aac", "audio/aac"),
    "audio/webm": (".webm", "audio/webm"),
}

AUDIO_EXTENSIONS = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".webm": "audio/webm",
}

AVATAR_SUFFIXES = {".png", ".jpg", ".jpeg"}

_UNSAFE_AUDIO_NAME = re.compile(r"[^A-Za-z0-9_.-]")


class StorageError(RuntimeError):
    pass


def sniff_audio(data: bytes) -> tuple[str, str] | None:
    """通过文件头推断音频格式，返回 (扩展名, Content-Type)。"""
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return ".mp3", "audio/mpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return ".wav", "audio/wav"
    if data[:4] == b"OggS":
        return ".ogg", "audio/ogg"
    if data[4:8] == b"ftyp":
        return ".m4a", "audio/mp4"
    return None


def guess_audio_format(
    content_type: str | None, url: str | None, data: bytes
) -> tuple[str, str]:
    """确定音频扩展名和 Content-Type：优先文件头，其次响应头，最后 URL。"""
    sniffed = sniff_audio(data)
    if sniffed:
        return sniffed

    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    if normalized in AUDIO_CONTENT_TYPES:
        return AUDIO_CONTENT_TYPES[normalized]

    if url:
        suffix = Path(url.split("?", 1)[0]).suffix.lower()
        if suffix in AUDIO_EXTENSIONS:
            return suffix, AUDIO_EXTENSIONS[suffix]

    if normalized in ("application/octet-stream", "binary/octet-stream", ""):
        return ".mp3", "audio/mpeg"

    raise StorageError("不支持的音频格式")


def sanitize_filename(raw: str | None, fallback: str = "presentation.pptx") -> str:
    """去除目录部分和控制字符，防止目录穿越。"""
    name = (raw or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip().strip(".")
    if not name or name in {".", ".."}:
        return fallback
    return name[:180]


class Storage(Protocol):
    def ensure_job_dir(self, job_id: str) -> Path: ...

    def source_path(self, job_id: str) -> Path: ...

    def save_audio(
        self, job_id: str, data: bytes, content_type: str | None, url: str | None
    ) -> tuple[str, str]: ...

    def audio_path(self, job_id: str, audio_name: str) -> Path: ...

    def save_avatar(
        self, job_id: str, video_id: str, data: bytes, suffix: str
    ) -> tuple[str, Path]: ...

    def avatar_path(self, job_id: str, avatar_name: str) -> Path: ...

    def video_name(self, video_id: str) -> str: ...

    def video_path(self, job_id: str, video_name: str) -> Path: ...

    def remove_avatar(self, job_id: str, avatar_name: str) -> None: ...

    def remove_job(self, job_id: str) -> None: ...


class LocalStorage:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir).resolve()

    def ensure_base(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def ensure_job_dir(self, job_id: str) -> Path:
        _validate_job_id(job_id)
        self.ensure_base()
        path = (self.base_dir / job_id).resolve()
        if not path.is_relative_to(self.base_dir):
            raise StorageError("非法的任务目录")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def source_path(self, job_id: str) -> Path:
        _validate_job_id(job_id)
        return self.ensure_job_dir(job_id) / "source.pptx"

    def save_audio(
        self, job_id: str, data: bytes, content_type: str | None, url: str | None
    ) -> tuple[str, str]:
        if not data:
            raise StorageError("音频内容为空")
        suffix, resolved_type = guess_audio_format(content_type, url, data)
        audio_name = f"audio{suffix}"
        target = self.audio_path(job_id, audio_name)
        target.write_bytes(data)
        return audio_name, resolved_type

    def audio_path(self, job_id: str, audio_name: str) -> Path:
        _validate_job_id(job_id)
        if not audio_name or _UNSAFE_AUDIO_NAME.search(audio_name) or ".." in audio_name:
            raise StorageError("非法的音频文件名")
        job_dir = self.ensure_job_dir(job_id)
        path = (job_dir / audio_name).resolve()
        if not path.is_relative_to(job_dir):
            raise StorageError("非法的音频路径")
        return path

    def save_avatar(
        self, job_id: str, video_id: str, data: bytes, suffix: str
    ) -> tuple[str, Path]:
        _validate_job_id(video_id)
        suffix = suffix.lower()
        if suffix not in AVATAR_SUFFIXES:
            raise StorageError("不支持的图片格式")
        avatar_name = f"{video_id}_avatar{suffix}"
        target = self.avatar_path(job_id, avatar_name)
        target.write_bytes(data)
        return avatar_name, target

    def avatar_path(self, job_id: str, avatar_name: str) -> Path:
        return self._file_path(job_id, avatar_name, "非法的图片文件名")

    def video_name(self, video_id: str) -> str:
        _validate_job_id(video_id)
        return f"{video_id}.mp4"

    def video_path(self, job_id: str, video_name: str) -> Path:
        return self._file_path(job_id, video_name, "非法的视频文件名")

    def remove_avatar(self, job_id: str, avatar_name: str) -> None:
        try:
            self.avatar_path(job_id, avatar_name).unlink(missing_ok=True)
        except (StorageError, OSError):
            pass

    def _file_path(self, job_id: str, name: str, message: str) -> Path:
        _validate_job_id(job_id)
        if not name or _UNSAFE_AUDIO_NAME.search(name) or ".." in name:
            raise StorageError(message)
        job_dir = self.ensure_job_dir(job_id)
        path = (job_dir / name).resolve()
        if not path.is_relative_to(job_dir):
            raise StorageError(message)
        return path

    def remove_job(self, job_id: str) -> None:
        _validate_job_id(job_id)
        job_dir = (self.base_dir / job_id).resolve()
        if not job_dir.is_relative_to(self.base_dir) or not job_dir.is_dir():
            return
        for item in job_dir.iterdir():
            if item.is_file():
                item.unlink(missing_ok=True)
        job_dir.rmdir()


def _validate_job_id(job_id: str) -> None:
    try:
        parsed = uuid.UUID(str(job_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise StorageError("非法的任务 ID") from exc
    if str(parsed) != str(job_id).lower():
        raise StorageError("非法的任务 ID")


@lru_cache(maxsize=1)
def get_storage() -> LocalStorage:
    settings = get_settings()
    if settings.storage_backend != "local":
        raise StorageError(
            f"未实现的存储后端：{settings.storage_backend}（当前仅支持 local）"
        )
    return LocalStorage(settings.storage_dir)

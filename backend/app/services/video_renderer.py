"""数字人视频渲染器。

FFmpegStillImageRenderer：静态数字人图片 + AI 口播音频合成 MP4。
"""

from __future__ import annotations

import logging
import os
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path

from app.config import Settings, get_settings

logger = logging.getLogger("app.video_renderer")

MAX_STDERR_LOG = 2000


class VideoRenderError(RuntimeError):
    """渲染失败；str(exc) 为可直接展示给用户的简短信息。"""

    def __init__(self, message: str, detail: str | None = None) -> None:
        super().__init__(message)
        self.detail = detail


class VideoRenderer(ABC):
    @abstractmethod
    def render(
        self,
        avatar_path: Path,
        audio_path: Path,
        output_path: Path,
    ) -> None:
        """将静态头像与音频合成为 MP4，成功后 output_path 必须存在。"""


class FFmpegStillImageRenderer(VideoRenderer):
    def __init__(self, settings: Settings | None = None) -> None:
        settings = settings or get_settings()
        self.ffmpeg = settings.ffmpeg_bin
        self.ffprobe = settings.ffprobe_bin
        self.width = settings.video_width
        self.height = settings.video_height
        self.timeout = settings.video_render_timeout
        self.max_bytes = settings.video_max_bytes

    def render(
        self,
        avatar_path: Path,
        audio_path: Path,
        output_path: Path,
    ) -> None:
        avatar_path = Path(avatar_path)
        audio_path = Path(audio_path)
        output_path = Path(output_path)
        if not avatar_path.is_file():
            raise VideoRenderError("数字人图片不存在，请重新上传")
        if not audio_path.is_file():
            raise VideoRenderError("原始音频不存在，请重新生成口播")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = output_path.with_name(
            f"{output_path.stem}.part{output_path.suffix or '.mp4'}"
        )
        temp_path.unlink(missing_ok=True)

        video_filter = (
            f"scale={self.width}:{self.height}:force_original_aspect_ratio=decrease,"
            f"pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
        )
        args = [
            self.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-loop",
            "1",
            "-i",
            str(avatar_path),
            "-i",
            str(audio_path),
            "-vf",
            video_filter,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            "-shortest",
            str(temp_path),
        ]

        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            temp_path.unlink(missing_ok=True)
            logger.error("FFmpeg 渲染超时 output=%s timeout=%s", output_path.name, self.timeout)
            raise VideoRenderError("视频合成超时，请稍后重试", str(exc)) from exc
        except OSError as exc:
            temp_path.unlink(missing_ok=True)
            logger.error("无法启动 FFmpeg：%s", exc)
            raise VideoRenderError("视频合成服务不可用，请稍后重试", str(exc)) from exc

        if completed.returncode != 0:
            temp_path.unlink(missing_ok=True)
            detail = _trim_stderr(completed.stderr)
            logger.warning("FFmpeg 渲染失败 code=%s detail=%s", completed.returncode, detail)
            raise VideoRenderError("视频合成失败，请稍后重试", detail)

        if not temp_path.is_file() or temp_path.stat().st_size == 0:
            temp_path.unlink(missing_ok=True)
            raise VideoRenderError("视频合成失败，请稍后重试", "FFmpeg 未生成有效文件")

        size = temp_path.stat().st_size
        if size > self.max_bytes:
            temp_path.unlink(missing_ok=True)
            logger.error("视频文件过大 size=%s limit=%s", size, self.max_bytes)
            raise VideoRenderError("生成的视频文件过大，请换一张图片后重试", f"size={size}")

        self._verify_streams(temp_path)
        os.replace(temp_path, output_path)
        logger.info(
            "视频渲染完成 output=%s size=%s", output_path.name, output_path.stat().st_size
        )

    def _verify_streams(self, path: Path) -> None:
        args = [
            self.ffprobe,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(path),
        ]
        try:
            completed = subprocess.run(
                args, capture_output=True, timeout=60, check=False
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.error("ffprobe 执行失败：%s", exc)
            raise VideoRenderError("视频文件校验失败，请稍后重试", str(exc)) from exc

        streams = {
            line.strip()
            for line in completed.stdout.decode("utf-8", "replace").splitlines()
            if line.strip()
        }
        if completed.returncode != 0 or "video" not in streams or "audio" not in streams:
            detail = _trim_stderr(completed.stderr) or f"streams={sorted(streams)}"
            logger.error("输出文件缺少音视频流：%s", detail)
            raise VideoRenderError("视频文件校验失败，请稍后重试", detail)


def _trim_stderr(raw: bytes | None) -> str:
    if not raw:
        return ""
    return raw.decode("utf-8", "replace")[-MAX_STDERR_LOG:]


def build_renderer() -> VideoRenderer:
    """渲染器工厂。"""
    return FFmpegStillImageRenderer()

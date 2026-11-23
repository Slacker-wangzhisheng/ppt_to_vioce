"""本地演示模式：不访问 Dify，直接生成示例口播稿和一段短音频。

用于没有 Dify Key 时演示完整上传 → 处理 → 播放 → 下载流程。
正式环境请保持 MOCK_DIFY=false。
"""

from __future__ import annotations

import html
import io
import math
import re
import struct
import time
import wave
import zipfile
from pathlib import Path

from app.config import Settings, get_settings
from app.services.dify import WorkflowResult

_TAG_RE = re.compile(r"<a:t>(.*?)</a:t>", re.S)
_SLIDE_RE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")


def _read_slide_texts(source: Path, limit: int = 12) -> list[list[str]]:
    try:
        with zipfile.ZipFile(source) as archive:
            slide_names = []
            for name in archive.namelist():
                match = _SLIDE_RE.match(name)
                if match:
                    slide_names.append((int(match.group(1)), name))
            slides: list[list[str]] = []
            for _, name in sorted(slide_names)[:limit]:
                xml = archive.read(name).decode("utf-8", "ignore")
                lines = [
                    html.unescape(item).strip()
                    for item in _TAG_RE.findall(xml)
                ]
                lines = [line for line in lines if line]
                if lines:
                    slides.append(lines)
            return slides
    except (zipfile.BadZipFile, OSError, KeyError):
        return []


def _compose_transcript(filename: str, slides: list[list[str]]) -> str:
    title = Path(filename).stem or "本次演示"
    paragraphs = [f"大家好，欢迎收听《{title}》的口播版本。"]
    if slides:
        for index, lines in enumerate(slides, start=1):
            content = "，".join(dict.fromkeys(lines[:6]))
            paragraphs.append(f"第 {index} 页，我们重点聊一聊{content}。")
    else:
        paragraphs.append(
            "这份演示围绕主题背景、核心观点和落地建议展开，"
            "我们把关键信息整理成更适合聆听的表达。"
        )
        paragraphs.append(
            "希望这段口播能帮助你在通勤或休息时，快速回顾整份内容。"
        )
    paragraphs.append("以上就是本次分享的主要内容，感谢聆听。")
    return "\n\n".join(paragraphs)


def _synth_wav() -> bytes:
    """生成约 1.4 秒的柔和提示音，保证演示模式有可播放的音频。"""
    rate = 22050
    notes = ((523.25, 0.35), (659.25, 0.35), (783.99, 0.7))
    samples: list[int] = []
    for frequency, duration in notes:
        total = int(rate * duration)
        fade_in = max(1, int(rate * 0.02))
        fade_out = max(1, int(rate * 0.08))
        for i in range(total):
            envelope = min(1.0, i / fade_in, (total - i) / fade_out)
            value = math.sin(2 * math.pi * frequency * i / rate)
            samples.append(int(8000 * envelope * value))

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buffer.getvalue()


class MockDifyClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def close(self) -> None:
        return None

    def __enter__(self) -> MockDifyClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def generate(self, job, source_path: Path) -> WorkflowResult:
        if self.settings.mock_dify_delay > 0:
            time.sleep(self.settings.mock_dify_delay)
        slides = _read_slide_texts(source_path)
        transcript = _compose_transcript(job.filename, slides)
        return WorkflowResult(
            transcript=transcript,
            audio=_synth_wav(),
            content_type="audio/wav",
            source_url=None,
        )

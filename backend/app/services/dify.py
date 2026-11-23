"""Dify Workflow 客户端。

流程：
1. POST {DIFY_BASE_URL}/files/upload 上传 PPTX，拿到 upload_file_id
2. POST {DIFY_BASE_URL}/workflows/run 执行工作流（默认 streaming，可配置 blocking）
3. 从 outputs 中读取 transcript 与 audio，并安全下载音频

安全约束：
- 只允许 http/https；
- 音频域名必须在 Dify 同源域名或 DIFY_AUDIO_ALLOWED_HOSTS 内；
- 下载时有大小上限，且跟随重定向时逐跳重新校验（防 SSRF）；
- 日志中绝不输出 API Key。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
MAX_REDIRECTS = 3


class DifyError(RuntimeError):
    """用户可见的简洁错误信息，detail 仅写入服务端日志。"""

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.detail = detail


@dataclass
class WorkflowResult:
    transcript: str | None
    audio: bytes
    content_type: str | None = None
    source_url: str | None = None


def _find_audio_ref(value: object) -> tuple[str, str] | None:
    """兼容 Dify 多种文件输出结构，返回 ("url"|"file_id", 值)。"""
    if isinstance(value, str):
        stripped = value.strip()
        return ("url", stripped) if stripped else None
    if isinstance(value, list):
        for item in value:
            found = _find_audio_ref(item)
            if found:
                return found
        return None
    if isinstance(value, dict):
        for key in ("files", "file_list", "items", "list", "data"):
            if key in value:
                found = _find_audio_ref(value[key])
                if found:
                    return found
        for key in ("url", "remote_url", "file_url", "download_url", "preview_url"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return ("url", candidate.strip())
        for key in ("id", "file_id", "upload_file_id"):
            candidate = value.get(key)
            if candidate:
                return ("file_id", str(candidate))
    return None


class DifyClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._deadline: float | None = None
        self._client = httpx.Client(
            transport=transport,
            follow_redirects=False,
            timeout=httpx.Timeout(
                connect=self.settings.dify_connect_timeout,
                read=self.settings.dify_read_timeout,
                write=self.settings.dify_read_timeout,
                pool=self.settings.dify_connect_timeout,
            ),
        )

    # ---- 生命周期 -------------------------------------------------
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> DifyClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ---- 对外入口 -------------------------------------------------
    def generate(self, job, source_path: Path) -> WorkflowResult:
        if not self.settings.dify_enabled:
            raise DifyError("AI 服务尚未配置，请联系管理员")

        self._deadline = time.monotonic() + self.settings.dify_total_timeout
        upload_id = self._upload_file(source_path, job.filename, job.id)
        outputs = self._run_workflow(upload_id, job.voice, job.speed, job.id)

        transcript = outputs.get(self.settings.dify_text_output)
        if transcript is not None and not isinstance(transcript, str):
            transcript = str(transcript)
        transcript = (transcript or "").strip() or None

        ref = _find_audio_ref(outputs.get(self.settings.dify_audio_output))
        if not ref:
            raise DifyError(
                "AI 工作流没有返回音频，请检查工作流输出配置",
                detail=f"outputs keys: {sorted(outputs.keys())}",
            )
        audio_url = self._resolve_audio_url(ref)
        audio, content_type = self._download_audio(audio_url)
        logger.info("Dify 工作流完成 job=%s transcript_len=%s audio_bytes=%s", job.id, len(transcript or ""), len(audio))
        return WorkflowResult(transcript, audio, content_type, audio_url)

    # ---- 内部步骤 -------------------------------------------------
    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.settings.dify_api_key}"}

    def _timeout(self) -> httpx.Timeout:
        return self._timeout_for(self.settings.dify_read_timeout)

    def _timeout_for(self, read: float) -> httpx.Timeout:
        if self._deadline is None:
            remaining = read
        else:
            remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise DifyError("AI 处理超时，请稍后重试")
        return httpx.Timeout(
            connect=min(self.settings.dify_connect_timeout, remaining),
            read=min(read, remaining),
            write=min(read, remaining),
            pool=min(self.settings.dify_connect_timeout, remaining),
        )

    def _sleep(self, attempt: int) -> None:
        delay = min(self.settings.dify_retry_backoff * (2 ** (attempt - 1)), 8.0)
        if delay > 0:
            time.sleep(delay)

    def _request_with_retry(self, request_factory, description: str) -> httpx.Response:
        attempts = max(1, self.settings.dify_max_retries)
        last_error: str | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = request_factory()
            except httpx.TransportError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt >= attempts:
                    break
                logger.warning("%s 网络异常（第 %s 次），准备重试", description, attempt)
                self._sleep(attempt)
                continue

            if response.status_code in RETRYABLE_STATUS and attempt < attempts:
                last_error = f"HTTP {response.status_code}"
                logger.warning("%s 返回 %s（第 %s 次），准备重试", description, response.status_code, attempt)
                self._sleep(attempt)
                continue
            return response

        raise DifyError(
            f"{description}失败，请稍后重试",
            detail=last_error,
        )

    def _upload_file(self, source: Path, filename: str, user: str) -> str:
        url = f"{self.settings.dify_base_url}/files/upload"

        def request() -> httpx.Response:
            with source.open("rb") as stream:
                return self._client.post(
                    url,
                    headers=self._auth_headers(),
                    data={"user": user},
                    files={"file": (filename, stream, PPTX_MIME)},
                    timeout=self._timeout(),
                )

        response = self._request_with_retry(request, "上传文件到 Dify")
        if response.status_code >= 400:
            raise self._http_error(response, "上传 PPTX 到 Dify")

        try:
            body = response.json()
        except ValueError as exc:
            raise DifyError("Dify 返回了无法解析的上传结果", detail=response.text[:500]) from exc
        upload_id = body.get("id") or body.get("file_id")
        if not upload_id:
            raise DifyError("Dify 未返回文件 ID", detail=response.text[:500])
        return str(upload_id)

    def _run_workflow(
        self, upload_id: str, voice: str, speed: float, user: str
    ) -> dict:
        url = f"{self.settings.dify_base_url}/workflows/run"
        payload = {
            "inputs": {
                self.settings.dify_file_input: {
                    "type": "document",
                    "transfer_method": "local_file",
                    "upload_file_id": upload_id,
                },
                self.settings.dify_voice_input: voice,
                self.settings.dify_speed_input: float(speed),
            },
            "response_mode": self.settings.dify_response_mode,
            "user": user,
        }

        if self.settings.dify_response_mode == "streaming":
            return self._run_workflow_stream(payload)

        response = self._request_with_retry(
            lambda: self._client.post(
                url,
                headers=self._auth_headers(),
                json=payload,
                timeout=self._timeout(),
            ),
            "执行 Dify 工作流",
        )
        if response.status_code >= 400:
            raise self._http_error(response, "Dify 工作流执行")

        try:
            body = response.json()
        except ValueError as exc:
            raise DifyError("Dify 返回了无法解析的结果", detail=response.text[:500]) from exc

        result = body.get("data") if isinstance(body.get("data"), dict) else body
        status = result.get("status")
        if status and status != "succeeded":
            detail = str(result.get("error") or status)[:1000]
            logger.error("Dify 工作流执行失败：%s", detail)
            raise DifyError("AI 工作流执行失败，请稍后重试", detail=detail)

        outputs = result.get("outputs")
        if not isinstance(outputs, dict) or not outputs:
            raise DifyError(
                "AI 工作流没有返回结果，请检查 End 节点输出",
                detail=response.text[:500],
            )
        return outputs

    def _run_workflow_stream(self, payload: dict) -> dict:
        """流式执行工作流，避免长任务在 blocking 模式下被网关按空闲超时断开。"""
        url = f"{self.settings.dify_base_url}/workflows/run"
        attempts = max(1, self.settings.dify_max_retries)
        last_error: str | None = None

        for attempt in range(1, attempts + 1):
            try:
                with self._client.stream(
                    "POST",
                    url,
                    headers={**self._auth_headers(), "Accept": "text/event-stream"},
                    json=payload,
                    timeout=self._timeout(),
                ) as response:
                    if response.status_code >= 400:
                        response.read()
                        error = self._http_error(response, "Dify 工作流执行")
                        if response.status_code in RETRYABLE_STATUS and attempt < attempts:
                            last_error = str(error)
                            logger.warning(
                                "执行 Dify 工作流返回 %s（第 %s 次），准备重试",
                                response.status_code,
                                attempt,
                            )
                        else:
                            raise error
                    else:
                        return self._consume_workflow_stream(response)
            except httpx.TransportError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("执行 Dify 工作流网络异常（第 %s 次），准备重试", attempt)

            if attempt < attempts:
                self._sleep(attempt)

        raise DifyError("执行 Dify 工作流失败，请稍后重试", detail=last_error)

    def _consume_workflow_stream(self, response: httpx.Response) -> dict:
        for line in response.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue

            event_type = event.get("event")
            if event_type == "workflow_finished":
                data = event.get("data") or {}
                status = data.get("status")
                if status and status != "succeeded":
                    detail = str(data.get("error") or status)[:1000]
                    logger.error("Dify 工作流执行失败：%s", detail)
                    raise DifyError("AI 工作流执行失败，请稍后重试", detail=detail)
                outputs = data.get("outputs")
                if not isinstance(outputs, dict) or not outputs:
                    raise DifyError(
                        "AI 工作流没有返回结果，请检查 End 节点输出",
                        detail=str(event)[:500],
                    )
                return outputs
            if event_type == "error":
                detail = str(event.get("message") or event)[:1000]
                logger.error("Dify 工作流执行失败：%s", detail)
                raise DifyError("AI 工作流执行失败，请稍后重试", detail=detail)

        raise DifyError(
            "AI 工作流连接中断，请稍后重试",
            detail="SSE 结束但未收到 workflow_finished",
        )

    def _resolve_audio_url(self, ref: tuple[str, str]) -> str:
        kind, value = ref
        if kind == "file_id":
            return (
                f"{self.settings.dify_base_url}/files/{value}/preview"
                "?as_attachment=true"
            )
        if value.startswith(("http://", "https://")):
            return value
        if value.startswith("/"):
            base = urlparse(self.settings.dify_base_url)
            return f"{base.scheme}://{base.netloc}{value}"
        return urljoin(f"{self.settings.dify_base_url}/", value)

    def _validate_audio_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise DifyError("音频地址协议不受支持")
        host = (parsed.hostname or "").lower()
        if not host:
            raise DifyError("音频地址缺少有效域名")

        base_host = (urlparse(self.settings.dify_base_url).hostname or "").lower()
        allowed = {base_host, *self.settings.audio_allowed_hosts}
        if host not in {item.lower() for item in allowed if item}:
            logger.warning("阻止非白名单音频地址：%s", host)
            raise DifyError("音频地址不在允许的域名范围内")

    def _stream_download(self, url: str) -> tuple[int, httpx.Headers, bytes]:
        attempts = max(1, self.settings.dify_max_retries)
        last_error: str | None = None

        for attempt in range(1, attempts + 1):
            try:
                with self._client.stream(
                    "GET",
                    url,
                    headers=self._auth_headers(),
                    timeout=self._timeout(),
                ) as response:
                    if response.status_code in RETRYABLE_STATUS and attempt < attempts:
                        last_error = f"HTTP {response.status_code}"
                        logger.warning("下载音频返回 %s（第 %s 次），准备重试", response.status_code, attempt)
                    else:
                        data = bytearray()
                        for chunk in response.iter_bytes(64 * 1024):
                            data.extend(chunk)
                            if len(data) > self.settings.max_audio_bytes:
                                raise DifyError("生成的音频文件过大")
                        return response.status_code, response.headers, bytes(data)
            except httpx.TransportError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("下载音频网络异常（第 %s 次）", attempt)

            if attempt < attempts:
                self._sleep(attempt)

        raise DifyError("下载音频失败，请稍后重试", detail=last_error)

    def _download_audio(self, url: str) -> tuple[bytes, str | None]:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            self._validate_audio_url(current)
            status, headers, data = self._stream_download(current)

            if status in (301, 302, 303, 307, 308):
                location = headers.get("location")
                if not location:
                    raise DifyError("音频地址跳转缺少目标")
                current = urljoin(current, location)
                continue

            if status >= 400:
                detail = f"HTTP {status} {urlparse(current).path}"
                logger.error("下载音频失败：%s", detail)
                raise DifyError("下载生成的音频失败，请稍后重试", detail=detail)

            content_type = (headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            if content_type and not (
                content_type.startswith("audio/")
                or content_type in ("application/octet-stream", "binary/octet-stream")
            ):
                detail = f"unexpected content-type: {content_type}"
                logger.error("音频下载返回异常类型：%s", detail)
                raise DifyError("AI 服务返回的音频格式不受支持", detail=detail)

            if not data:
                raise DifyError("AI 服务返回了空音频")
            return data, content_type or None

        raise DifyError("音频地址重定向次数过多")

    def _http_error(self, response: httpx.Response, action: str) -> DifyError:
        status = response.status_code
        snippet = response.text[:500].replace(self.settings.dify_api_key or "\0", "***")
        logger.error("%s失败：HTTP %s %s", action, status, snippet)

        if status in (401, 403):
            return DifyError("AI 服务鉴权失败，请联系管理员检查 Dify 配置", detail=snippet)
        if status == 404:
            return DifyError("AI 服务接口不存在，请检查 DIFY_BASE_URL", detail=snippet)
        if status == 413:
            return DifyError("文件超过 AI 服务允许的大小", detail=snippet)
        if status == 429:
            return DifyError("AI 服务繁忙，请稍后重试", detail=snippet)
        if status >= 500:
            return DifyError("AI 服务暂时不可用，请稍后重试", detail=snippet)
        return DifyError(f"{action}失败（HTTP {status}）", detail=snippet)


def build_client(settings: Settings | None = None) -> DifyClient:
    """按配置返回真实 Dify 客户端或本地演示客户端。"""
    settings = settings or get_settings()
    if settings.mock_dify:
        from app.services.mock_dify import MockDifyClient

        return MockDifyClient(settings)  # type: ignore[return-value]
    return DifyClient(settings)

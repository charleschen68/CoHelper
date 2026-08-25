"""Closeable loopback Ollama transports for region OCR and translation."""

from __future__ import annotations

import base64
import json
import threading
from io import BytesIO
from urllib.parse import urlparse

from PIL import Image
import requests

from ai_drive.model_scheduler import (
    DEFAULT_MODEL_SCHEDULER,
    ModelQueueCancelled,
    ModelQueueTimeout,
    ModelScheduler,
)

MAX_VISION_LONG_EDGE = 3072
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class RegionTransportError(RuntimeError):
    """The local model transport returned an invalid protocol response."""


class RegionTransportCancelled(RegionTransportError):
    """The active response was closed by an explicit cancellation."""


class RegionTransportQueueTimeout(RegionTransportError):
    """The request could not obtain the shared local-model lease in time."""


class _OllamaStreamingClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        timeout: int = 60,
        session=requests,
        scheduler: ModelScheduler = DEFAULT_MODEL_SCHEDULER,
        queue_timeout: int = 30,
    ):
        host = urlparse(base_url).hostname
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or host not in _LOOPBACK_HOSTS:
            raise ValueError("region translation endpoint must be local")
        if timeout <= 0:
            raise ValueError("region translation timeout must be positive")
        if queue_timeout <= 0:
            raise ValueError("region translation queue timeout must be positive")
        self.endpoint = base_url.rstrip("/")
        self._timeout = timeout
        self._scheduler = scheduler
        self._queue_timeout = queue_timeout
        self._session = session
        self._lock = threading.RLock()
        self._response = None
        self._request_active = False
        self._request_token = 0
        self._cancel_requested = False

    def cancel(self) -> bool:
        return self._cancel_for_token(None)

    def _cancel_for_token(self, token: int | None) -> bool:
        with self._lock:
            if not self._request_active or (
                token is not None and token != self._request_token
            ):
                return True
            self._cancel_requested = True
            response = self._response
            self._response = None
            request_active = self._request_active
        if response is not None:
            response.close()
        if request_active:
            # The worker still has to unwind; callers must expose STOPPING
            # until the request's finally block clears this state.
            return False
        return True

    def _stream(
        self,
        model: str,
        messages: list[dict],
        *,
        format_json: bool = False,
        cancel: threading.Event | None = None,
    ) -> str:
        with self._lock:
            if self._request_active:
                raise RegionTransportError("concurrent local model request")
            self._cancel_requested = False
            self._request_active = True
            self._request_token += 1
            request_token = self._request_token
        payload = {"model": model, "stream": True, "messages": messages}
        if format_json:
            payload["format"] = "json"
        response = None
        lease = None
        watcher_stop = threading.Event()
        watcher = None
        if cancel is not None:
            watcher = threading.Thread(
                target=self._watch_cancel,
                args=(cancel, watcher_stop, request_token),
                name="region-translation-cancel",
                daemon=True,
            )
            watcher.start()
        try:
            try:
                lease = self._scheduler.acquire(
                    self.endpoint,
                    model,
                    priority=0,
                    timeout=self._queue_timeout,
                    cancel=cancel,
                    cancel_check=self._cancelled,
                )
            except ModelQueueCancelled as exc:
                raise RegionTransportCancelled("model request was cancelled") from exc
            except ModelQueueTimeout as exc:
                raise RegionTransportQueueTimeout("local model queue timed out") from exc
            response = self._session.post(
                f"{self.endpoint}/api/chat",
                json=payload,
                timeout=self._timeout,
                allow_redirects=False,
                stream=True,
            )
            with self._lock:
                if self._cancel_requested:
                    raise RegionTransportCancelled("model request was cancelled")
                self._response = response
            if response.is_redirect or 300 <= response.status_code < 400:
                raise RegionTransportError("Ollama endpoint redirected unexpectedly")
            response.raise_for_status()
            parts: list[str] = []
            done = False
            for raw in response.iter_lines(decode_unicode=False):
                if self._cancelled():
                    raise RegionTransportCancelled("model request was cancelled")
                if not raw:
                    continue
                try:
                    body = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
                except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise RegionTransportError("Ollama stream row is not JSON") from exc
                if not isinstance(body, dict):
                    raise RegionTransportError("Ollama stream row is not an object")
                if "error" in body:
                    raise RegionTransportError("Ollama stream reported an error")
                message = body.get("message")
                if not isinstance(message, dict):
                    raise RegionTransportError("Ollama stream message is invalid")
                content = message.get("content")
                if content is not None and not isinstance(content, str):
                    raise RegionTransportError("Ollama stream content is invalid")
                if content:
                    parts.append(content)
                if body.get("done") is True:
                    done = True
                    break
            if self._cancelled():
                raise RegionTransportCancelled("model request was cancelled")
            if not done:
                raise RegionTransportError("Ollama stream ended without done marker")
            return "".join(parts)
        finally:
            watcher_stop.set()
            if watcher is not None:
                watcher.join(timeout=1)
            if response is not None:
                response.close()
            if lease is not None:
                lease.release()
            with self._lock:
                if response is not None and self._response is response:
                    self._response = None
                self._request_active = False

    def _cancelled(self) -> bool:
        with self._lock:
            return self._cancel_requested

    def _watch_cancel(
        self, cancel: threading.Event, stop: threading.Event, request_token: int
    ) -> None:
        while not stop.wait(0.05):
            if cancel.is_set():
                self._cancel_for_token(request_token)
                return


class OllamaRegionVisionClient(_OllamaStreamingClient):
    def analyze(
        self,
        model: str,
        image: bytes,
        prompt: str,
        cancel: threading.Event | None = None,
    ) -> str:
        if cancel is not None and cancel.is_set():
            self.cancel()
            raise RegionTransportCancelled("model request was cancelled")
        result = self._stream(
            model,
            [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [base64.b64encode(_bounded_image_bytes(image)).decode("ascii")],
                }
            ],
            format_json=True,
            cancel=cancel,
        )
        if cancel is not None and cancel.is_set():
            self.cancel()
            raise RegionTransportCancelled("model request was cancelled")
        return result


class OllamaRegionTranslationClient(_OllamaStreamingClient):
    def complete(
        self,
        model: str,
        system: str,
        user: str,
        cancel: threading.Event | None = None,
    ) -> str:
        if cancel is not None and cancel.is_set():
            self.cancel()
            raise RegionTransportCancelled("model request was cancelled")
        result = self._stream(
            model,
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            cancel=cancel,
        )
        if cancel is not None and cancel.is_set():
            self.cancel()
            raise RegionTransportCancelled("model request was cancelled")
        return result


__all__ = [
    "build_region_translation_clients",
    "OllamaRegionTranslationClient",
    "OllamaRegionVisionClient",
    "MAX_VISION_LONG_EDGE",
    "RegionTransportCancelled",
    "RegionTransportError",
    "RegionTransportQueueTimeout",
]


def build_region_translation_clients(config, *, scheduler: ModelScheduler = DEFAULT_MODEL_SCHEDULER):
    """Build configured local clients without allowing model/endpoint drift."""
    section = config.section("region_translation") if hasattr(config, "section") else config
    queue_timeout = int(section["queue_timeout_seconds"])
    vision = OllamaRegionVisionClient(
        base_url=str(section["ocr_base_url"]),
        timeout=int(section["ocr_timeout_seconds"]),
        scheduler=scheduler,
        queue_timeout=queue_timeout,
    )
    translation = OllamaRegionTranslationClient(
        base_url=str(section["translation_base_url"]),
        timeout=int(section["translation_timeout_seconds"]),
        scheduler=scheduler,
        queue_timeout=queue_timeout,
    )
    return vision, translation


def _bounded_image_bytes(image: bytes) -> bytes:
    """Downscale oversized valid images before sending them to the vision model."""
    try:
        with Image.open(BytesIO(image)) as source:
            source.load()
            width, height = source.size
            if max(width, height) <= MAX_VISION_LONG_EDGE:
                return image
            scale = MAX_VISION_LONG_EDGE / max(width, height)
            resized = source.resize(
                (max(1, round(width * scale)), max(1, round(height * scale))),
                Image.Resampling.LANCZOS,
            )
            output = BytesIO()
            resized.save(output, format="PNG")
            return output.getvalue()
    except (OSError, ValueError):
        # The transport remains byte-oriented for callers that provide an
        # opaque test or pre-encoded payload; Ollama will report model-side
        # validation errors for invalid image bytes.
        return image

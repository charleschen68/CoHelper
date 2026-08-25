"""Closeable loopback Ollama transports for region OCR and translation."""

from __future__ import annotations

import base64
import json
import threading
from io import BytesIO
from urllib.parse import urlparse

from PIL import Image
import requests


MAX_VISION_LONG_EDGE = 3072


class RegionTransportError(RuntimeError):
    """The local model transport returned an invalid protocol response."""


class RegionTransportCancelled(RegionTransportError):
    """The active response was closed by an explicit cancellation."""


class _OllamaStreamingClient:
    def __init__(self, base_url: str = "http://127.0.0.1:11434", timeout: int = 60, session=requests):
        host = urlparse(base_url).hostname
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("region translation endpoint must be local")
        if timeout <= 0:
            raise ValueError("region translation timeout must be positive")
        self.endpoint = base_url.rstrip("/")
        self._timeout = timeout
        self._session = session
        self._lock = threading.RLock()
        self._response = None
        self._request_active = False
        self._cancel_requested = False

    def cancel(self) -> bool:
        with self._lock:
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

    def _stream(self, model: str, messages: list[dict], *, format_json: bool = False) -> str:
        with self._lock:
            if self._request_active:
                raise RegionTransportError("concurrent local model request")
            self._cancel_requested = False
            self._request_active = True
        payload = {"model": model, "stream": True, "messages": messages}
        if format_json:
            payload["format"] = "json"
        response = None
        try:
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
                message = body.get("message") or {}
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
            if response is not None:
                response.close()
            with self._lock:
                if response is not None and self._response is response:
                    self._response = None
                self._request_active = False

    def _cancelled(self) -> bool:
        with self._lock:
            return self._cancel_requested


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
        )
        if cancel is not None and cancel.is_set():
            self.cancel()
            raise RegionTransportCancelled("model request was cancelled")
        return result


__all__ = [
    "OllamaRegionTranslationClient",
    "OllamaRegionVisionClient",
    "MAX_VISION_LONG_EDGE",
    "RegionTransportCancelled",
    "RegionTransportError",
]


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

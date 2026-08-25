"""Closeable loopback Ollama transports for region OCR and translation."""

from __future__ import annotations

import base64
import json
import threading
from urllib.parse import urlparse

import requests


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
        self._cancel_requested = False

    def cancel(self) -> bool:
        with self._lock:
            self._cancel_requested = True
            response = self._response
            self._response = None
        if response is not None:
            response.close()
        return True

    def _stream(self, model: str, messages: list[dict], *, format_json: bool = False) -> str:
        with self._lock:
            if self._response is not None:
                raise RegionTransportError("concurrent local model request")
            self._cancel_requested = False
        payload = {"model": model, "stream": True, "messages": messages}
        if format_json:
            payload["format"] = "json"
        response = self._session.post(
            f"{self.endpoint}/api/chat",
            json=payload,
            timeout=self._timeout,
            allow_redirects=False,
            stream=True,
        )
        with self._lock:
            if self._cancel_requested:
                response.close()
                raise RegionTransportCancelled("model request was cancelled")
            self._response = response
        try:
            if response.is_redirect or 300 <= response.status_code < 400:
                raise RegionTransportError("Ollama endpoint redirected unexpectedly")
            response.raise_for_status()
            parts: list[str] = []
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
                    break
            if self._cancelled():
                raise RegionTransportCancelled("model request was cancelled")
            return "".join(parts)
        finally:
            response.close()
            with self._lock:
                if self._response is response:
                    self._response = None

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
                    "images": [base64.b64encode(image).decode("ascii")],
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
    "RegionTransportCancelled",
    "RegionTransportError",
]

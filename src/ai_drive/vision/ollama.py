"""Ollama multimodal client with a strict local-only endpoint."""

from __future__ import annotations

import base64
from urllib.parse import urlparse

import requests


class OllamaVisionClient:
    def __init__(self, base_url: str = "http://127.0.0.1:11434", timeout: int = 90, session=requests):
        host = urlparse(base_url).hostname
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("vision endpoint must be local")
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._session = session

    def analyze(self, model: str, image: bytes, prompt: str) -> str:
        response = self._session.post(
            f"{self._base_url}/api/chat",
            json={
                "model": model,
                "stream": False,
                "format": "json",
                "messages": [
                    {
                        "role": "user",
                        "content": prompt,
                        "images": [base64.b64encode(image).decode("ascii")],
                    }
                ],
            },
            timeout=self._timeout,
            allow_redirects=False,
        )
        if response.is_redirect or 300 <= response.status_code < 400:
            raise RuntimeError("Ollama vision endpoint redirected unexpectedly")
        response.raise_for_status()
        try:
            return str(response.json()["message"]["content"])
        except (KeyError, TypeError) as exc:
            raise RuntimeError("Ollama vision response lacks message.content") from exc

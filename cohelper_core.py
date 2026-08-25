"""Core services for cohelper.

The module deliberately has no AppKit dependency so the orchestration and
privacy rules can be tested on any machine.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

import requests
import yaml

from apps.clipboard_helper.service import route_clipboard_text
from ai_drive.voice.router import VoiceCommandRouter, VoiceCommandRouterError
from cohelper_setup import EnvironmentDoctor, KeychainStore, resolve_command


APP_NAME = "cohelper"
APP_SUPPORT = Path.home() / "Library" / "Application Support" / APP_NAME
CONFIG_PATH = APP_SUPPORT / "config.yaml"


DEFAULT_CONFIG = {
    "features": {
        "translation": True,
        "knowledge_search": True,
        "knowledge_answer": True,
        "overlay": True,
        "voice_input": False,
        "voice_output": False,
        "voice_direct_actions": False,
        "region_translation": False,
    },
    "privacy": {"allow_external_api": False},
    "clipboard": {"min_chars": 3, "max_chars": 20000, "poll_interval_ms": 400, "debounce_ms": 500, "process_plain_text_only": True},
    "knowledge": {"collection": "jarvis-wiki", "source_path": "", "limit": 5, "query_timeout_seconds": 20, "max_summary_source_chars": 50000},
    "translation": {"provider": "ollama", "model": "translategemma:4b", "base_url": "http://127.0.0.1:11434", "timeout_seconds": 60, "credential_account": "translation"},
    "summary": {"provider": "ollama", "model": "qwen3:8b", "base_url": "http://127.0.0.1:11434", "timeout_seconds": 120, "credential_account": "summary"},
    "qmd": {"command": "qmd", "index": "index", "no_rerank": False, "models": {"embedding": "", "reranking": "", "generation": ""}},
    "vision": {"model": "qwen2.5vl:7b", "base_url": "http://127.0.0.1:11434", "timeout_seconds": 90},
    "voice": {
        "sample_rate": 16_000,
        "channels": 1,
        "vad_threshold": 500,
        "silence_seconds": 0.8,
        "server_executable": "whisper-server",
        "model_path": str(APP_SUPPORT / "voice" / "models" / "ggml-large-v3-turbo-q5_0.bin"),
        "server_port": 18080,
        "language": "auto",
        "command_aliases": {},
        "command_instructions": {},
    },
    "actions": {
        "allowed_bundle_ids": ["com.apple.Safari", "com.apple.TextEdit"],
        "allowed_capabilities": [
            "com.apple.Safari|AXButton|Reload this page|AXToolbar|",
            "com.apple.Safari|AXButton|刷新按钮|AXToolbar|",
        ],
        "minimum_confidence": 0.75,
        "screenshot_max_age_seconds": 5,
        "confirmation_ttl_seconds": 30,
    },
    "telegram": {"enabled": False, "allowed_user_id": 0, "allowed_chat_id": 0, "credential_account": "telegram"},
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class ConfigError(ValueError):
    pass


class Config:
    def __init__(self, values: dict[str, Any]):
        migrated = _migrate_config(values)
        self.values = _deep_merge(DEFAULT_CONFIG, migrated)
        self._validate()

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        if not path.exists():
            return cls({})
        try:
            with path.open(encoding="utf-8") as fh:
                values = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise ConfigError(f"无法读取配置文件 {path}: {exc}") from exc
        if not isinstance(values, dict):
            raise ConfigError("配置文件根节点必须是 YAML mapping")
        return cls(values)

    def save(self, path: Path = CONFIG_PATH) -> None:
        self._validate()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(self.values, fh, allow_unicode=True, sort_keys=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)

    def _validate(self) -> None:
        for section_name in ("features", "privacy", "clipboard", "knowledge", "translation", "summary", "qmd", "vision", "voice", "actions", "telegram"):
            if not isinstance(self.values.get(section_name), dict):
                raise ConfigError(f"{section_name} 必须是 mapping")
        clipboard = self.values["clipboard"]
        try:
            min_chars = int(clipboard["min_chars"])
            max_chars = int(clipboard["max_chars"])
            poll_interval = int(clipboard["poll_interval_ms"])
            debounce = int(clipboard["debounce_ms"])
            knowledge_limit = int(self.values["knowledge"]["limit"])
            max_source_chars = int(self.values["knowledge"]["max_summary_source_chars"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"数值配置无效：{exc}") from exc
        if not 0 <= min_chars <= max_chars:
            raise ConfigError("clipboard.min_chars 必须不大于 max_chars 且不能为负数")
        if poll_interval < 100:
            raise ConfigError("clipboard.poll_interval_ms 不能小于 100")
        if debounce < 0:
            raise ConfigError("clipboard.debounce_ms 不能为负数")
        if knowledge_limit < 1:
            raise ConfigError("knowledge.limit 必须大于 0")
        if max_source_chars < 1000:
            raise ConfigError("knowledge.max_summary_source_chars 不能小于 1000")
        for feature, enabled in self.values["features"].items():
            if not isinstance(enabled, bool):
                raise ConfigError(f"features.{feature} 必须是 boolean")
        if self.values["features"].get("knowledge_answer") and not self.values["features"].get("knowledge_search"):
            raise ConfigError("knowledge_answer 依赖 knowledge_search")
        if self.values["features"].get("voice_direct_actions") and not self.values["features"].get("voice_input"):
            raise ConfigError("voice_direct_actions 依赖 voice_input")
        if self.values["features"].get("voice_direct_actions") and not self.values["features"].get("overlay"):
            raise ConfigError("voice_direct_actions 依赖 overlay")
        for section_name in ("translation", "summary"):
            section = self.values[section_name]
            if section.get("provider") not in {"ollama", "openai-compatible"}:
                raise ConfigError(f"不支持的 {section_name}.provider: {section.get('provider')}")
            if not str(section.get("model", "")).strip():
                raise ConfigError(f"{section_name}.model 不能为空")
            if not urlparse(str(section.get("base_url", ""))).scheme:
                raise ConfigError(f"{section_name}.base_url 必须是完整 URL")
        summary = self.values["summary"]
        summary_url = urlparse(str(summary.get("base_url", "")))
        if summary.get("provider") != "ollama":
            raise ConfigError("summary.provider 必须是本机 ollama")
        if summary.get("model") != "qwen3:8b":
            raise ConfigError("summary.model 必须是 qwen3:8b")
        if summary_url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ConfigError("summary.base_url 必须是本机 Ollama 地址")
        vision = self.values["vision"]
        vision_url = urlparse(str(vision.get("base_url", "")))
        if vision_url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ConfigError("vision.base_url 必须是本机 Ollama 地址")
        if vision.get("model") != "qwen2.5vl:7b":
            raise ConfigError("vision.model 必须是 qwen2.5vl:7b")
        voice = self.values["voice"]
        if voice.get("sample_rate") != 16_000 or voice.get("channels") != 1:
            raise ConfigError("voice 必须使用 16 kHz mono")
        if not isinstance(voice.get("server_executable"), str) or not voice["server_executable"].strip():
            raise ConfigError("voice.server_executable 不能为空")
        if not isinstance(voice.get("model_path"), str) or not voice["model_path"].strip():
            raise ConfigError("voice.model_path 不能为空")
        try:
            vad_threshold = int(voice["vad_threshold"])
            silence_seconds = float(voice["silence_seconds"])
            server_port = int(voice["server_port"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"voice 数值配置无效：{exc}") from exc
        if vad_threshold < 0 or silence_seconds <= 0:
            raise ConfigError("voice VAD 配置必须有效")
        if not 1024 <= server_port <= 65535:
            raise ConfigError("voice.server_port 必须在 1024 到 65535 之间")
        command_aliases = voice.get("command_aliases")
        if not isinstance(command_aliases, dict):
            raise ConfigError("voice.command_aliases 必须是 mapping")
        if any(not isinstance(command, str) or not command.strip() for command in command_aliases):
            raise ConfigError("voice.command_aliases 的命令名不能为空")
        if any(
            not isinstance(phrases, list)
            or not phrases
            or not all(isinstance(phrase, str) and phrase.strip() for phrase in phrases)
            for phrases in command_aliases.values()
        ):
            raise ConfigError("voice.command_aliases 的短语必须是非空字符串列表")
        try:
            VoiceCommandRouter(command_aliases)
        except VoiceCommandRouterError as exc:
            raise ConfigError(f"voice.command_aliases 无效：{exc}") from exc
        command_instructions = voice.get("command_instructions")
        if not isinstance(command_instructions, dict) or any(
            not isinstance(command, str) or not command.strip()
            or not isinstance(instruction, str) or not instruction.strip()
            for command, instruction in command_instructions.items()
        ):
            raise ConfigError("voice.command_instructions 必须是非空字符串 mapping")
        allowed = self.values["actions"].get("allowed_bundle_ids")
        if not isinstance(allowed, list) or not allowed or not all(isinstance(item, str) and item for item in allowed):
            raise ConfigError("actions.allowed_bundle_ids 必须是非空字符串列表")
        allowed_capabilities = self.values["actions"].get("allowed_capabilities")
        if (
            not isinstance(allowed_capabilities, list)
            or not allowed_capabilities
            or not all(_valid_capability(item) for item in allowed_capabilities)
        ):
            raise ConfigError(
                "actions.allowed_capabilities 必须使用 bundle|role|title|ancestor_role|identifier 格式"
            )
        actions = self.values["actions"]
        try:
            confidence = float(actions["minimum_confidence"])
            screenshot_age = float(actions["screenshot_max_age_seconds"])
            confirmation_ttl = float(actions["confirmation_ttl_seconds"])
            vision_timeout = int(vision["timeout_seconds"])
            allowed_user_id = int(self.values["telegram"]["allowed_user_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"AI Drive 数值配置无效：{exc}") from exc
        if not 0 <= confidence <= 1:
            raise ConfigError("actions.minimum_confidence 必须在 0 到 1 之间")
        if screenshot_age <= 0 or vision_timeout <= 0:
            raise ConfigError("视觉与动作超时必须大于 0")
        if confirmation_ttl != 30:
            raise ConfigError("actions.confirmation_ttl_seconds 必须固定为 30")
        allowed_chat_id = int(self.values["telegram"].get("allowed_chat_id", 0))
        if allowed_user_id < 0 or (self.values["telegram"]["enabled"] and allowed_user_id == 0):
            raise ConfigError("启用 Telegram 时 telegram.allowed_user_id 必须为正整数")
        if allowed_chat_id < 0 or (self.values["telegram"]["enabled"] and allowed_chat_id == 0):
            raise ConfigError("启用 Telegram 时 telegram.allowed_chat_id 必须为正整数")

    def enabled(self, feature: str) -> bool:
        return bool(self.values["features"].get(feature, False))

    def section(self, name: str) -> dict[str, Any]:
        return self.values[name]


def _valid_capability(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parts = value.split("|")
    return len(parts) == 5 and all(part.strip() for part in parts[:4])


def _migrate_config(values: dict[str, Any]) -> dict[str, Any]:
    migrated = _deep_merge({}, values)
    features = migrated.get("features")
    if isinstance(features, dict) and "knowledge_summary" in features:
        features.setdefault("knowledge_answer", features["knowledge_summary"])
        del features["knowledge_summary"]
    summary = migrated.get("summary")
    previous_default = "rafw007/qwen3.6-35b-A3b-mlx-claude-coder-abliterated:latest"
    if isinstance(summary, dict) and summary.get("model") == previous_default:
        summary["model"] = "qwen3:8b"
    return migrated


SECRET_PATTERNS = (
    re.compile(r"-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----"),
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"(?i)\b(?:api[_ -]?key|token|password|secret)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=:-]{8,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
)


def contains_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


@dataclass
class ModelResult:
    text: str = ""
    error: str | None = None
    provider: str = ""
    elapsed_seconds: float = 0.0


class ModelProvider:
    def complete(self, system: str, user: str, *, model: str, base_url: str, timeout: int, api_key: str | None = None) -> str:
        raise NotImplementedError

    def stream(self, system: str, user: str, *, model: str, base_url: str, timeout: int, api_key: str | None = None, cancel: threading.Event | None = None):
        yield self.complete(system, user, model=model, base_url=base_url, timeout=timeout, api_key=api_key)


class OllamaProvider(ModelProvider):
    def complete(self, system: str, user: str, *, model: str, base_url: str, timeout: int, api_key: str | None = None) -> str:
        response = requests.post(
            f"{base_url.rstrip('/')}/api/chat",
            json={"model": model, "stream": False, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
            timeout=timeout,
            allow_redirects=False,
        )
        if response.is_redirect or 300 <= getattr(response, "status_code", 200) < 400:
            raise RuntimeError("模型端点返回重定向，已阻止转发剪贴板内容")
        response.raise_for_status()
        body = response.json()
        try:
            return str(body["message"]["content"])
        except (KeyError, TypeError) as exc:
            raise RuntimeError(f"Ollama 响应缺少 message.content: {body!r}") from exc

    def stream(self, system: str, user: str, *, model: str, base_url: str, timeout: int, api_key: str | None = None, cancel: threading.Event | None = None):
        response = requests.post(
            f"{base_url.rstrip('/')}/api/chat",
            json={"model": model, "stream": True, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
            timeout=timeout,
            allow_redirects=False,
            stream=True,
        )
        try:
            if response.is_redirect or 300 <= getattr(response, "status_code", 200) < 400:
                raise RuntimeError("模型端点返回重定向，已阻止转发知识库内容")
            response.raise_for_status()
            for raw in response.iter_lines(decode_unicode=True):
                if cancel and cancel.is_set():
                    return
                if not raw:
                    continue
                body = json.loads(raw)
                if not isinstance(body, dict):
                    raise RuntimeError("Ollama 流响应不是 JSON object")
                message = body.get("message") or {}
                delta = message.get("content")
                if delta:
                    yield str(delta)
                if body.get("done"):
                    return
        finally:
            response.close()


class OpenAICompatibleProvider(ModelProvider):
    def complete(self, system: str, user: str, *, model: str, base_url: str, timeout: int, api_key: str | None = None) -> str:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        response = requests.post(
            f"{base_url.rstrip('/')}/chat/completions",
            json={"model": model, "stream": False, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
            headers=headers,
            timeout=timeout,
            allow_redirects=False,
        )
        if response.is_redirect or 300 <= getattr(response, "status_code", 200) < 400:
            raise RuntimeError("模型端点返回重定向，已阻止转发剪贴板内容")
        response.raise_for_status()
        body = response.json()
        try:
            return str(body["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"OpenAI-compatible 响应格式错误: {body!r}") from exc


PROVIDERS = {"ollama": OllamaProvider, "openai-compatible": OpenAICompatibleProvider}
_MODEL_LOCKS: dict[tuple[str, str, str], threading.Lock] = {}
_MODEL_LOCKS_GUARD = threading.Lock()


def _model_lock(provider: str, base_url: str, model: str) -> threading.Lock:
    key = (provider, base_url, model)
    with _MODEL_LOCKS_GUARD:
        return _MODEL_LOCKS.setdefault(key, threading.Lock())


def make_provider(name: str) -> ModelProvider:
    try:
        return PROVIDERS[name]()
    except KeyError as exc:
        raise ConfigError(f"不支持的模型 provider: {name}") from exc


TRANSLATION_SYSTEM = """你是专业技术翻译。将用户提供的内容翻译成简洁、准确、自然的中文。
保留代码块、命令、路径、URL、专有名词和 Markdown 结构；如果输入已经是中文，说明其英文技术含义但不要臆造内容。只输出翻译结果。"""
SUMMARY_SYSTEM = """你是一个严谨的个人知识库助手。只能依据提供的知识库来源回答。
如果来源不足以支持结论，明确说“知识库没有足够依据”，不要补充猜测。
使用中文，先给出结论，再列出关键依据；保留来源路径标记。"""


class ModelService:
    def __init__(self, config: Config, kind: str):
        section = config.section(kind)
        self.config = config
        self.kind = kind
        self.section = section
        self.provider = make_provider(str(section["provider"]))

    def run(self, prompt: str, system: str, cancel: threading.Event | None = None) -> ModelResult:
        if self.kind == "translation" and not self.config.enabled("translation"):
            return ModelResult(provider="disabled")
        if self.kind == "summary" and not self.config.enabled("knowledge_answer"):
            return ModelResult(provider="disabled")
        started = time.monotonic()
        try:
            lock = _model_lock(str(self.section["provider"]), str(self.section["base_url"]), str(self.section["model"]))
            while not lock.acquire(timeout=0.1):
                if cancel and cancel.is_set():
                    return ModelResult(error="请求已取消", provider="cancelled", elapsed_seconds=time.monotonic() - started)
            if cancel and cancel.is_set():
                lock.release()
                return ModelResult(error="请求已取消", provider="cancelled", elapsed_seconds=time.monotonic() - started)
            api_key = None
            try:
                if self.section["provider"] == "openai-compatible":
                    api_key = KeychainStore().get(str(self.section.get("credential_account", self.kind)))
                text = self.provider.complete(system, prompt, model=self.section["model"], base_url=self.section["base_url"], timeout=int(self.section["timeout_seconds"]), api_key=api_key)
            finally:
                lock.release()
            return ModelResult(text=text, provider=self.section["provider"], elapsed_seconds=time.monotonic() - started)
        except Exception as exc:  # boundary: provider errors become user-visible results
            return ModelResult(error=f"{type(exc).__name__}: {exc}", provider=self.section["provider"], elapsed_seconds=time.monotonic() - started)

    def stream(self, prompt: str, system: str, cancel: threading.Event | None, on_delta: Callable[[str], None]) -> ModelResult:
        if self.kind == "summary" and not self.config.enabled("knowledge_answer"):
            return ModelResult(provider="disabled")
        started = time.monotonic()
        try:
            lock = _model_lock(str(self.section["provider"]), str(self.section["base_url"]), str(self.section["model"]))
            while not lock.acquire(timeout=0.1):
                if cancel and cancel.is_set():
                    return ModelResult(error="请求已取消", provider="cancelled", elapsed_seconds=time.monotonic() - started)
            if cancel and cancel.is_set():
                lock.release()
                return ModelResult(error="请求已取消", provider="cancelled", elapsed_seconds=time.monotonic() - started)
            api_key = None
            text_parts = []
            try:
                if self.section["provider"] == "openai-compatible":
                    api_key = KeychainStore().get(str(self.section.get("credential_account", self.kind)))
                for delta in self.provider.stream(
                    system,
                    prompt,
                    model=self.section["model"],
                    base_url=self.section["base_url"],
                    timeout=int(self.section["timeout_seconds"]),
                    api_key=api_key,
                    cancel=cancel,
                ):
                    if cancel and cancel.is_set():
                        return ModelResult(error="请求已取消", provider="cancelled", elapsed_seconds=time.monotonic() - started)
                    text_parts.append(delta)
                    on_delta(delta)
            finally:
                lock.release()
            return ModelResult(text="".join(text_parts), provider=self.section["provider"], elapsed_seconds=time.monotonic() - started)
        except Exception as exc:
            return ModelResult(error=f"{type(exc).__name__}: {exc}", provider=self.section["provider"], elapsed_seconds=time.monotonic() - started)


@dataclass
class KnowledgeHit:
    path: str
    snippet: str = ""
    score: float | None = None


class QmdError(RuntimeError):
    pass


class QmdClient:
    def __init__(self, config: Config):
        self.config = config

    def _command(self, *args: str) -> list[str]:
        qmd = str(self.config.section("qmd")["command"])
        executable = resolve_command(qmd) or qmd
        command = [executable, "--index", str(self.config.section("qmd")["index"]), *args]
        return command

    def _environment(self) -> dict[str, str]:
        return EnvironmentDoctor(self.config.values).qmd_environment()

    def search(self, query: str, cancel: threading.Event | None = None) -> list[KnowledgeHit]:
        if not self.config.enabled("knowledge_search"):
            return []
        knowledge = self.config.section("knowledge")
        qmd = self.config.section("qmd")
        args = ["query", query, "-c", str(knowledge["collection"]), "-n", str(knowledge["limit"]), "--format", "json", "--full-path"]
        if qmd.get("no_rerank"):
            args.append("--no-rerank")
        try:
            process = subprocess.Popen(self._command(*args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self._environment())
        except OSError as exc:
            raise QmdError(f"无法启动 QMD: {exc}") from exc
        timeout = int(knowledge["query_timeout_seconds"])
        output = self._communicate(process, timeout, cancel, "QMD 查询")
        if output is None:
            return []
        stdout, stderr = output
        if process.returncode:
            raise QmdError(stderr.strip() or f"QMD 退出码 {process.returncode}")
        return self._parse_hits(stdout)

    @staticmethod
    def _parse_hits(raw: str) -> list[KnowledgeHit]:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = None
        records: Iterable[Any]
        if isinstance(payload, list):
            records = payload
        elif isinstance(payload, dict):
            records = payload.get("results") or payload.get("matches") or payload.get("documents") or []
        else:
            records = []
        hits: list[KnowledgeHit] = []
        for record in records:
            if isinstance(record, str):
                hits.append(KnowledgeHit(record))
            elif isinstance(record, dict):
                path = record.get("path") or record.get("file") or record.get("uri") or record.get("filepath")
                if path:
                    hits.append(KnowledgeHit(str(path), str(record.get("snippet") or record.get("text") or ""), record.get("score")))
        if hits:
            return hits
        # Keep a conservative fallback for QMD versions whose JSON schema changes.
        return [KnowledgeHit(path) for path in re.findall(r"(?:qmd://[^\s\"']+|/[^\s\"']+\.md)", raw)]

    def get(self, hit: KnowledgeHit, cancel: threading.Event | None = None) -> str:
        try:
            process = subprocess.Popen(self._command("get", hit.path, "--full-path"), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self._environment())
        except OSError as exc:
            raise QmdError(f"无法启动 QMD get: {exc}") from exc
        timeout = int(self.config.section("knowledge")["query_timeout_seconds"])
        output = self._communicate(process, timeout, cancel, "QMD get")
        if output is None:
            return ""
        stdout, stderr = output
        if process.returncode:
            raise QmdError(stderr.strip() or f"QMD get 退出码 {process.returncode}")
        return stdout

    @staticmethod
    def _communicate(
        process: subprocess.Popen[str],
        timeout: int,
        cancel: threading.Event | None,
        operation: str,
    ) -> tuple[str, str] | None:
        deadline = time.monotonic() + timeout
        while True:
            if cancel and cancel.is_set():
                process.kill()
                process.communicate()
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                process.communicate()
                raise QmdError(f"{operation}超过 {timeout} 秒")
            try:
                return process.communicate(timeout=min(0.1, remaining))
            except subprocess.TimeoutExpired:
                continue


def build_knowledge_prompt(
    query: str,
    documents: list[tuple[KnowledgeHit, str]],
    max_source_chars: int = 50000,
    task: str = "answer",
) -> str:
    label = "用户段落" if task == "summarize" else "用户问题"
    instruction = "请总结该段落与知识库来源的关系。" if task == "summarize" else "请依据知识库来源回答。"
    if not documents:
        return f"{label}：\n{query}\n\n没有检索到知识库来源。请明确说明没有足够依据。"
    remaining = max_source_chars
    rendered = []
    for hit, content in documents:
        if remaining <= 0:
            break
        excerpt = content[:remaining]
        rendered.append(f"来源：{hit.path}\n{excerpt}")
        remaining -= len(excerpt)
    sources = "\n\n".join(rendered)
    return f"处理要求：{instruction}\n\n{label}：\n{query}\n\n知识库来源：\n{sources}"


@dataclass
class TaskCallbacks:
    on_started: Callable[[int, str], None] = lambda _generation, _text: None
    on_translation: Callable[[int, ModelResult], None] = lambda _generation, _result: None
    on_knowledge: Callable[[int, list[KnowledgeHit]], None] = lambda _generation, _hits: None
    on_summary: Callable[[int, ModelResult], None] = lambda _generation, _result: None
    on_summary_delta: Callable[[int, str], None] = lambda _generation, _delta: None
    on_error: Callable[[int, str], None] = lambda _generation, _error: None
    on_rejected: Callable[[int, str], None] = lambda _generation, _reason: None
    on_finished: Callable[[int], None] = lambda _generation: None


class TaskCoordinator:
    def __init__(self, config: Config, callbacks: TaskCallbacks | None = None):
        self.config = config
        self.callbacks = callbacks or TaskCallbacks()
        self._lock = threading.Lock()
        self._generation = 0
        self._cancel = threading.Event()

    def submit(self, text: str) -> int | None:
        with self._lock:
            config = Config(deepcopy(self.config.values))
            if not (config.enabled("translation") or config.enabled("knowledge_search")):
                return None
            if not text or len(text) < int(config.section("clipboard")["min_chars"]):
                return None
            self._generation += 1
            generation = self._generation
            self._cancel.set()
            self._cancel = threading.Event()
            cancel = self._cancel
        rejection = self._rejection_reason(text, config)
        if rejection:
            self.callbacks.on_rejected(generation, rejection)
            return generation
        self.callbacks.on_started(generation, text)
        threading.Thread(target=self._run, args=(generation, text, cancel, config), daemon=True).start()
        return generation

    def update_config(self, config: Config) -> int:
        """Cancel in-flight work before atomically adopting a new configuration."""
        with self._lock:
            self._generation += 1
            self._cancel.set()
            self.config = config
            return self._generation

    def cancel(self) -> None:
        with self._lock:
            self._generation += 1
            self._cancel.set()

    def _rejection_reason(self, text: str, config: Config) -> str | None:
        maximum = int(config.section("clipboard")["max_chars"])
        if len(text) > maximum:
            return f"剪贴板内容有 {len(text)} 个字符，超过配置上限 {maximum}；未启动任何模型或 QMD。"
        if "\x00" in text:
            return "剪贴板内容包含 NUL 字符，疑似二进制数据；已跳过。"
        if text:
            control_count = sum(1 for char in text if ord(char) < 32 and char not in "\n\r\t")
            if control_count / len(text) > 0.05:
                return "剪贴板内容包含过多控制字符，疑似二进制数据；已跳过。"
        if len(text) > 1000 and len(set(text)) <= 3:
            return "剪贴板内容高度重复；已跳过以避免浪费模型资源。"
        return None

    def _current(self, generation: int, cancel: threading.Event) -> bool:
        with self._lock:
            return generation == self._generation and not cancel.is_set()

    def _run(self, generation: int, text: str, cancel: threading.Event, config: Config) -> None:
        jobs: list[threading.Thread] = []
        if config.enabled("translation"):
            jobs.append(threading.Thread(target=self._translation, args=(generation, text, cancel, config), daemon=True))
        if config.enabled("knowledge_search"):
            jobs.append(threading.Thread(target=self._knowledge, args=(generation, text, cancel, config), daemon=True))
        for job in jobs:
            job.start()
        for job in jobs:
            job.join()
        if self._current(generation, cancel):
            self.callbacks.on_finished(generation)

    def _translation(
        self, generation: int, text: str, cancel: threading.Event, config: Config | None = None
    ) -> None:
        config = config or self.config
        if self._external_blocked(text, "translation", config):
            if self._current(generation, cancel):
                self.callbacks.on_translation(
                    generation, ModelResult(error="外部 API 被隐私策略阻止", provider="blocked")
                )
            return
        result = ModelService(config, "translation").run(text, TRANSLATION_SYSTEM, cancel)
        if self._current(generation, cancel):
            self.callbacks.on_translation(generation, result)

    def _knowledge(
        self, generation: int, text: str, cancel: threading.Event, config: Config | None = None
    ) -> None:
        config = config or self.config
        try:
            route = route_clipboard_text(text)
            qmd = QmdClient(config)
            hits = qmd.search(route.query, cancel)
            if self._current(generation, cancel):
                self.callbacks.on_knowledge(generation, hits)
            if not config.enabled("knowledge_answer") or not self._current(generation, cancel):
                return
            if not hits:
                self.callbacks.on_summary(
                    generation, ModelResult(text="知识库中没有足够依据。", provider="knowledge")
                )
                return
            documents = [(hit, qmd.get(hit, cancel)) for hit in hits]
            if self._external_blocked(text, "summary", config):
                result = ModelResult(error="外部 API 被隐私策略阻止", provider="blocked")
            else:
                prompt = build_knowledge_prompt(
                    route.query,
                    documents,
                    int(config.section("knowledge")["max_summary_source_chars"]),
                    route.task,
                )
                if self._external_blocked(prompt, "summary", config):
                    result = ModelResult(error="知识库来源包含疑似敏感信息，外部 API 被隐私策略阻止", provider="blocked")
                else:
                    result = ModelService(config, "summary").stream(
                        prompt,
                        SUMMARY_SYSTEM,
                        cancel,
                        lambda delta: self.callbacks.on_summary_delta(generation, delta)
                        if self._current(generation, cancel)
                        else None,
                    )
            if self._current(generation, cancel):
                self.callbacks.on_summary(generation, result)
        except Exception as exc:
            if self._current(generation, cancel):
                self.callbacks.on_error(generation, f"知识库处理失败：{type(exc).__name__}: {exc}")

    def _external_blocked(self, text: str, kind: str, config: Config | None = None) -> bool:
        config = config or self.config
        section = config.section("translation" if kind == "translation" else "summary")
        host = urlparse(str(section.get("base_url", ""))).hostname
        local_host = host in {"127.0.0.1", "localhost", "::1"}
        is_external = not local_host
        return is_external and (not config.section("privacy")["allow_external_api"] or contains_secret(text))

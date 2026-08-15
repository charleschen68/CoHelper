"""Dependency diagnostics, controlled installation, and Keychain access."""

from __future__ import annotations

import json
import os
import platform
import re
import signal
import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import urlparse

import requests


KEYCHAIN_SERVICE = "com.charleschen68.cohelper"
STANDARD_COMMAND_DIRS = (Path("/opt/homebrew/bin"), Path("/usr/local/bin"))
SETUP_STATE_PATH = Path.home() / "Library" / "Application Support" / "cohelper" / "setup-state.json"


def resolve_command(command: str) -> str | None:
    """Resolve commands when launched from Finder's minimal PATH."""
    candidate = Path(command).expanduser()
    if candidate.is_absolute():
        return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None
    resolved = shutil.which(command)
    if resolved:
        return resolved
    for directory in STANDARD_COMMAND_DIRS:
        candidate = directory / command
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


class KeychainError(RuntimeError):
    pass


class KeychainStore:
    """Store API credentials in macOS Keychain without putting secrets in YAML."""

    def __init__(self, service: str = KEYCHAIN_SERVICE):
        self.service = service

    def get(self, account: str) -> str | None:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", self.service, "-a", account, "-w"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 44:
            return None
        if result.returncode:
            raise KeychainError(result.stderr.strip() or "无法读取 macOS Keychain")
        return result.stdout.rstrip("\n")

    def set(self, account: str, secret: str) -> None:
        if not account or not secret:
            raise ValueError("Keychain account 和 secret 不能为空")
        # `-w` without a value reads from stdin, keeping the secret out of argv.
        result = subprocess.run(
            ["security", "add-generic-password", "-U", "-s", self.service, "-a", account, "-w"],
            input=secret + "\n",
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise KeychainError(result.stderr.strip() or "无法写入 macOS Keychain")

    def delete(self, account: str) -> None:
        result = subprocess.run(
            ["security", "delete-generic-password", "-s", self.service, "-a", account],
            capture_output=True,
            text=True,
        )
        if result.returncode not in (0, 44):
            raise KeychainError(result.stderr.strip() or "无法删除 macOS Keychain 凭据")


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str
    remediation: str = ""


@dataclass
class SetupState:
    setup_complete: bool = False
    qmd_embedding_model: str = ""

    @classmethod
    def load(cls, path: Path = SETUP_STATE_PATH) -> "SetupState":
        if not path.exists():
            return cls()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return cls(bool(payload.get("setup_complete", False)), str(payload.get("qmd_embedding_model", "")))
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: Path = SETUP_STATE_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)


@dataclass(frozen=True)
class DiagnosticReport:
    checks: tuple[CheckResult, ...]
    generated_at: float

    @property
    def ready(self) -> bool:
        return all(check.ok for check in self.checks)

    def as_text(self) -> str:
        lines = []
        for check in self.checks:
            lines.append(f"{'✓' if check.ok else '✗'} {check.name}: {check.detail}")
            if not check.ok and check.remediation:
                lines.append(f"  修复：{check.remediation}")
        return "\n".join(lines)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"generated_at": self.generated_at, "ready": self.ready, "checks": [asdict(check) for check in self.checks]}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class EnvironmentDoctor:
    def __init__(self, config: Mapping[str, object]):
        self.config = config

    def run(self) -> DiagnosticReport:
        checks = [self._mac_check()]
        if self._section("features").get("knowledge_search"):
            checks.extend((self._node_check(), self._qmd_check()))
        models_by_endpoint = self.ollama_models_by_endpoint()
        if models_by_endpoint:
            checks.append(self._ollama_service_check(set(models_by_endpoint)))
            checks.extend(self._ollama_models_check(base_url, models) for base_url, models in sorted(models_by_endpoint.items()))
        checks.extend(self._openai_compatible_checks())
        return DiagnosticReport(tuple(checks), time.time())

    def _mac_check(self) -> CheckResult:
        version = platform.mac_ver()[0] or "unknown"
        machine = platform.machine()
        version_part = version.split(".")[0]
        major = int(version_part) if version_part.isdigit() else 0
        ok = platform.system() == "Darwin" and machine == "arm64" and major >= 14
        return CheckResult("macOS", ok, f"{version}, {machine}", "需要 macOS 14+ 与 Apple Silicon")

    def _qmd_check(self) -> CheckResult:
        qmd = str(self._section("qmd").get("command", "qmd"))
        index = str(self._section("qmd").get("index", "index"))
        path = resolve_command(qmd)
        if not path:
            return CheckResult("QMD", False, "未找到 qmd 命令", "安装 Node.js 22+，然后执行 npm install -g @tobilu/qmd")
        try:
            result = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10, env=self.qmd_environment())
        except (OSError, subprocess.TimeoutExpired) as exc:
            return CheckResult("QMD", False, str(exc), "检查 qmd 安装和 PATH")
        detail = (result.stdout or result.stderr).strip()
        if result.returncode:
            return CheckResult("QMD", False, detail or f"退出码 {result.returncode}", "重新安装 @tobilu/qmd")
        collection = str(self._section("knowledge").get("collection", ""))
        environment = self.qmd_environment()
        try:
            listed = subprocess.run([path, "--index", index, "ls", collection], capture_output=True, text=True, timeout=20, env=environment)
        except subprocess.TimeoutExpired:
            return CheckResult("QMD", False, f"{detail}; collection 检查超时", f"运行 qmd ls {collection}")
        if listed.returncode:
            reason = listed.stderr.strip().splitlines()[-1] if listed.stderr.strip() else "collection 不可用"
            return CheckResult("QMD", False, f"{detail}; {collection}: {reason}", f"创建或修复 collection: {collection}")
        try:
            query = subprocess.run(
                [path, "--index", index, "status"],
                capture_output=True,
                text=True,
                timeout=20,
                env=environment,
            )
        except subprocess.TimeoutExpired:
            return CheckResult("QMD", False, f"{detail}; 状态检查超时", "检查 QMD 索引和模型配置")
        if query.returncode:
            reason = query.stderr.strip().splitlines()[-1] if query.stderr.strip() else "状态检查失败"
            return CheckResult("QMD", False, f"{detail}; {reason}", "检查 QMD 三类模型配置并重新初始化")
        return CheckResult("QMD", True, f"{detail}; collection={collection}; 状态检查通过")

    def _node_check(self) -> CheckResult:
        node = resolve_command("node")
        if not node:
            return CheckResult("Node.js", False, "未找到 node", "安装 Node.js 22+")
        try:
            result = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return CheckResult("Node.js", False, str(exc), "安装 Node.js 22+")
        version = (result.stdout or result.stderr).strip()
        match = re.search(r"(\d+)", version)
        ok = result.returncode == 0 and bool(match) and int(match.group(1)) >= 22
        return CheckResult("Node.js", ok, version or "无法读取版本", "QMD 需要 Node.js 22+")

    def _ollama_service_check(self, base_urls: set[str]) -> CheckResult:
        if not resolve_command("ollama"):
            return CheckResult("Ollama", False, "未找到 ollama 命令", "从 https://ollama.com/download/mac 安装并打开 Ollama")
        for base_url in base_urls:
            try:
                response = requests.get(f"{base_url.rstrip('/')}/api/tags", timeout=3, allow_redirects=False)
                if response.is_redirect or 300 <= response.status_code < 400:
                    raise requests.RequestException("Ollama 端点返回重定向")
                response.raise_for_status()
            except requests.RequestException as exc:
                return CheckResult("Ollama", False, f"服务不可用：{exc}", "打开 Ollama.app 后重试")
        return CheckResult("Ollama", True, "本地服务可访问")

    def _ollama_models_check(self, base_url: str, required: set[str]) -> CheckResult:
        try:
            response = requests.get(f"{base_url.rstrip('/')}/api/tags", timeout=3, allow_redirects=False)
            if response.is_redirect or 300 <= response.status_code < 400:
                raise requests.RequestException("Ollama 端点返回重定向")
            response.raise_for_status()
            installed = {str(model.get("name")) for model in response.json().get("models", [])}
        except (requests.RequestException, ValueError) as exc:
            return CheckResult(f"Ollama 模型 ({base_url})", False, f"无法读取模型列表：{exc}", "检查对应 Ollama 服务")
        missing = sorted(model for model in required if model not in installed and _without_latest(model) not in installed)
        if missing:
            return CheckResult(f"Ollama 模型 ({base_url})", False, "缺少 " + ", ".join(missing), "在 cohelper 环境设置中确认下载")
        return CheckResult(f"Ollama 模型 ({base_url})", True, "已安装 " + ", ".join(sorted(required)))

    def required_ollama_models(self) -> set[str]:
        return {model for models in self.ollama_models_by_endpoint().values() for model in models}

    def ollama_models_by_endpoint(self) -> dict[str, set[str]]:
        required: dict[str, set[str]] = {}
        features = self._section("features")
        if features.get("translation") and self._section("translation").get("provider") == "ollama":
            section = self._section("translation")
            required.setdefault(str(section.get("base_url", "http://127.0.0.1:11434")), set()).add(str(section["model"]))
        knowledge_answer = features.get("knowledge_answer", features.get("knowledge_summary"))
        if knowledge_answer and self._section("summary").get("provider") == "ollama":
            section = self._section("summary")
            required.setdefault(str(section.get("base_url", "http://127.0.0.1:11434")), set()).add(str(section["model"]))
        if self._section("telegram").get("enabled"):
            section = self._section("vision")
            required.setdefault(str(section.get("base_url", "http://127.0.0.1:11434")), set()).add(str(section["model"]))
        return required

    def _openai_compatible_checks(self) -> list[CheckResult]:
        checks = []
        features = self._section("features")
        knowledge_answer = features.get("knowledge_answer", features.get("knowledge_summary"))
        enabled = {"translation": features.get("translation"), "summary": knowledge_answer}
        privacy = self._section("privacy")
        for kind, is_enabled in enabled.items():
            section = self._section(kind)
            if not is_enabled or section.get("provider") != "openai-compatible":
                continue
            base_url = str(section.get("base_url", ""))
            host = urlparse(base_url).hostname
            external = host not in {"127.0.0.1", "localhost", "::1"}
            label = f"{kind} API"
            if external and not privacy.get("allow_external_api"):
                checks.append(CheckResult(label, False, "外部 API 未获隐私授权", "将 privacy.allow_external_api 设为 true"))
                continue
            account = str(section.get("credential_account", kind))
            try:
                api_key = KeychainStore().get(account)
                headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
                response = requests.get(f"{base_url.rstrip('/')}/models", headers=headers, timeout=5, allow_redirects=False)
                if response.is_redirect or 300 <= response.status_code < 400:
                    raise requests.RequestException("API 端点返回重定向")
                response.raise_for_status()
                checks.append(CheckResult(label, True, f"{host} 可访问"))
            except (KeychainError, requests.RequestException) as exc:
                checks.append(CheckResult(label, False, str(exc), "检查 API URL、Keychain 凭据和网络"))
        return checks

    def qmd_environment(self) -> dict[str, str]:
        env = os.environ.copy()
        current_path = env.get("PATH", "")
        standard_path = os.pathsep.join(str(path) for path in STANDARD_COMMAND_DIRS)
        env["PATH"] = standard_path + (os.pathsep + current_path if current_path else "")
        models = self._section("qmd").get("models", {})
        if isinstance(models, Mapping):
            mapping = {"embedding": "QMD_EMBED_MODEL", "reranking": "QMD_RERANK_MODEL", "generation": "QMD_GENERATE_MODEL"}
            for key, variable in mapping.items():
                value = models.get(key)
                if value:
                    env[variable] = str(value)
        return env

    def _section(self, name: str) -> Mapping[str, object]:
        value = self.config.get(name, {})
        return value if isinstance(value, Mapping) else {}


class SetupInstaller:
    """Execute only fixed setup actions after UI confirmation."""

    def __init__(self, config: Mapping[str, object], state_path: Path = SETUP_STATE_PATH):
        self.config = config
        self.state_path = state_path
        self._cancel = threading.Event()
        self._process_lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None

    def cancel(self) -> None:
        self._cancel.set()
        with self._process_lock:
            process = self._process
        if process and process.poll() is None:
            self._terminate_process_group(process)

    def pull_ollama_models(self, progress: Callable[[str], None] = lambda _: None) -> None:
        ollama = resolve_command("ollama")
        if not ollama:
            raise RuntimeError("未找到 Ollama CLI")
        for base_url, models in sorted(EnvironmentDoctor(self.config).ollama_models_by_endpoint().items()):
            environment = os.environ.copy()
            environment["OLLAMA_HOST"] = base_url
            for model in sorted(models):
                progress(f"正在从 {base_url} 下载 {model}…")
                self._run([ollama, "pull", model], timeout=None, env=environment)

    def install_qmd(self, progress: Callable[[str], None] = lambda _: None) -> None:
        npm = resolve_command("npm")
        if not npm:
            raise RuntimeError("未找到 npm；请先安装 Node.js 22+")
        progress("正在安装 QMD…")
        self._run([npm, "install", "-g", "@tobilu/qmd"], timeout=None)

    def initialize_qmd_models(self, progress: Callable[[str], None] = lambda _: None) -> None:
        qmd_config = self.config.get("qmd", {})
        knowledge = self.config.get("knowledge", {})
        collection = str(knowledge.get("collection", "")) if isinstance(knowledge, Mapping) else ""
        source_path = str(knowledge.get("source_path", "")) if isinstance(knowledge, Mapping) else ""
        qmd = self._qmd_command()
        env = EnvironmentDoctor(self.config).qmd_environment()
        listed = self._run([*qmd, "ls", collection], timeout=30, env=env, check=False)
        created = False
        if listed.returncode:
            if not source_path:
                raise RuntimeError(f"QMD collection {collection!r} 不存在，且未配置 knowledge.source_path")
            source = Path(source_path).expanduser()
            if not source.is_dir():
                raise RuntimeError(f"知识库目录不存在：{source}")
            progress(f"正在创建 QMD collection {collection}…")
            self._run([*qmd, "collection", "add", str(source), "--name", collection], timeout=None, env=env)
            self._run([*qmd, "update"], timeout=None, env=env)
            progress(f"正在为 {collection} 生成向量…")
            self._run([*qmd, "embed", "-c", collection], timeout=None, env=env)
            created = True
        models = qmd_config.get("models", {}) if isinstance(qmd_config, Mapping) else {}
        desired_embedding = str(models.get("embedding", "")) if isinstance(models, Mapping) else ""
        state = SetupState.load(self.state_path)
        if not created and desired_embedding != state.qmd_embedding_model:
            progress(f"QMD embedding 模型已变更，正在重建 {collection} 向量…")
            self._run([*qmd, "embed", "-f", "-c", collection], timeout=None, env=env)
        progress("正在初始化 QMD 的检索、嵌入和重排模型…")
        self._run(
            [*qmd, "query", "cohelper setup verification", "-c", collection, "-n", "1", "--format", "json"],
            timeout=None,
            env=env,
        )
        state.qmd_embedding_model = desired_embedding
        state.save(self.state_path)

    def _qmd_command(self) -> list[str]:
        qmd_config = self.config.get("qmd", {})
        qmd_name = str(qmd_config.get("command", "qmd")) if isinstance(qmd_config, Mapping) else "qmd"
        index = str(qmd_config.get("index", "index")) if isinstance(qmd_config, Mapping) else "index"
        qmd = resolve_command(qmd_name)
        if not qmd:
            raise RuntimeError("未找到 QMD CLI")
        return [qmd, "--index", index]

    def _run(
        self,
        command: list[str],
        timeout: int | None,
        env: Mapping[str, str] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        if self._cancel.is_set():
            raise RuntimeError("环境设置已取消")
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            start_new_session=True,
        )
        with self._process_lock:
            self._process = process
        deadline = time.monotonic() + timeout if timeout is not None else None
        try:
            while True:
                if self._cancel.is_set():
                    self._terminate_process_group(process)
                    process.communicate()
                    raise RuntimeError("环境设置已取消")
                if deadline is not None and time.monotonic() >= deadline:
                    self._terminate_process_group(process)
                    process.communicate()
                    raise RuntimeError(f"命令超时：{command[0]}")
                try:
                    stdout, stderr = process.communicate(timeout=0.2)
                    if self._cancel.is_set():
                        raise RuntimeError("环境设置已取消")
                    break
                except subprocess.TimeoutExpired:
                    continue
        finally:
            with self._process_lock:
                if self._process is process:
                    self._process = None
        result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        if check and result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"命令失败：{command[0]}")
        return result

    @staticmethod
    def _terminate_process_group(process: subprocess.Popen[str]) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=3)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


def _without_latest(model: str) -> str:
    return re.sub(r":latest$", "", model)

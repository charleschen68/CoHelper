import subprocess
import threading
import time

import pytest

from cohelper_setup import CheckResult, DiagnosticReport, EnvironmentDoctor, KeychainStore, SetupInstaller, SetupState, resolve_command


def test_report_ready_requires_every_check():
    ready = DiagnosticReport((CheckResult("a", True, "ok"),), 1.0)
    blocked = DiagnosticReport((CheckResult("a", True, "ok"), CheckResult("b", False, "bad")), 1.0)
    assert ready.ready
    assert not blocked.ready
    assert "✗ b" in blocked.as_text()


def test_qmd_model_environment_uses_configured_values(monkeypatch):
    doctor = EnvironmentDoctor({"qmd": {"models": {"embedding": "embed.gguf", "reranking": "rank.gguf", "generation": "gen.gguf"}}})
    env = doctor.qmd_environment()
    assert env["QMD_EMBED_MODEL"] == "embed.gguf"
    assert env["QMD_RERANK_MODEL"] == "rank.gguf"
    assert env["QMD_GENERATE_MODEL"] == "gen.gguf"
    assert env["PATH"].startswith("/opt/homebrew/bin:/usr/local/bin")


def test_resolve_command_falls_back_to_standard_mac_paths(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.setattr("pathlib.Path.is_file", lambda path: str(path) == "/opt/homebrew/bin/qmd")
    monkeypatch.setattr("os.access", lambda path, mode: str(path) == "/opt/homebrew/bin/qmd")
    assert resolve_command("qmd") == "/opt/homebrew/bin/qmd"


def test_required_models_respect_disabled_features():
    config = {
        "features": {"translation": False, "knowledge_summary": True},
        "translation": {"provider": "ollama", "model": "translation-model"},
        "summary": {"provider": "ollama", "model": "summary-model"},
    }
    assert EnvironmentDoctor(config).required_ollama_models() == {"summary-model"}


def test_region_translation_model_diagnostics_are_feature_gated():
    config = {
        "features": {"region_translation": True},
        "vision": {"model": "qwen2.5vl:7b", "base_url": "http://127.0.0.1:11434"},
    }

    assert EnvironmentDoctor(config).required_ollama_models() == {"qwen2.5vl:7b"}


def test_disabled_knowledge_search_does_not_check_qmd(monkeypatch):
    doctor = EnvironmentDoctor(
        {
            "features": {"knowledge_search": False, "translation": False, "knowledge_summary": False},
        }
    )
    monkeypatch.setattr(doctor, "_mac_check", lambda: CheckResult("macOS", True, "ok"))
    monkeypatch.setattr(doctor, "_qmd_check", lambda: pytest.fail("QMD should not run"))
    assert doctor.run().ready


def test_node_22_is_required(monkeypatch):
    doctor = EnvironmentDoctor({})
    monkeypatch.setattr("cohelper_setup.resolve_command", lambda _: "/usr/local/bin/node")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "v21.9.0\n", ""),
    )
    assert not doctor._node_check().ok


def test_qmd_collection_check_uses_configured_index(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        output = "qmd 2.5.3" if "--version" in command else "qmd://notes/a.md"
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr("cohelper_setup.resolve_command", lambda _: "/opt/homebrew/bin/qmd")
    monkeypatch.setattr(subprocess, "run", fake_run)
    result = EnvironmentDoctor({"qmd": {"index": "custom"}, "knowledge": {"collection": "notes"}})._qmd_check()
    assert result.ok
    assert calls[1][:4] == ["/opt/homebrew/bin/qmd", "--index", "custom", "ls"]


def test_missing_qmd_collection_is_created_from_configured_source(monkeypatch, tmp_path):
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        returncode = 1 if "ls" in command else 0
        return subprocess.CompletedProcess(command, returncode, "", "missing" if returncode else "")

    monkeypatch.setattr("cohelper_setup.resolve_command", lambda _: "/opt/homebrew/bin/qmd")
    monkeypatch.setattr(SetupInstaller, "_run", lambda self, command, **kwargs: fake_run(command, **kwargs))
    installer = SetupInstaller(
        {
            "qmd": {"command": "qmd", "index": "custom"},
            "knowledge": {"collection": "notes", "source_path": str(tmp_path)},
        },
        state_path=tmp_path / "state.json",
    )
    installer.initialize_qmd_models()
    assert ["collection", "add", str(tmp_path), "--name", "notes"] in [command[3:] for command in commands]
    assert any("embed" in command for command in commands)


def test_missing_qmd_collection_without_source_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr("cohelper_setup.resolve_command", lambda _: "/opt/homebrew/bin/qmd")
    monkeypatch.setattr(
        SetupInstaller,
        "_run",
        lambda self, command, **kwargs: subprocess.CompletedProcess(command, 1, "", "missing"),
    )
    installer = SetupInstaller({"qmd": {}, "knowledge": {"collection": "notes", "source_path": ""}}, state_path=tmp_path / "state.json")
    with pytest.raises(RuntimeError, match="source_path"):
        installer.initialize_qmd_models()


def test_custom_embedding_rebuilds_existing_collection(monkeypatch, tmp_path):
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr("cohelper_setup.resolve_command", lambda _: "/opt/homebrew/bin/qmd")
    monkeypatch.setattr(SetupInstaller, "_run", lambda self, command, **kwargs: fake_run(command, **kwargs))
    installer = SetupInstaller(
        {
            "qmd": {"models": {"embedding": "hf:custom/embed.gguf"}},
            "knowledge": {"collection": "notes"},
        },
        state_path=tmp_path / "state.json",
    )
    installer.initialize_qmd_models()
    assert any(command[-4:] == ["embed", "-f", "-c", "notes"] for command in commands)


def test_installer_uses_argument_list_without_shell(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(SetupInstaller, "_run", lambda self, command, **kwargs: fake_run(command, **kwargs))
    installer = SetupInstaller(
        {
            "features": {"translation": True, "knowledge_summary": False},
            "translation": {"provider": "ollama", "model": "safe:model"},
        }
    )
    installer.pull_ollama_models()
    assert calls[0][0][0].endswith("/ollama")
    assert calls[0][0][1:] == ["pull", "safe:model"]
    assert "shell" not in calls[0][1]
    assert calls[0][1]["env"]["OLLAMA_HOST"] == "http://127.0.0.1:11434"


def test_models_are_grouped_by_ollama_endpoint():
    config = {
        "features": {"translation": True, "knowledge_summary": True},
        "translation": {"provider": "ollama", "model": "translate", "base_url": "http://localhost:11434"},
        "summary": {"provider": "ollama", "model": "summary", "base_url": "http://server:11434"},
    }
    assert EnvironmentDoctor(config).ollama_models_by_endpoint() == {
        "http://localhost:11434": {"translate"},
        "http://server:11434": {"summary"},
    }


def test_cancelled_installer_does_not_start_process():
    installer = SetupInstaller({})
    installer.cancel()
    with pytest.raises(RuntimeError, match="已取消"):
        installer._run(["/usr/bin/true"], timeout=1)


def test_running_installer_process_can_be_cancelled():
    installer = SetupInstaller({})
    errors = []

    def run():
        try:
            installer._run(["/bin/sleep", "10"], timeout=None)
        except RuntimeError as exc:
            errors.append(str(exc))

    thread = threading.Thread(target=run)
    thread.start()
    for _ in range(100):
        if installer._process is not None:
            break
        time.sleep(0.01)
    installer.cancel()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert errors == ["环境设置已取消"]


def test_setup_state_round_trip(tmp_path):
    path = tmp_path / "state.json"
    SetupState(True, "embed-v2").save(path)
    assert SetupState.load(path) == SetupState(True, "embed-v2")


def test_unchanged_embedding_model_does_not_rebuild(monkeypatch, tmp_path):
    commands = []

    def fake_run(self, command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "ok", "")

    state_path = tmp_path / "state.json"
    SetupState(False, "hf:custom/embed.gguf").save(state_path)
    monkeypatch.setattr("cohelper_setup.resolve_command", lambda _: "/opt/homebrew/bin/qmd")
    monkeypatch.setattr(SetupInstaller, "_run", fake_run)
    installer = SetupInstaller(
        {
            "qmd": {"models": {"embedding": "hf:custom/embed.gguf"}},
            "knowledge": {"collection": "notes"},
        },
        state_path=state_path,
    )
    installer.initialize_qmd_models()
    assert not any(command[-4:] == ["embed", "-f", "-c", "notes"] for command in commands)


def test_keychain_secret_is_sent_via_stdin(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured.update(command=command, kwargs=kwargs)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    KeychainStore().set("summary", "super-secret")
    assert "super-secret" not in captured["command"]
    assert captured["kwargs"]["input"] == "super-secret\n"


def test_empty_keychain_secret_is_rejected():
    with pytest.raises(ValueError):
        KeychainStore().set("summary", "")

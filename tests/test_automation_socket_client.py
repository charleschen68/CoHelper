import json
from pathlib import Path

from ai_drive.automation.socket import AutomationSocketClient


def test_client_sends_only_json_operation_over_injected_transport(tmp_path: Path):
    sent = []
    client = AutomationSocketClient(tmp_path / "control.sock", request=lambda payload: sent.append(payload) or '{"ok":true,"status":"accept: armed"}')

    assert client.status() == "accept: armed"
    client.arm("accept")

    assert [json.loads(item)["op"] for item in sent] == ["status", "arm"]

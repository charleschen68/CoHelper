import json
from pathlib import Path

from ai_drive.automation.socket import AutomationSocketProtocol


class Runtime:
    def __init__(self): self.calls = []
    def arm(self, group): self.calls.append(("arm", group))
    def disarm(self, group): self.calls.append(("disarm", group))
    def status(self): return "accept: disarmed"


def test_socket_protocol_only_exposes_explicit_local_control_operations():
    runtime = Runtime()
    protocol = AutomationSocketProtocol(runtime)

    response = json.loads(protocol.handle(json.dumps({"op": "arm", "group": "accept"})))

    assert response == {"ok": True, "status": "accept: disarmed"}
    assert runtime.calls == [("arm", "accept")]
    assert json.loads(protocol.handle('{"op":"shell"}'))["ok"] is False

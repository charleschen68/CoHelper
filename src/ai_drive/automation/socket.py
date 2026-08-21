"""Current-user Unix-socket control surface for the local automation process."""

from __future__ import annotations

import json
import os
import socket
import stat
import threading
from pathlib import Path
from typing import Protocol


class RuntimeControl(Protocol):
    def arm(self, group: str) -> None: ...
    def disarm(self, group: str) -> None: ...
    def status(self) -> str: ...
    def emergency_stop(self) -> None: ...
    def resume(self) -> None: ...


class AutomationSocketProtocol:
    def __init__(self, runtime: RuntimeControl):
        self._runtime = runtime

    def handle(self, request: str) -> str:
        try:
            payload = json.loads(request)
            if not isinstance(payload, dict):
                raise ValueError("request must be an object")
            op = payload.get("op")
            if op == "status":
                return self._success()
            if op == "emergency_stop":
                self._runtime.emergency_stop()
                return self._success()
            if op == "resume":
                self._runtime.resume()
                return self._success()
            group = payload.get("group")
            if op not in {"arm", "disarm"} or not isinstance(group, str) or not group:
                raise ValueError("unsupported operation")
            if op == "arm":
                self._runtime.arm(group)
            else:
                self._runtime.disarm(group)
            return self._success()
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)

    def _success(self) -> str:
        return json.dumps({"ok": True, "status": self._runtime.status()}, ensure_ascii=False)


class AutomationUnixSocketServer:
    """Small local-only server; callers need filesystem permission to its socket."""

    def __init__(self, path: Path, protocol: AutomationSocketProtocol):
        self._path = path
        self._protocol = protocol
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stopped = threading.Event()

    def start(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self._path.parent, stat.S_IRWXU)
        if self._path.exists():
            if not stat.S_ISSOCK(self._path.stat().st_mode):
                raise RuntimeError(f"refusing to replace non-socket path: {self._path}")
            self._path.unlink()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self._path))
        os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)
        listener.listen(4)
        listener.settimeout(.25)
        self._listener = listener
        self._stopped.clear()
        self._thread = threading.Thread(target=self._serve, name="cohelper-automation-socket", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        if self._listener is not None:
            self._listener.close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._path.exists() and stat.S_ISSOCK(self._path.stat().st_mode):
            self._path.unlink()

    def _serve(self) -> None:
        assert self._listener is not None
        while not self._stopped.is_set():
            try:
                connection, _ = self._listener.accept()
            except (OSError, TimeoutError):
                continue
            with connection:
                request = connection.recv(16_384).decode("utf-8")
                connection.sendall((self._protocol.handle(request) + "\n").encode("utf-8"))


class AutomationSocketClient:
    """Narrow client used by local CLI and external control transports."""

    def __init__(self, path: Path, request=None):
        self._path = path
        self._request = request or self._send

    def status(self) -> str:
        return self._call({"op": "status"})

    def arm(self, group: str) -> None:
        self._call({"op": "arm", "group": group})

    def disarm(self, group: str) -> None:
        self._call({"op": "disarm", "group": group})

    def emergency_stop(self) -> None:
        self._call({"op": "emergency_stop"})

    def resume(self) -> None:
        self._call({"op": "resume"})

    def _call(self, payload: dict[str, str]) -> str:
        response = json.loads(self._request(json.dumps(payload, ensure_ascii=False)))
        if not response.get("ok"):
            raise RuntimeError(str(response.get("error", "automation control failed")))
        return str(response["status"])

    def _send(self, payload: str) -> str:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.connect(str(self._path))
            connection.sendall(payload.encode("utf-8"))
            return connection.recv(16_384).decode("utf-8")

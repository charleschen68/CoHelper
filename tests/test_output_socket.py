import json
import socket
import stat
import tempfile
import threading
import time
from pathlib import Path

import pytest

from ai_drive.output import (
    OutputEvent,
    OutputEventSocketClient,
    OutputEventSocketProtocol,
    OutputEventUnixSocketServer,
    OutputKind,
    OutputSeverity,
    OutputSource,
)


def test_output_socket_protocol_accepts_only_versioned_output_events():
    published = []
    protocol = OutputEventSocketProtocol(published.append)
    event = OutputEvent(
        event_id="detection-1",
        kind=OutputKind.DETECTION,
        source=OutputSource.AUTOMATION,
        occurred_at=123.0,
        title="目标检测",
        message="检测到接受按钮",
        severity=OutputSeverity.INFO,
        generation=None,
        metadata={"rule_id": "accept"},
    )

    accepted = json.loads(protocol.handle(event.to_json()))
    rejected = json.loads(protocol.handle('{"op":"click","x":10,"y":20}'))

    assert accepted == {"ok": True, "event_id": "detection-1"}
    assert rejected["ok"] is False
    assert published == [event]


def test_output_client_rejects_acknowledgement_for_another_event():
    client = OutputEventSocketClient(Path("/not-used"))

    with pytest.raises(RuntimeError, match="does not match"):
        client._validate_ack('{"ok":true,"event_id":"another-event"}', "expected-event")


def test_output_event_crosses_a_current_user_only_unix_socket():
    received = []
    delivered = threading.Event()

    def publish(event):
        received.append(event)
        delivered.set()

    with tempfile.TemporaryDirectory(prefix="cohelper-", dir="/private/tmp") as directory:
        path = Path(directory) / "private" / "events.sock"
        server = OutputEventUnixSocketServer(path, OutputEventSocketProtocol(publish))
        event = OutputEvent(
            event_id="status-1",
            kind=OutputKind.STATUS,
            source=OutputSource.SYSTEM,
            occurred_at=456.0,
            title="状态",
            message="已连接",
            severity=OutputSeverity.INFO,
            generation=None,
            metadata={},
        )

        server.start()
        try:
            assert OutputEventSocketClient(path).publish(event) == "status-1"
            assert delivered.wait(1)
            assert received == [event]
            assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
        finally:
            server.stop()

        assert not path.exists()


def test_slow_client_cannot_block_other_output_publishers():
    delivered = threading.Event()
    with tempfile.TemporaryDirectory(prefix="cohelper-", dir="/private/tmp") as directory:
        path = Path(directory) / "private" / "events.sock"
        server = OutputEventUnixSocketServer(
            path,
            OutputEventSocketProtocol(lambda _event: delivered.set()),
        )
        event = OutputEvent(
            event_id="status-after-slow-client",
            kind=OutputKind.STATUS,
            source=OutputSource.SYSTEM,
            occurred_at=456.0,
            title="状态",
            message="通道仍可用",
            severity=OutputSeverity.INFO,
            generation=None,
            metadata={},
        )
        server.start()
        slow = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            slow.connect(str(path))
            slow.sendall(b"{")
            assert OutputEventSocketClient(path, timeout_seconds=0.5).publish(event) == event.event_id
            assert delivered.wait(0.5)
        finally:
            slow.close()
            server.stop()


def test_stop_interrupts_incomplete_clients_without_waiting_for_read_timeout():
    with tempfile.TemporaryDirectory(prefix="cohelper-", dir="/private/tmp") as directory:
        path = Path(directory) / "private" / "events.sock"
        server = OutputEventUnixSocketServer(path, OutputEventSocketProtocol(lambda _event: None))
        server.start()
        slow = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        slow.connect(str(path))
        slow.sendall(b"{")
        time.sleep(0.05)

        started = time.monotonic()
        server.stop()

        slow.close()
        assert time.monotonic() - started < 0.5
        assert not path.exists()


def test_client_timeout_is_a_total_deadline_for_trickled_acknowledgements():
    with tempfile.TemporaryDirectory(prefix="cohelper-", dir="/private/tmp") as directory:
        path = Path(directory) / "trickle.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        listener.listen(1)
        stopped = threading.Event()

        def trickle_ack():
            connection, _ = listener.accept()
            with connection:
                while connection.recv(16_384):
                    pass
                for byte in b'{"ok":true,"event_id":"status-1"}\n':
                    if stopped.wait(0.05):
                        return
                    try:
                        connection.sendall(bytes([byte]))
                    except OSError:
                        return

        worker = threading.Thread(target=trickle_ack, daemon=True)
        worker.start()
        event = OutputEvent(
            event_id="status-1",
            kind=OutputKind.STATUS,
            source=OutputSource.SYSTEM,
            occurred_at=456.0,
            title="状态",
            message="测试总超时",
            severity=OutputSeverity.INFO,
            generation=None,
            metadata={},
        )
        started = time.monotonic()
        try:
            with pytest.raises(TimeoutError):
                OutputEventSocketClient(path, timeout_seconds=0.12).publish(event)
        finally:
            stopped.set()
            listener.close()
            worker.join(1)

        assert time.monotonic() - started < 0.5

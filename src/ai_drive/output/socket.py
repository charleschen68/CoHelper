"""Current-user Unix-socket transport for output-only events."""

from __future__ import annotations

import json
import logging
import math
import os
import socket as socketlib
import stat
import threading
import time
from pathlib import Path
from typing import Callable

from .events import MAX_WIRE_BYTES, OutputEvent, OutputEventError


LOGGER = logging.getLogger(__name__)
MAX_CONCURRENT_CLIENTS = 8
CLIENT_READ_TIMEOUT_SECONDS = 1.0


class OutputEventSocketProtocol:
    def __init__(self, publish: Callable[[OutputEvent], None]):
        self._publish = publish

    def handle(self, request: str) -> str:
        try:
            event = OutputEvent.from_json(request)
            self._publish(event)
            response = {"ok": True, "event_id": event.event_id}
        except OutputEventError as exc:
            response = {"ok": False, "error": str(exc)}
        except Exception as exc:  # output consumers cannot terminate the transport thread
            response = {"ok": False, "error": f"output consumer failed: {type(exc).__name__}"}
        return json.dumps(response, ensure_ascii=False, separators=(",", ":"))


class OutputEventUnixSocketServer:
    def __init__(self, path: Path, protocol: OutputEventSocketProtocol):
        self._path = path
        self._protocol = protocol
        self._listener: socketlib.socket | None = None
        self._thread: threading.Thread | None = None
        self._stopped = threading.Event()
        self._bound_identity: tuple[int, int] | None = None
        self._state_lock = threading.Lock()
        self._connections: set[socketlib.socket] = set()
        self._workers: set[threading.Thread] = set()
        self._client_slots = threading.BoundedSemaphore(MAX_CONCURRENT_CLIENTS)

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("output socket server is already started")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self._path.parent, stat.S_IRWXU)
        self._remove_stale_socket()
        listener = socketlib.socket(socketlib.AF_UNIX, socketlib.SOCK_STREAM)
        bound = False
        try:
            listener.bind(str(self._path))
            bound = True
            socket_stat = self._path.stat()
            self._bound_identity = (socket_stat.st_dev, socket_stat.st_ino)
            os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)
            listener.listen(MAX_CONCURRENT_CLIENTS)
            listener.settimeout(0.25)
        except Exception:
            listener.close()
            if bound:
                self._unlink_bound_socket()
                self._bound_identity = None
            raise
        self._listener = listener
        self._stopped.clear()
        self._thread = threading.Thread(target=self._serve, name="cohelper-output-socket", daemon=True)
        try:
            self._thread.start()
        except Exception:
            listener.close()
            self._listener = None
            self._thread = None
            self._unlink_bound_socket()
            self._bound_identity = None
            raise

    def stop(self) -> None:
        self._stopped.set()
        listener, self._listener = self._listener, None
        accept_thread, self._thread = self._thread, None
        if listener is not None:
            listener.close()
        with self._state_lock:
            connections = tuple(self._connections)
        for connection in connections:
            try:
                connection.shutdown(socketlib.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        if accept_thread is not None:
            accept_thread.join(timeout=1)
            if accept_thread.is_alive():
                LOGGER.error("output socket accept thread did not stop")
        deadline = time.monotonic() + 1
        with self._state_lock:
            workers = tuple(self._workers)
        for worker in workers:
            worker.join(timeout=max(0, deadline - time.monotonic()))
        survivors = [worker.name for worker in workers if worker.is_alive()]
        if survivors:
            LOGGER.error("output socket workers did not stop: %s", ", ".join(survivors))
        self._unlink_bound_socket()
        self._bound_identity = None

    def _unlink_bound_socket(self) -> None:
        try:
            socket_stat = self._path.stat()
            identity = (socket_stat.st_dev, socket_stat.st_ino)
            if stat.S_ISSOCK(socket_stat.st_mode) and identity == self._bound_identity:
                self._path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            LOGGER.error("failed to remove output socket: %s", type(exc).__name__)

    def _remove_stale_socket(self) -> None:
        if not self._path.exists():
            return
        if not stat.S_ISSOCK(self._path.stat().st_mode):
            raise RuntimeError(f"refusing to replace non-socket path: {self._path}")
        probe = socketlib.socket(socketlib.AF_UNIX, socketlib.SOCK_STREAM)
        probe.settimeout(0.25)
        try:
            probe.connect(str(self._path))
        except (ConnectionRefusedError, FileNotFoundError):
            self._path.unlink(missing_ok=True)
        except OSError as exc:
            raise RuntimeError(f"cannot verify existing output socket: {exc}") from exc
        else:
            raise RuntimeError(f"output socket is already active: {self._path}")
        finally:
            probe.close()

    def _serve(self) -> None:
        listener = self._listener
        assert listener is not None
        while not self._stopped.is_set():
            try:
                connection, _ = listener.accept()
            except (OSError, TimeoutError):
                continue
            if self._stopped.is_set():
                connection.close()
                break
            if not self._client_slots.acquire(blocking=False):
                self._reject_busy(connection)
                continue
            worker = threading.Thread(
                target=self._serve_connection,
                args=(connection,),
                name="cohelper-output-client",
                daemon=True,
            )
            with self._state_lock:
                if self._stopped.is_set():
                    self._client_slots.release()
                    connection.close()
                    break
                self._connections.add(connection)
                self._workers.add(worker)
            try:
                worker.start()
            except Exception:
                with self._state_lock:
                    self._connections.discard(connection)
                    self._workers.discard(worker)
                self._client_slots.release()
                connection.close()
                raise

    def _serve_connection(self, connection: socketlib.socket) -> None:
        try:
            response = self._read_and_handle(connection)
            try:
                connection.sendall((response + "\n").encode("utf-8"))
            except OSError:
                pass
        finally:
            connection.close()
            with self._state_lock:
                self._connections.discard(connection)
                self._workers.discard(threading.current_thread())
            self._client_slots.release()

    @staticmethod
    def _reject_busy(connection: socketlib.socket) -> None:
        try:
            connection.settimeout(0.1)
            response = json.dumps(
                {"ok": False, "error": "output socket is busy"},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            connection.sendall((response + "\n").encode("utf-8"))
        except OSError:
            pass
        finally:
            connection.close()

    def _read_and_handle(self, connection: socketlib.socket) -> str:
        request = bytearray()
        deadline = time.monotonic() + CLIENT_READ_TIMEOUT_SECONDS
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise OutputEventError("timed out while reading output event")
                connection.settimeout(min(0.25, remaining))
                try:
                    chunk = connection.recv(min(16_384, MAX_WIRE_BYTES + 1 - len(request)))
                except TimeoutError:
                    if self._stopped.is_set():
                        raise OutputEventError("output socket is stopping")
                    continue
                if not chunk:
                    break
                request.extend(chunk)
                if len(request) > MAX_WIRE_BYTES:
                    raise OutputEventError(f"event exceeds {MAX_WIRE_BYTES} wire bytes")
            return self._protocol.handle(request.decode("utf-8"))
        except (OSError, UnicodeDecodeError, OutputEventError) as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, separators=(",", ":"))


class OutputEventSocketClient:
    def __init__(self, path: Path, *, timeout_seconds: float = 2):
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive finite number")
        self._path = path
        self._timeout_seconds = float(timeout_seconds)

    def publish(self, event: OutputEvent) -> str:
        payload = event.to_json().encode("utf-8")
        deadline = time.monotonic() + self._timeout_seconds

        def set_remaining_timeout(connection: socketlib.socket) -> None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out publishing output event")
            connection.settimeout(remaining)

        with socketlib.socket(socketlib.AF_UNIX, socketlib.SOCK_STREAM) as connection:
            set_remaining_timeout(connection)
            connection.connect(str(self._path))
            set_remaining_timeout(connection)
            connection.sendall(payload)
            connection.shutdown(socketlib.SHUT_WR)
            response_bytes = bytearray()
            while b"\n" not in response_bytes:
                if len(response_bytes) >= 16_384:
                    raise RuntimeError("output acknowledgement is too large")
                set_remaining_timeout(connection)
                chunk = connection.recv(16_384 - len(response_bytes))
                if not chunk:
                    break
                response_bytes.extend(chunk)
            try:
                response = bytes(response_bytes).decode("utf-8").strip()
            except UnicodeDecodeError as exc:
                raise RuntimeError("invalid output acknowledgement encoding") from exc
        return self._validate_ack(response, event.event_id)

    @staticmethod
    def _validate_ack(response: str, expected_event_id: str) -> str:
        try:
            body = json.loads(response)
            if not isinstance(body, dict):
                raise RuntimeError("invalid output acknowledgement")
            if not body.get("ok"):
                raise RuntimeError(str(body.get("error", "output publish failed")))
            event_id = body.get("event_id")
            if not isinstance(event_id, str) or not event_id:
                raise RuntimeError("output acknowledgement lacks event_id")
            if event_id != expected_event_id:
                raise RuntimeError("output acknowledgement event_id does not match request")
            return event_id
        except json.JSONDecodeError as exc:
            raise RuntimeError("invalid output acknowledgement") from exc

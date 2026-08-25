"""Priority-aware leases for serialized local-model requests."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable


class ModelQueueTimeout(TimeoutError):
    """The request waited too long for its local-model lease."""


class ModelQueueCancelled(RuntimeError):
    """The request was cancelled while waiting for its local-model lease."""


@dataclass
class _QueueState:
    condition: threading.Condition
    active: bool = False
    waiters: list[tuple[int, int, object]] = field(default_factory=list)


class ModelLease:
    def __init__(self, state: _QueueState):
        self._state = state
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        with self._state.condition:
            self._state.active = False
            self._state.condition.notify_all()

    def __enter__(self) -> "ModelLease":
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        self.release()


class ModelScheduler:
    """Serialize requests per endpoint/model while honoring priority.

    Lower priority values run first. Region translation uses priority zero;
    background clipboard work uses the default priority ten. A request that
    is already running is never preempted.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.RLock()
        self._states: dict[tuple[str, str], _QueueState] = {}
        self._sequence = 0

    def acquire(
        self,
        endpoint: str,
        model: str,
        *,
        priority: int = 10,
        timeout: float = 30.0,
        cancel: threading.Event | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> ModelLease:
        if timeout <= 0:
            raise ValueError("model queue timeout must be positive")
        key = (endpoint.rstrip("/"), model)
        with self._lock:
            state = self._states.get(key)
            if state is None:
                state = _QueueState(threading.Condition(self._lock))
                self._states[key] = state
            self._sequence += 1
            waiter = (priority, self._sequence, object())
            state.waiters.append(waiter)
            deadline = self._clock() + timeout
            while True:
                cancelled = (cancel is not None and cancel.is_set()) or (
                    cancel_check is not None and cancel_check()
                )
                if cancelled:
                    state.waiters.remove(waiter)
                    state.condition.notify_all()
                    raise ModelQueueCancelled("local model wait was cancelled")
                if not state.active and min(state.waiters) == waiter:
                    state.waiters.remove(waiter)
                    state.active = True
                    return ModelLease(state)
                remaining = deadline - self._clock()
                if remaining <= 0:
                    state.waiters.remove(waiter)
                    state.condition.notify_all()
                    raise ModelQueueTimeout("local model queue timed out")
                state.condition.wait(min(0.05, remaining))


DEFAULT_MODEL_SCHEDULER = ModelScheduler()


__all__ = [
    "DEFAULT_MODEL_SCHEDULER",
    "ModelLease",
    "ModelQueueCancelled",
    "ModelQueueTimeout",
    "ModelScheduler",
]

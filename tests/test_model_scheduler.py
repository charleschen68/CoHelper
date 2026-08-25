import threading
import time

import pytest

from ai_drive.model_scheduler import (
    ModelQueueCancelled,
    ModelQueueTimeout,
    ModelScheduler,
)


def test_region_priority_runs_before_older_background_waiter():
    scheduler = ModelScheduler()
    held = scheduler.acquire("http://127.0.0.1:11434", "translategemma:4b")
    order = []
    release = threading.Event()

    def run(name, priority):
        with scheduler.acquire(
            "http://127.0.0.1:11434",
            "translategemma:4b",
            priority=priority,
            timeout=1,
        ):
            order.append(name)
            release.wait(1)

    background = threading.Thread(target=run, args=("background", 10))
    region = threading.Thread(target=run, args=("region", 0))
    background.start()
    time.sleep(0.02)
    region.start()
    held.release()
    time.sleep(0.05)
    release.set()
    background.join(1)
    region.join(1)

    assert order == ["region", "background"]


def test_queue_timeout_and_cancellation_are_distinct():
    scheduler = ModelScheduler()
    held = scheduler.acquire("endpoint", "model")
    with pytest.raises(ModelQueueTimeout):
        scheduler.acquire("endpoint", "model", timeout=0.02)

    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ModelQueueCancelled):
        scheduler.acquire("endpoint", "model", cancel=cancel)
    held.release()


def test_same_endpoint_and_model_are_serialized_but_other_models_are_independent():
    scheduler = ModelScheduler()
    held = scheduler.acquire("endpoint", "model")
    other = scheduler.acquire("endpoint", "other-model", timeout=0.02)
    other.release()
    with pytest.raises(ModelQueueTimeout):
        scheduler.acquire("endpoint", "model", timeout=0.02)
    held.release()


def test_scheduler_reports_waiting_and_acquired_callbacks_outside_the_lease():
    scheduler = ModelScheduler()
    held = scheduler.acquire("endpoint", "model")
    events = []
    result = []

    def run():
        with scheduler.acquire(
            "endpoint",
            "model",
            timeout=1,
            on_waiting=lambda: events.append("waiting"),
            on_acquired=lambda: events.append("acquired"),
        ):
            result.append("ran")

    worker = threading.Thread(target=run)
    worker.start()
    time.sleep(0.05)
    held.release()
    worker.join(1)

    assert events == ["waiting", "acquired"]
    assert result == ["ran"]

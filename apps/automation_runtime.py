"""Manual entry point for the local CoHelper screen-automation service."""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from Quartz import CGEventCreate, CGEventGetLocation

from ai_drive.automation.actions import GuardedActionExecutor
from ai_drive.automation.config import AutomationConfig, DEFAULT_CONFIG_PATH, TemplateSpec
from ai_drive.automation.macos import QuartzFrameCapture
from ai_drive.automation.macos_output import QuartzAutomationOutput
from ai_drive.automation.notifications import NotificationQueue
from ai_drive.automation.matcher import OpenCVTemplateMatcher
from ai_drive.automation.runner import AutomationRunner
from ai_drive.automation.runtime import AutomationRuntime
from ai_drive.automation.socket import AutomationSocketProtocol, AutomationUnixSocketServer
from ai_drive.automation.sound import SystemAlarm
from ai_drive.automation.state import AutomationStateStore
from ai_drive.macos import QuartzPointerController
from ai_drive.vision import ScreenPoint
from cohelper_setup import KeychainStore


DEFAULT_STATE_PATH = Path.home() / "Library" / "Application Support" / "cohelper" / "automation" / "state.sqlite3"
DEFAULT_SOCKET_PATH = Path.home() / "Library" / "Application Support" / "cohelper" / "automation" / "control.sock"


def _pointer_is_in_emergency_corner() -> bool:
    event = CGEventCreate(None)
    if event is None:
        return False
    point = CGEventGetLocation(event)
    return float(point.x) <= 5 and float(point.y) <= 5


def run() -> None:
    parser = argparse.ArgumentParser(description="Run CoHelper local screen automation")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--arm", action="append", default=[])
    arguments = parser.parse_args()
    config = AutomationConfig.load(arguments.config)
    for group, reason in config.disabled_groups.items():
        logging.error("automation group %s is disabled by invalid configuration: %s", group, reason)
    state = AutomationStateStore(DEFAULT_STATE_PATH)
    queue = NotificationQueue(DEFAULT_STATE_PATH)
    matcher = OpenCVTemplateMatcher()
    capture = QuartzFrameCapture()
    templates = {template.path: template for group in config.groups.values() for rule in group.rules for template in rule.templates}
    runtime_ref: list[AutomationRuntime] = []

    def locate(path: Path):
        template = templates.get(path, TemplateSpec(path, .9))
        match = matcher.locate(capture.capture(), template)
        return capture.to_logical_point(match.center) if match else None

    pointer = QuartzPointerController()
    alarm = SystemAlarm()
    output = QuartzAutomationOutput(
        locate=locate,
        click=lambda point: pointer.click(ScreenPoint(*point)),
        play_sound=alarm.start,
        notify=lambda: logging.info("automation notification requested"),
        should_stop=lambda: bool(runtime_ref) and runtime_ref[0].is_suspended(),
    )
    def wait_for(template: TemplateSpec, state_name: str, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while True:
            present = matcher.locate(capture.capture(), template) is not None
            if present == (state_name == "present"):
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(.2, remaining))

    executor = GuardedActionExecutor(
        output,
        guard_matches=lambda path: locate(path) is not None,
        get_secret=KeychainStore().get,
        wait_for=wait_for,
        should_stop=lambda: bool(runtime_ref) and runtime_ref[0].is_suspended(),
    )
    runtime = AutomationRuntime(config.groups, state, executor, notify=queue.enqueue, stop_alarm=alarm.stop)
    runtime_ref.append(runtime)
    for group in arguments.arm:
        runtime.arm(group)
    rules = tuple(rule for group in config.groups.values() for rule in group.rules)
    runner = AutomationRunner(runtime, rules, capture, matcher)
    server = AutomationUnixSocketServer(DEFAULT_SOCKET_PATH, AutomationSocketProtocol(runtime))
    source_stamp = arguments.config.stat().st_mtime_ns
    server.start()
    try:
        while True:
            try:
                unchanged = arguments.config.stat().st_mtime_ns == source_stamp
            except OSError:
                unchanged = False
            if not unchanged:
                logging.warning("automation configuration changed or disappeared; stopping service")
                break
            if _pointer_is_in_emergency_corner():
                runtime.emergency_stop()
                logging.warning("automation emergency-stopped by pointer in top-left corner")
            started = time.monotonic()
            outcome = runner.scan_once()
            if outcome is not None:
                logging.info("automation outcome: succeeded=%s step=%s", outcome.succeeded, outcome.failed_step)
            time.sleep(max(0, config.scan_interval_seconds - (time.monotonic() - started)))
    finally:
        alarm.stop()
        server.stop()
        queue.close()
        state.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()

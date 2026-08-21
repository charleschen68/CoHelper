"""Manual entry point for the local CoHelper screen-automation service."""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from ai_drive.automation.actions import GuardedActionExecutor
from ai_drive.automation.config import AutomationConfig, DEFAULT_CONFIG_PATH, TemplateSpec
from ai_drive.automation.macos import QuartzFrameCapture
from ai_drive.automation.macos_output import QuartzAutomationOutput
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


def run() -> None:
    parser = argparse.ArgumentParser(description="Run CoHelper local screen automation")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--arm", action="append", default=[])
    arguments = parser.parse_args()
    config = AutomationConfig.load(arguments.config)
    state = AutomationStateStore(DEFAULT_STATE_PATH)
    matcher = OpenCVTemplateMatcher()
    capture = QuartzFrameCapture()
    templates = {template.path: template for group in config.groups.values() for rule in group.rules for template in rule.templates}

    def locate(path: Path):
        template = templates.get(path, TemplateSpec(path, .9))
        match = matcher.locate(capture.capture(), template)
        return match.center if match else None

    pointer = QuartzPointerController()
    alarm = SystemAlarm()
    output = QuartzAutomationOutput(
        locate=locate,
        click=lambda point: pointer.click(ScreenPoint(*point)),
        play_sound=alarm.start,
        notify=lambda: logging.info("automation notification requested"),
    )
    executor = GuardedActionExecutor(output, guard_matches=lambda path: locate(path) is not None, get_secret=KeychainStore().get)
    runtime = AutomationRuntime(config.groups, state, executor)
    for group in arguments.arm:
        runtime.arm(group)
    rules = tuple(rule for group in config.groups.values() for rule in group.rules)
    runner = AutomationRunner(runtime, rules, capture, matcher)
    server = AutomationUnixSocketServer(DEFAULT_SOCKET_PATH, AutomationSocketProtocol(runtime))
    source_stamp = arguments.config.stat().st_mtime_ns
    server.start()
    try:
        while arguments.config.stat().st_mtime_ns == source_stamp:
            started = time.monotonic()
            outcome = runner.scan_once()
            if outcome is not None:
                logging.info("automation outcome: succeeded=%s step=%s", outcome.succeeded, outcome.failed_step)
            time.sleep(max(0, config.scan_interval_seconds - (time.monotonic() - started)))
        logging.warning("automation configuration changed; stopping service")
    finally:
        alarm.stop()
        server.stop()
        state.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()

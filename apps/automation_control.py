"""Explicit local control client for the manually started automation service."""

from __future__ import annotations

import argparse

from ai_drive.automation.socket import AutomationSocketClient
from apps.automation_runtime import DEFAULT_SOCKET_PATH


def run() -> None:
    parser = argparse.ArgumentParser(description="Control CoHelper local screen automation")
    parser.add_argument("command", choices=("status", "start", "stop", "emergency-stop", "resume"))
    parser.add_argument("group", nargs="?")
    arguments = parser.parse_args()
    client = AutomationSocketClient(DEFAULT_SOCKET_PATH)
    if arguments.command == "status":
        if arguments.group:
            parser.error("status does not take a rule group")
        print(client.status())
        return
    if arguments.command == "emergency-stop":
        if arguments.group:
            parser.error("emergency-stop does not take a rule group")
        client.emergency_stop()
        print(client.status())
        return
    if arguments.command == "resume":
        if arguments.group:
            parser.error("resume does not take a rule group")
        client.resume()
        print(client.status())
        return
    if not arguments.group:
        parser.error(f"{arguments.command} requires a rule group")
    if arguments.command == "start":
        if arguments.group == "all":
            parser.error("start all is not allowed; choose one configured rule group")
        client.arm(arguments.group)
    else:
        client.disarm(arguments.group)
    print(client.status())


if __name__ == "__main__":
    run()

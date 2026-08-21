import time

from ai_drive.automation.emergency import EmergencyStopMonitor


def test_emergency_monitor_latches_stop_without_waiting_for_normal_scan_interval():
    corner = [False]
    stops = []
    monitor = EmergencyStopMonitor(lambda: corner[0], lambda: stops.append(time.monotonic()), interval_seconds=.01)
    monitor.start()
    corner[0] = True
    deadline = time.monotonic() + .25
    while not stops and time.monotonic() < deadline:
        time.sleep(.005)
    monitor.stop()

    assert len(stops) == 1

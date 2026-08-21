from threading import Event

from ai_drive.automation.sound import SystemAlarm


def test_latched_alarm_uses_one_worker_and_stops_cleanly():
    played = Event()
    count = 0

    def play_once():
        nonlocal count
        count += 1
        played.set()

    alarm = SystemAlarm(play_once=play_once, interval_seconds=0.01)
    alarm.start("latched")
    alarm.start("latched")
    assert played.wait(1)
    alarm.stop()
    after_stop = count
    Event().wait(.03)

    assert count == after_stop

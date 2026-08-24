import pytest

from ai_drive.voice import VoiceActionSafetyError, VoiceActionSafetyGate


def test_safety_gate_requires_masked_overlay_and_manual_resume():
    gate = VoiceActionSafetyGate()
    with pytest.raises(VoiceActionSafetyError, match="mask"):
        gate.assert_ready(overlay_masked=False)

    gate.emergency_stop()
    with pytest.raises(VoiceActionSafetyError, match="emergency-stopped"):
        gate.assert_ready(overlay_masked=True)
    with pytest.raises(VoiceActionSafetyError, match="manual"):
        gate.resume(manual=False)

    gate.resume(manual=True)
    gate.assert_ready(overlay_masked=True)


def test_disabled_safety_gate_fails_closed():
    gate = VoiceActionSafetyGate(enabled=False)
    with pytest.raises(VoiceActionSafetyError, match="disabled"):
        gate.assert_ready(overlay_masked=True)

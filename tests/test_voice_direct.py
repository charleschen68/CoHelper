import pytest

from ai_drive.voice import (
    VoiceDirectActionError,
    VoiceDirectTarget,
    VoiceDirectTargetStore,
)


def target(target_id="target-1", *, detected_at=10.0, voice_direct=True):
    return VoiceDirectTarget(target_id, "refresh_safari", detected_at, voice_direct)


def test_prepare_requires_one_fresh_voice_direct_target():
    store = VoiceDirectTargetStore()
    store.replace([target()])

    intent = store.prepare("voice-1", now=12.0)

    assert intent.target_id == "target-1"
    assert intent.rule_id == "refresh_safari"


def test_expired_or_non_direct_targets_cannot_be_prepared():
    store = VoiceDirectTargetStore()
    store.replace([target(detected_at=1.0, voice_direct=True)])
    with pytest.raises(VoiceDirectActionError, match="fresh"):
        store.prepare("voice-1", now=5.0)

    store.replace([target(voice_direct=False)])
    with pytest.raises(VoiceDirectActionError, match="fresh"):
        store.prepare("voice-1", now=12.0)


def test_ambiguous_targets_are_rejected():
    store = VoiceDirectTargetStore()
    store.replace([target("target-1"), target("target-2")])

    with pytest.raises(VoiceDirectActionError, match="ambiguous"):
        store.prepare("voice-1", now=12.0)


def test_consume_is_one_time_and_invalidated_by_replacement():
    store = VoiceDirectTargetStore()
    store.replace([target()])
    intent = store.prepare("voice-1", now=12.0)

    consumed = store.consume(intent, now=12.1)
    assert consumed.target_id == "target-1"
    with pytest.raises(VoiceDirectActionError, match="consumed"):
        store.consume(intent, now=12.2)

    store.replace([target("target-2")])
    with pytest.raises(VoiceDirectActionError, match="replaced"):
        store.consume(intent, now=12.2)


def test_disabled_store_does_not_retain_or_prepare_targets():
    store = VoiceDirectTargetStore(enabled=False)
    store.replace([target()])

    with pytest.raises(VoiceDirectActionError, match="disabled"):
        store.prepare("voice-1", now=12.0)

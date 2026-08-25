import pytest

from ai_drive.region_translation_runtime import RegionTranslationRuntime
from cohelper_core import Config


class MustNotTrigger:
    generation = 0

    def trigger(self):
        raise AssertionError("disabled feature must not trigger AppKit selection")

    def cancel(self):
        return False

    def close(self):
        pass


def test_disabled_feature_does_not_start_selection_runtime():
    runtime = RegionTranslationRuntime(Config({}), selection_controller=MustNotTrigger())

    with pytest.raises(RuntimeError, match="disabled"):
        runtime.trigger()

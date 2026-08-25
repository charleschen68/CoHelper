from io import BytesIO

import pytest
from PIL import Image

from ai_drive.region_capture import (
    RegionSelection,
    RegionSelectionError,
    crop_screenshot,
)
from ai_drive.vision import Screenshot


def screenshot() -> Screenshot:
    output = BytesIO()
    image = Image.new("RGB", (1200, 800), "white")
    image.save(output, format="PNG")
    return Screenshot(
        output.getvalue(),
        1200,
        800,
        600,
        400,
        7,
        123.0,
        "com.apple.Safari",
        100,
        50,
    )


def test_drag_is_normalized_to_display_local_logical_rect():
    selection = RegionSelection.from_drag(
        display_id=7,
        display_origin=(100, 50),
        display_size=(600, 400),
        start=(500, 350),
        end=(200, 100),
    )

    assert selection.x == 200
    assert selection.y == 100
    assert selection.width == 300
    assert selection.height == 250
    assert selection.display_id == 7


@pytest.mark.parametrize(
    "start, end",
    [((100, 50), (219, 130)), ((100, 50), (220, 129)), ((600, 450), (600, 450)), ((float("nan"), 50), (220, 130))],
)
def test_drag_rejects_too_small_or_zero_selection(start, end):
    with pytest.raises(RegionSelectionError, match="selection"):
        RegionSelection.from_drag(7, (100, 50), (600, 400), start, end)


def test_drag_rejects_selection_outside_or_crossing_display():
    with pytest.raises(RegionSelectionError, match="display"):
        RegionSelection.from_drag(7, (100, 50), (600, 400), (90, 100), (200, 200))
    with pytest.raises(RegionSelectionError, match="display"):
        RegionSelection.from_drag(7, (100, 50), (600, 400), (200, 100), (710, 200))


def test_crop_maps_logical_rect_to_retina_pixels_and_preserves_capture_context():
    frozen = screenshot()
    selection = RegionSelection(7, 200, 100, 300, 200)

    cropped = crop_screenshot(frozen, selection)

    assert cropped.pixel_width == 600
    assert cropped.pixel_height == 400
    assert cropped.logical_width == 300
    assert cropped.logical_height == 200
    assert cropped.origin_x == 200
    assert cropped.origin_y == 100
    assert cropped.display_id == frozen.display_id
    assert cropped.captured_at == frozen.captured_at
    assert cropped.frontmost_bundle_id == frozen.frontmost_bundle_id
    image = Image.open(BytesIO(cropped.image))
    assert image.size == (600, 400)


def test_crop_rejects_a_selection_from_another_display():
    with pytest.raises(RegionSelectionError, match="display"):
        crop_screenshot(screenshot(), RegionSelection(8, 200, 100, 300, 200))


def test_crop_rejects_capture_metadata_that_disagrees_with_decoded_image():
    output = BytesIO()
    Image.new("RGB", (10, 10), "white").save(output, format="PNG")
    malformed = Screenshot(output.getvalue(), 1200, 800, 600, 400, 7, 123.0, "app", 100, 50)

    with pytest.raises(RegionSelectionError, match="dimensions"):
        crop_screenshot(malformed, RegionSelection(7, 200, 100, 300, 200))

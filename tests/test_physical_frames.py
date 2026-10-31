"""Exact physical frames bypass legacy text geometry and retain every RGB value."""

import pytest
from src.led.matrix import LedFrame
from src.outputs.cells import RichCells, cells_from_codes

physical_frame_layer = pytest.importorskip(
    "src.outputs.physical_frames",
    reason="Flexible-frame SDK is not available on this core",
).physical_frame_layer
from .conftest import build

WIDTH, HEIGHT = 52, 16


def tagged():
    pixels = bytes((i * 37 + 5) % 256 for i in range(WIDTH * HEIGHT * 3))
    cells = RichCells(
        cells_from_codes([[0]]),
        layers=(physical_frame_layer(LedFrame(WIDTH, HEIGHT, pixels)),),
    )
    return cells, pixels


def test_exact_physical_rgb_survives_adapter_render():
    cells, expected = tagged()
    assert build({"host": "192.0.2.10"}).render(cells) == expected


def test_wrong_physical_surface_is_refused():
    cells = RichCells(
        cells_from_codes([[0]]),
        layers=(physical_frame_layer(LedFrame(1, 1, b"\x01\x02\x03")),),
    )
    with pytest.raises(ValueError, match="surface"):
        build({"host": "192.0.2.10"}).render(cells)


@pytest.mark.parametrize("spec", ["none", "slide"])
def test_transition_to_physical_frame_preserves_final_rgb(monkeypatch, spec):
    from types import SimpleNamespace

    from src.plugins import CancelToken, WriteResult

    cells, expected = tagged()
    plugin = build({"host": "192.0.2.10"})
    delivered = []
    monkeypatch.setattr(
        plugin,
        "_still",
        lambda pixels, cancel: delivered.append(pixels) or WriteResult(True, True),
    )
    # A physical page must not re-enter the legacy transition layout path.
    monkeypatch.setattr(
        plugin,
        "layout",
        lambda frame: pytest.fail("physical transition entered legacy layout"),
    )
    result = plugin.write_transition(
        cells_from_codes([[0]]), cells, SimpleNamespace(spec=spec), cancel=CancelToken()
    )
    assert result.success and delivered == [expected]


def test_native_upload_preserves_exact_physical_rgb(make_plugin, clock):
    from types import SimpleNamespace

    from src.plugins import CancelToken

    cells, expected = tagged()
    plugin = make_plugin(native_rotation=True)
    playlist = SimpleNamespace(
        board_id="board-test",
        reference_id="collection:flex",
        entries=(SimpleNamespace(page_id="flex", cells=cells, duration_ms=30000),),
    )
    result = plugin.sync_native_playlist(playlist, cancel=CancelToken())
    assert result.success and result.was_sent
    names = [name for name in clock.apps if name.startswith("fb_")]
    assert len(names) == 1
    assert clock.bitmap(clock.apps[names[0]]) == expected


def test_ordinary_upload_preserves_exact_physical_rgb(make_plugin, clock):
    from src.plugins import CancelToken

    cells, expected = tagged()
    plugin = make_plugin()
    result = plugin.write_cells(cells, native=None, cancel=CancelToken())
    assert result.success and result.was_sent
    assert clock.bitmap(clock.apps[plugin.app_name]) == expected


def test_transition_from_physical_to_legacy_keeps_the_legacy_result(monkeypatch):
    from types import SimpleNamespace

    from src.plugins import CancelToken, WriteResult

    from .conftest import grid

    before, _ = tagged()
    plugin = build({"host": "192.0.2.10"})
    after = cells_from_codes(grid(1))
    expected = plugin.render(after)
    delivered = []
    monkeypatch.setattr(
        plugin,
        "_still",
        lambda pixels, cancel: delivered.append(pixels) or WriteResult(True, True),
    )
    monkeypatch.setattr(
        plugin,
        "_play",
        lambda *args: pytest.fail("mixed coordinate spaces entered legacy animation"),
    )
    result = plugin.write_transition(
        before, after, SimpleNamespace(spec="slide"), cancel=CancelToken()
    )
    assert result.success and delivered == [expected]

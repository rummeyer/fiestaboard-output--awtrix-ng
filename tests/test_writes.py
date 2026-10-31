"""Writes against the mock clock: pushes, switching, streaming, cancelling."""

from __future__ import annotations

import time

import pytest
from plugins.ulanzi_tc002 import FADE_STEPS, PANEL_HEIGHT, PANEL_WIDTH, SEQUENCE_MAX_FRAMES

from src.led.transition_registry import ResolvedLedTransition
from src.led.transitions import LedTransitionSpec
from src.plugins import CancelToken, TimedFrame, cells_from_codes

from .conftest import grid, render
from .mock_awtrix import ACTIVE, PUSHED, MockAwtrix


def cells(code: int):
    return cells_from_codes(grid(code))


def transition(kind: str, **fields) -> ResolvedLedTransition:
    return ResolvedLedTransition(id=kind, spec=LedTransitionSpec(kind=kind, **fields), source="explicit")


def test_a_still_is_one_push_of_a_native_size_layout(clock, make_plugin):
    result = make_plugin(show_on_change=False).write(grid(1), native=None, cancel=CancelToken())

    assert (result.success, result.was_sent) == (True, True)
    assert clock.calls() == [("GET", "/api/v1/apps"), ("PUT", PUSHED + "FiestaBoard")]
    body = clock.pushes()[0]
    region = body["layout"]["regions"][0]
    assert body["layout"]["version"] == 1
    assert region["box"] == [0, 0, PANEL_WIDTH, PANEL_HEIGHT]
    assert region["draw"][0][:5] == ["bitmap", 0, 0, PANEL_WIDTH, PANEL_HEIGHT]
    assert MockAwtrix.bitmap(body) == render(grid(1))
    assert "durationMs" not in body


def test_show_on_change_switches_to_the_board_after_a_still(clock, make_plugin):
    result = make_plugin().write_cells(cells(2), native=None, cancel=CancelToken())

    assert result.was_sent
    assert clock.calls() == [("GET", "/api/v1/apps"), ("PUT", PUSHED + "FiestaBoard"), ("PUT", ACTIVE)]
    assert clock.switches() == [{"name": "FiestaBoard", "fast": True}]
    assert clock.current_app == "FiestaBoard"


def test_the_app_name_and_display_time_come_from_the_settings(clock, make_plugin):
    make_plugin(app_name="Board_2", duration_s=30).write(grid(1), native=None, cancel=CancelToken())

    assert clock.calls()[:2] == [("GET", "/api/v1/apps"), ("PUT", PUSHED + "Board_2")]
    assert clock.pushes()[0]["durationMs"] == 30_000
    assert clock.switches() == [{"name": "Board_2", "fast": True}]


def test_an_invalid_app_name_falls_back_to_the_default(clock, make_plugin):
    make_plugin(app_name="not a name!", show_on_change=False).write(grid(1), native=None, cancel=CancelToken())
    assert clock.calls() == [("GET", "/api/v1/apps"), ("PUT", PUSHED + "FiestaBoard")]


def test_basic_auth_is_sent_when_set(clock, make_plugin):
    clock.password = "s3cret"
    ok = make_plugin(username="admin", password="s3cret").write(grid(1), native=None, cancel=CancelToken())
    assert ok.was_sent
    refused = make_plugin(username="admin", password="wrong").write(grid(1), native=None, cancel=CancelToken())
    assert (refused.success, refused.was_sent) == (False, False)


def test_a_failed_push_is_a_failed_write(clock, make_plugin):
    clock.mode = "http_500"
    result = make_plugin().write(grid(1), native=None, cancel=CancelToken())
    assert (result.success, result.was_sent) == (False, False)
    assert clock.switches() == []


def test_a_failed_switch_never_fails_the_write(clock, make_plugin):
    clock.fail_paths = (ACTIVE,)
    result = make_plugin().write(grid(1), native=None, cancel=CancelToken())
    assert (result.success, result.was_sent) == (True, True)


def test_no_address_is_a_failed_write(make_plugin):
    result = make_plugin(host="").write(grid(1), native=None, cancel=CancelToken())
    assert (result.success, result.was_sent) == (False, False)


def test_a_cancelled_write_sends_nothing(clock, make_plugin):
    cancel = CancelToken()
    cancel._event.set()
    result = make_plugin().write(grid(1), native=None, cancel=cancel)
    assert (result.success, result.was_sent) == (True, False)
    assert clock.requests == []


# --- transitions ------------------------------------------------------------------------------


def test_a_flip_switches_first_then_streams_and_lands_on_the_new_page(clock, make_plugin):
    plugin = make_plugin()
    plugin.write_cells(cells(1), native=None, cancel=CancelToken())
    clock.clear()

    result = plugin.write_transition(cells(1), cells(2), transition("flip"), cancel=CancelToken())

    assert (result.success, result.was_sent) == (True, True)
    calls = clock.calls()
    assert calls[0] == ("PUT", ACTIVE)
    assert len(clock.pushes()) > 1 and all(c == ("PUT", PUSHED + "FiestaBoard") for c in calls[1:])
    assert MockAwtrix.bitmap(clock.pushes()[-1]) == render(grid(2))


def test_a_board_not_there_yet_gets_the_new_page_and_then_the_switch(clock, make_plugin):
    # After a restart the app is gone: switching fails, so the transition is
    # not streamed off screen; the target lands and is then switched to.
    result = make_plugin().write_transition(cells(1), cells(2), transition("flip"), cancel=CancelToken())

    assert result.was_sent
    assert clock.calls() == [("PUT", ACTIVE), ("GET", "/api/v1/apps"), ("PUT", PUSHED + "FiestaBoard"), ("PUT", ACTIVE)]
    assert MockAwtrix.bitmap(clock.pushes()[0]) == render(grid(2))
    assert clock.current_app == "FiestaBoard"


def test_without_show_on_change_a_board_off_screen_gets_only_the_new_page(clock, make_plugin):
    plugin = make_plugin(show_on_change=False)
    plugin.write_cells(cells(1), native=None, cancel=CancelToken())
    clock.clear()

    result = plugin.write_transition(cells(1), cells(2), transition("slide"), cancel=CancelToken())

    assert result.was_sent
    assert clock.calls() == [("GET", "/api/v1/device"), ("PUT", PUSHED + "FiestaBoard")]
    assert MockAwtrix.bitmap(clock.pushes()[0]) == render(grid(2))


def test_without_show_on_change_a_board_on_screen_streams(clock, make_plugin):
    plugin = make_plugin(show_on_change=False)
    plugin.write_cells(cells(1), native=None, cancel=CancelToken())
    clock.current_app = "fiestaboard"  # names match case-insensitively
    clock.clear()

    plugin.write_transition(cells(1), cells(2), transition("wipe"), cancel=CancelToken())

    assert clock.calls()[0] == ("GET", "/api/v1/device")
    assert len(clock.pushes()) > 1
    assert clock.switches() == []


def test_none_is_one_still(clock, make_plugin):
    resolved = ResolvedLedTransition(id="none", spec="none", source="explicit")
    make_plugin(show_on_change=False).write_transition(cells(1), cells(2), resolved, cancel=CancelToken())
    assert len(clock.pushes()) == 1


def test_fade_goes_through_black_in_pixels(clock, make_plugin):
    plugin = make_plugin()
    plugin.write_cells(cells(1), native=None, cancel=CancelToken())
    clock.clear()

    plugin.write_transition(cells(1), cells(2), transition("fade"), cancel=CancelToken())

    frames = [MockAwtrix.bitmap(b) for b in clock.pushes()]
    assert len(frames) == 2 * FADE_STEPS
    assert frames[FADE_STEPS - 1] == bytes(len(frames[0]))  # black in the middle
    assert frames[-1] == render(grid(2))


def test_fade_blend_is_planned_by_core(clock, make_plugin):
    plugin = make_plugin(fade_style="blend")
    plugin.write_cells(cells(1), native=None, cancel=CancelToken())
    clock.clear()

    plugin.write_transition(cells(1), cells(2), transition("fade"), cancel=CancelToken())

    frames = [MockAwtrix.bitmap(b) for b in clock.pushes()]
    assert frames[-1] == render(grid(2))
    assert bytes(len(frames[0])) not in frames


def test_streamed_frames_are_paced(clock, make_plugin):
    plugin = make_plugin(fast=False)
    plugin.write_cells(cells(1), native=None, cancel=CancelToken())
    clock.clear()

    started = time.monotonic()
    plugin.write_transition(cells(1), cells(2), transition("fade"), cancel=CancelToken())
    elapsed = time.monotonic() - started

    # Six frames at five a second: five gaps of 0.2 s.
    assert elapsed >= 5 * 0.2 - 0.05


def test_a_newer_page_stops_the_stream_between_frames(clock, make_plugin):
    plugin = make_plugin()
    plugin.write_cells(cells(1), native=None, cancel=CancelToken())
    clock.clear()
    cancel = CancelToken()

    def fire_after_second_push(method, path, body, index):
        if path.startswith(PUSHED) and len(clock.pushes()) == 2:
            cancel._event.set()

    clock.on_request = fire_after_second_push
    result = plugin.write_transition(cells(1), cells(2), transition("fade"), cancel=cancel)

    assert (result.success, result.was_sent) == (True, False)
    assert len(clock.pushes()) == 2


def test_a_push_failing_mid_stream_fails_the_write(clock, make_plugin):
    plugin = make_plugin()
    plugin.write_cells(cells(1), native=None, cancel=CancelToken())
    clock.clear()

    def fail_from_third_push(method, path, body, index):
        if path.startswith(PUSHED) and len(clock.pushes()) == 3:
            clock.mode = "http_500"

    clock.on_request = fail_from_third_push
    result = plugin.write_transition(cells(1), cells(2), transition("fade"), cancel=CancelToken())
    assert (result.success, result.was_sent) == (False, False)


# --- sequences --------------------------------------------------------------------------------


def test_a_sequence_streams_its_frames_and_ends_on_the_last(clock, make_plugin):
    frames = [TimedFrame(grid(code), 50) for code in (1, 2, 3)]
    result = make_plugin().write_sequence(frames, cancel=CancelToken())

    assert result.was_sent
    assert [MockAwtrix.bitmap(b) for b in clock.pushes()][-1] == render(grid(3))


def test_a_long_sequence_is_thinned_keeping_first_and_last(clock, make_plugin):
    plugin = make_plugin()
    plugin.write_cells(cells(1), native=None, cancel=CancelToken())
    clock.current_app = "FiestaBoard"
    clock.clear()
    frames = [TimedFrame(grid(code % 26 + 1), 0) for code in range(100)]

    plugin.write_sequence(frames, cancel=CancelToken())

    pushed = [MockAwtrix.bitmap(b) for b in clock.pushes()]
    assert len(pushed) == SEQUENCE_MAX_FRAMES
    assert pushed[0] == render(grid(1)) and pushed[-1] == render(grid(99 % 26 + 1))


@pytest.mark.parametrize("frames", [[]])
def test_an_empty_sequence_sends_nothing(clock, make_plugin, frames):
    result = make_plugin().write_sequence(frames, cancel=CancelToken())
    assert (result.success, result.was_sent) == (True, False)
    assert clock.requests == []

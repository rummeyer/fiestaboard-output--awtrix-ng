"""Optional device-wide transition controls; all writes are loopback only."""
import pytest
from threading import Event

from src.plugins import CancelToken

from .conftest import grid


def write(plugin):
    return plugin.write(grid(1), native=None, cancel=CancelToken())


def patches(clock):
    return [body for method, path, body in clock.requests if method == "PATCH"]


@pytest.mark.parametrize("config", [{}, {"firmware_transition_override": False, "firmware_transition_duration_ms": 0},
                                  {"firmware_transition_override": True},
                                  {"firmware_transition_override": True, "firmware_transition_effect": "device",
                                   "firmware_transition_direction": "device", "firmware_auto_rotation": "device"},
                                  {"firmware_transition_override": True, "firmware_transition_duration_ms": ""}])
def test_unset_or_disabled_override_never_writes_firmware_settings(make_plugin, clock, config):
    assert write(make_plugin(**config)).success
    assert patches(clock) == []
    assert clock.settings["transitionDurationMs"] == 1000


def test_explicit_zero_and_disabled_rotation_are_applied_and_read_back_once(make_plugin, clock):
    plugin = make_plugin(firmware_transition_override=True, firmware_transition_effect="Fade",
                         firmware_transition_direction="reverse", firmware_transition_duration_ms=0,
                         firmware_auto_rotation="off")
    assert write(plugin).success
    expected = {"transitionEffect": "Fade", "transitionDirection": "reverse", "transitionDurationMs": 0,
                "autoTransition": False}
    assert patches(clock) == [expected]
    assert clock.settings == expected
    assert write(plugin).success
    assert patches(clock) == [expected]


@pytest.mark.parametrize("duration", [-1, True, "12"])
def test_invalid_duration_is_refused_before_any_device_mutation(make_plugin, clock, duration):
    plugin = make_plugin(firmware_transition_override=True, firmware_transition_duration_ms=duration)
    assert not write(plugin).success
    assert all(method == "GET" for method, _ in clock.calls())


def test_effect_must_be_advertised_by_this_device(make_plugin, clock):
    clock.transitions = ["Slide"]
    plugin = make_plugin(firmware_transition_override=True, firmware_transition_effect="Fade")
    assert not write(plugin).success
    assert not any(method != "GET" for method, _ in clock.calls())


def test_settings_failure_blocks_output_then_retries(make_plugin, clock):
    plugin = make_plugin(firmware_transition_override=True, firmware_transition_duration_ms=0)
    clock.fail_paths = ("/api/v1/settings",)
    assert not write(plugin).success
    assert clock.apps == {}
    clock.fail_paths = ()
    assert write(plugin).success
    assert clock.settings["transitionDurationMs"] == 0


def test_readback_mismatch_is_not_reported_as_success(make_plugin, clock):
    plugin = make_plugin(firmware_transition_override=True, firmware_transition_effect="Fade")
    clock.ignore_settings_patch = True
    assert not write(plugin).success
    assert clock.apps == {}
    clock.ignore_settings_patch = False
    assert write(plugin).success
    assert clock.settings["transitionEffect"] == "Fade"


def test_test_connection_reports_actual_settings_without_applying_override(make_plugin, clock):
    plugin = make_plugin(firmware_transition_override=True, firmware_transition_effect="Fade")
    check = plugin.check_connection()
    assert check.success
    assert check.details["firmware_transitions"]["transitionEffect"] == "Rain"
    assert "Rain" in check.message
    assert patches(clock) == [] and clock.apps == {}


def test_wrong_device_never_receives_a_transition_patch(make_plugin, clock):
    clock.panel_width = 64
    plugin = make_plugin(firmware_transition_override=True, firmware_transition_effect="Fade")
    assert not write(plugin).success
    assert all(method == "GET" for method, _ in clock.calls())


def test_cancellation_after_settings_patch_is_superseded_and_retried(make_plugin, clock):
    plugin = make_plugin(firmware_transition_override=True, firmware_transition_duration_ms=0)
    event = Event()
    clock.on_request = lambda method, *_: event.set() if method == "PATCH" else None
    result = plugin.write(grid(1), native=None, cancel=CancelToken(event))
    assert result.superseded and not result.was_sent
    assert clock.apps == {}
    clock.on_request = None
    assert write(plugin).success
    assert len(patches(clock)) == 2


def test_turning_override_off_leaves_current_device_values_in_place(make_plugin, clock):
    assert write(make_plugin(firmware_transition_override=True, firmware_transition_duration_ms=0)).success
    clock.clear()
    assert write(make_plugin(firmware_transition_override=False)).success
    assert patches(clock) == [] and clock.settings["transitionDurationMs"] == 0


def test_native_rotation_uses_firmware_settings_without_streamed_page_transition(make_plugin, clock):
    from src import plugins
    if not hasattr(plugins, "NativePlaylist"):
        pytest.skip("Native playlist SDK required")
    plugin = make_plugin(native_rotation=True, firmware_transition_override=True,
                         firmware_transition_effect="Fade", firmware_transition_duration_ms=0)
    value = plugins.NativePlaylist("board-test", "collection:demo", (
        plugins.NativePlaylistEntry("clock", plugins.cells_from_codes(grid(1)), 30000),
        plugins.NativePlaylistEntry("art", plugins.cells_from_codes(grid(2)), 30000),
    ))
    assert plugin.sync_native_playlist(value, cancel=CancelToken()).success
    assert patches(clock) == [{"transitionEffect": "Fade", "transitionDurationMs": 0}]
    assert len(clock.pushes()) == 2
    clock.current_app = list(clock.apps)[1]
    clock.clear()
    assert plugin.sync_native_playlist(value, cancel=CancelToken()).success
    assert patches(clock) == [] and clock.pushes() == [] and clock.switches() == []


def test_connection_inspection_does_not_mask_native_write_cancellation(make_plugin, clock):
    from src import plugins
    if not hasattr(plugins, "NativePlaylist"):
        pytest.skip("Native playlist SDK required")
    plugin = make_plugin(native_rotation=True, firmware_transition_override=True, firmware_transition_effect="Fade")
    event = Event()
    clock.on_request = lambda method, path, body, index: event.set() if index == 2 else None
    value = plugins.NativePlaylist("board-test", "clock", (
        plugins.NativePlaylistEntry("clock", plugins.cells_from_codes(grid(1)), 0),
    ))
    with plugin.http.cancel_scope(CancelToken(event)):
        result = plugin.sync_native_playlist(value, cancel=CancelToken(event))
    assert result.superseded
    assert clock.calls() == [("GET", "/api/v1/device"), ("GET", "/api/v1/capabilities"), ("GET", "/api/v1/device")]

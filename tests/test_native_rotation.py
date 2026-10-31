"""Native app rotation over real loopback HTTP; no hardware or AI calls."""
from types import SimpleNamespace
from threading import Event

import pytest

from src import plugins
from src.plugins import CancelToken, cells_from_codes

pytestmark = pytest.mark.skipif(not hasattr(plugins, "NativePageSelection"), reason="Native playlist core SDK required")

from .conftest import grid


def playlist(*pages, reference="collection:demo", duration=30000):
    return SimpleNamespace(board_id="board-test", reference_id=reference, entries=tuple(
        SimpleNamespace(page_id=page_id, cells=cells_from_codes(grid(code)), duration_ms=duration)
        for page_id, code in pages
    ))


def sync(plugin, value):
    return plugin.sync_native_playlist(value, cancel=CancelToken())


def owned(clock):
    return [name for name in clock.apps if name.startswith("fb_")]


def test_timed_members_become_ordered_slots_with_full_pixels_and_duration(make_plugin, clock):
    plugin = make_plugin(native_rotation=True)
    result = sync(plugin, playlist(("clock", 1), ("art", 2)))
    assert result.success and result.was_sent
    names = owned(clock)
    assert len(names) == 2
    assert [clock.bitmap(clock.apps[n]) for n in names] == [plugin.render(grid(1)), plugin.render(grid(2))]
    assert [clock.apps[n]["durationMs"] for n in names] == [30000, 30000]
    assert clock.order[-2:] == names
    assert clock.current_app == names[0]


def test_refresh_preserves_button_selection_and_deduplicates_payloads(make_plugin, clock):
    plugin = make_plugin(native_rotation=True)
    value = playlist(("clock", 1), ("art", 2))
    sync(plugin, value)
    names = owned(clock)
    clock.current_app = names[1]  # a firmware-side button selected the second app
    clock.clear()
    result = sync(plugin, value)
    assert result.success and not result.was_sent
    assert clock.pushes() == [] and clock.switches() == []
    assert clock.current_app == names[1]
    result = sync(plugin, playlist(("clock", 3), ("art", 2)))
    assert result.success
    assert len(clock.pushes()) == 1 and clock.switches() == []
    assert clock.current_app == names[1]


def test_reorder_cleanup_preserves_foreign_apps_and_disabled_battery(make_plugin, clock):
    clock.apps["other_owner"] = {"text": "OTHER"}
    clock.order = ["Time", "other_owner", "Status"]
    clock.disabled = ["Status", "Spare"]
    plugin = make_plugin(native_rotation=True)
    sync(plugin, playlist(("clock", 1), ("art", 2)))
    old = owned(clock)
    result = sync(plugin, playlist(("art", 2), reference="art", duration=0))
    assert result.success
    assert owned(clock) == [old[1]]
    assert clock.order == ["Time", "other_owner", "Status", "Spare", old[1]]
    assert set(clock.disabled) == {"Status", "Spare"}
    assert clock.apps["other_owner"] == {"text": "OTHER"}


def test_capacity_failure_does_not_change_existing_rotation(make_plugin, clock):
    clock.apps.update({f"foreign{i}": {"text": str(i)} for i in range(49)})
    plugin = make_plugin(native_rotation=True)
    result = sync(plugin, playlist(("clock", 1), ("art", 2)))
    assert not result.success
    assert len(clock.apps) == 49 and clock.order == []
    assert not any(method != "GET" for method, _ in clock.calls())


def test_reboot_restores_slots_and_process_restart_preserves_selected_member(make_plugin, clock):
    first = make_plugin(native_rotation=True)
    value = playlist(("clock", 1), ("art", 2))
    sync(first, value)
    names = owned(clock)
    clock.current_app = names[1]
    second = make_plugin(native_rotation=True)
    clock.clear()
    assert sync(second, value).success
    assert clock.current_app == names[1] and clock.switches() == []
    clock.restart()
    assert sync(second, value).success
    assert owned(clock) == names
    assert clock.current_app == names[0]


def test_recreated_opt_out_retires_only_owned_slots_after_legacy_push(make_plugin, clock):
    clock.apps["foreign"] = {"text": "KEEP"}
    clock.disabled = ["Status"]
    sync(make_plugin(native_rotation=True), playlist(("clock", 1), ("art", 2)))
    legacy = make_plugin(native_rotation=False)
    result = legacy.write(grid(3), native=None, cancel=CancelToken())
    assert result.success
    assert not owned(clock)
    assert clock.apps["foreign"] == {"text": "KEEP"}
    assert clock.bitmap(clock.apps[legacy.app_name]) == legacy.render(grid(3))
    assert clock.disabled == ["Status"]


def test_partial_push_stays_out_of_rotation_and_retry_converges(make_plugin, clock):
    plugin = make_plugin(native_rotation=True)
    value = playlist(("clock", 1), ("art", 2))
    def fail_second(method, path, body, index):
        if method == "PUT" and "/pushed/" in path and clock.apps:
            clock.fail_paths = (path,)
    clock.on_request = fail_second
    assert not sync(plugin, value).success
    assert all(name in clock.disabled for name in owned(clock))
    assert clock.current_app == "Time"
    clock.on_request = None
    clock.fail_paths = ()
    # A process restart during an interrupted update must also converge.
    assert sync(make_plugin(native_rotation=True), value).success
    assert len(owned(clock)) == 2
    assert not set(owned(clock)) & set(clock.disabled)


def test_missing_slot_is_reported_as_restart_and_current_member_as_feedback(make_plugin, clock):
    from src import plugins
    if not hasattr(plugins, "NativePageSelection"):
        pytest.skip("Selection feedback needs the native playlist core SDK")
    plugin = make_plugin(native_rotation=True)
    sync(plugin, playlist(("clock", 1), ("art", 2)))
    names = owned(clock)
    clock.current_app = names[1]
    news = plugin.poll_device()
    assert news.native_selection.page_id == "art"
    assert news.restarted is False
    del clock.apps[names[0]]
    assert plugin.poll_device().restarted is True
    clock.current_app = "Status"
    assert plugin.poll_device().native_selection.page_id is None


def test_default_legacy_output_does_not_touch_other_owners_native_slots(make_plugin, clock):
    clock.apps["fb_000000000000_111111111111"] = {"text": "FOREIGN"}
    plugin = make_plugin()
    assert plugin.write(grid(1), native=None, cancel=CancelToken()).success
    assert clock.apps["fb_000000000000_111111111111"] == {"text": "FOREIGN"}


def test_cancel_before_sync_performs_no_device_requests(make_plugin, clock):
    plugin = make_plugin(native_rotation=True)
    event = Event()
    event.set()
    token = CancelToken(event)
    result = plugin.sync_native_playlist(playlist(("clock", 1)), cancel=token)
    assert result.superseded and not result.was_sent
    assert clock.calls() == []


def test_duplicate_page_uses_one_payload_and_repeated_order_slot(make_plugin, clock):
    plugin = make_plugin(native_rotation=True)
    assert sync(plugin, playlist(("clock", 1), ("art", 2), ("clock", 1))).success
    names = owned(clock)
    assert len(names) == 2
    assert clock.order[-3:] == [names[0], names[1], names[0]]


def test_foreign_active_slot_is_not_stolen_by_refresh(make_plugin, clock):
    plugin = make_plugin(native_rotation=True)
    sync(plugin, playlist(("clock", 1)))
    clock.current_app = "Time"
    clock.clear()
    assert sync(plugin, playlist(("clock", 2))).success
    assert clock.current_app == "Time" and clock.switches() == []


@pytest.mark.parametrize("board_type,width", [("awtrixng", 32), ("tc002", 64)])
def test_wrong_hardware_receives_no_native_writes(make_plugin, clock, board_type, width):
    clock.board_type, clock.panel_width = board_type, width
    assert not sync(make_plugin(native_rotation=True), playlist(("clock", 1))).success
    assert all(method == "GET" for method, _ in clock.calls())


def test_fifty_apps_fit_but_one_more_preserves_them(make_plugin, clock):
    clock.apps.update({f"foreign{i}": {"text": str(i)} for i in range(49)})
    plugin = make_plugin(native_rotation=True)
    assert sync(plugin, playlist(("clock", 1))).success
    clock.clear()
    assert not sync(plugin, playlist(("clock", 1), ("art", 2))).success
    assert len(clock.apps) == 50
    assert all(method == "GET" for method, _ in clock.calls())


def test_empty_playlist_retires_owned_slots_without_removing_foreign_apps(make_plugin, clock):
    clock.apps["foreign"] = {"text": "KEEP"}
    plugin = make_plugin(native_rotation=True)
    sync(plugin, playlist(("clock", 1)))
    assert sync(plugin, playlist()).success
    assert not owned(clock) and clock.apps == {"foreign": {"text": "KEEP"}}


def test_old_core_rejects_opt_in_with_upgrade_guidance_without_writing(make_plugin, clock, monkeypatch):
    import plugins.ulanzi_tc002 as output
    monkeypatch.setattr(output, "NativePageSelection", None)
    plugin = make_plugin(native_rotation=True)
    check = plugin.check_connection()
    assert not check.success and "upgrade" in check.message.lower()
    assert not plugin.write(grid(1), native=None, cancel=CancelToken()).success
    assert clock.calls() == []


def test_firmware_modules_do_not_block_native_or_legacy_output(make_plugin, clock):
    clock.modules = ["helper_library"]
    plugin = make_plugin(native_rotation=True)
    assert sync(plugin, playlist(("clock", 1))).success
    assert plugin.write(grid(2), native=None, cancel=CancelToken()).success
    assert "helper_library" not in clock.order
    assert not any(method == "DELETE" and path.endswith("helper_library") for method, path in clock.calls())


def test_full_native_capacity_can_opt_out_to_legacy_slot(make_plugin, clock):
    value = playlist(*[(f"page{i}", 1) for i in range(50)])
    assert sync(make_plugin(native_rotation=True), value).success
    plugin = make_plugin(native_rotation=False)
    assert plugin.write(grid(2), native=None, cancel=CancelToken()).success
    assert list(clock.apps) == ["FiestaBoard"]
    assert clock.bitmap(clock.apps["FiestaBoard"]) == plugin.render(grid(2))


@pytest.mark.parametrize("recreate", [False, True])
def test_applied_but_unsaved_order_is_retried_until_persisted(make_plugin, clock, recreate):
    plugin = make_plugin(native_rotation=True)
    assert sync(plugin, playlist(("clock", 1), ("art", 2))).success
    clock.unsaved_order_once = True
    reordered = playlist(("art", 2), ("clock", 1))
    assert not sync(plugin, reordered).success
    assert clock.saved_order != clock.order
    if recreate:
        plugin = make_plugin(native_rotation=True)
    assert sync(plugin, reordered).success
    assert clock.saved_order == clock.order
    assert clock.saved_disabled == clock.disabled


def test_module_with_owned_name_is_never_overwritten(make_plugin, clock):
    plugin = make_plugin(native_rotation=True)
    value = playlist(("clock", 1))
    assert sync(plugin, value).success
    name = owned(clock)[0]
    clock.apps.clear()
    clock.modules = [name]
    clock.clear()
    assert not sync(plugin, value).success
    assert all(method == "GET" for method, _ in clock.calls())


def test_cancellation_during_validation_is_superseded_without_more_requests(make_plugin, clock):
    plugin = make_plugin(native_rotation=True)
    event = Event()
    token = CancelToken(event)
    clock.on_request = lambda *_: event.set()
    with plugin.http.cancel_scope(token):
        result = plugin.sync_native_playlist(playlist(("clock", 1)), cancel=token)
    assert result.superseded and not plugin._upload_failed
    assert clock.calls() == [("GET", "/api/v1/device")]


def test_native_layout_is_physical_52_by_16_and_keeps_last_row(make_plugin, clock):
    from src.plugins import NativePlaylist, NativePlaylistEntry, cells_from_codes
    plugin = make_plugin(native_rotation=True)
    cells = cells_from_codes([[1] * 13, [2] * 13, [3] * 13])
    value = NativePlaylist("board-test", "layout", (NativePlaylistEntry("edge", cells, 9000),))
    assert sync(plugin, value).success
    payload = clock.pushes()[0]
    region = payload["layout"]["regions"][0]
    assert region["box"] == [0, 0, 52, 16]
    assert region["draw"][0][1:5] == [0, 0, 52, 16]
    pixels = clock.bitmap(payload)
    assert len(pixels) == 52 * 16 * 3
    assert pixels == plugin.render(cells)
    assert any(pixels[-52 * 3:])


def test_missing_layout_capability_refuses_native_mutations(make_plugin, clock):
    clock.layout_supported = False
    result = sync(make_plugin(native_rotation=True), playlist(("clock", 1)))
    assert not result.success
    assert all(method == "GET" for method, _ in clock.calls())


def test_native_canvas_bottom_edge_is_not_centered_or_lost(make_plugin, clock):
    from src.canvas import CanvasLayer
    from src.outputs.cells import RichCells
    from src.plugins import NativePlaylist, NativePlaylistEntry, cells_from_codes
    # The bottom-right virtual pixel y16 must land at physical y15.
    layer = CanvasLayer(51, 16, 1, 1, bytes([255, 0, 127, 255]))
    frame = RichCells(cells_from_codes([[0] * 13 for _ in range(3)]), [layer])
    value = NativePlaylist("board-test", "canvas", (NativePlaylistEntry("edge", frame, 1000),))
    assert sync(make_plugin(native_rotation=True), value).success
    pixels = clock.bitmap(clock.pushes()[0])
    assert pixels == bytes(52 * 16 * 3 - 3) + bytes([255, 0, 127])


def test_capacity_failure_with_override_leaves_settings_unchanged(make_plugin, clock):
    clock.apps.update({f"foreign{i}": {"text": str(i)} for i in range(49)})
    plugin = make_plugin(native_rotation=True, firmware_transition_override=True,
                         firmware_auto_rotation="off")
    before = dict(clock.settings)
    assert not sync(plugin, playlist(("one", 1), ("two", 2))).success
    assert clock.settings == before
    assert all(method == "GET" for method, _ in clock.calls())


def test_native_first_sync_does_not_claim_existing_legacy_name(make_plugin, clock):
    clock.apps["FiestaBoard"] = {"text": "OTHER OWNER"}
    clock.order = ["Time", "FiestaBoard", "Status"]
    plugin = make_plugin(native_rotation=True)
    assert sync(plugin, playlist(("one", 1))).success
    assert clock.apps["FiestaBoard"] == {"text": "OTHER OWNER"}
    assert clock.order[:3] == ["Time", "FiestaBoard", "Status"]
    assert not any(method == "DELETE" and path.endswith("/FiestaBoard") for method, path in clock.calls())


def test_native_migration_cleans_legacy_app_only_after_this_writer_created_it(make_plugin, clock):
    plugin = make_plugin(native_rotation=True)
    assert plugin.write(grid(1), native=None, cancel=CancelToken()).success
    assert "FiestaBoard" in clock.apps
    assert sync(plugin, playlist(("one", 1))).success
    assert "FiestaBoard" not in clock.apps
    assert len(owned(clock)) == 1


def test_opt_out_retries_failed_final_order_save_after_apps_deleted(make_plugin, clock):
    plugin = make_plugin(native_rotation=True)
    assert sync(plugin, playlist(("one", 1), ("two", 2))).success
    deletes = []
    def fail_final_save(method, path, body, index):
        if method == "DELETE":
            deletes.append(path)
        if method == "PUT" and path == "/api/v1/apps/order" and deletes:
            clock.unsaved_order_once = True
            clock.on_request = None
    clock.on_request = fail_final_save
    assert not plugin.write(grid(3), native=None, cancel=CancelToken()).success
    assert not owned(clock)
    assert clock.saved_disabled != clock.disabled
    assert plugin.write(grid(3), native=None, cancel=CancelToken()).success
    assert clock.saved_disabled == clock.disabled
    assert clock.saved_order == clock.order


def test_native_show_on_change_off_never_forces_selection(make_plugin, clock):
    plugin = make_plugin(native_rotation=True, show_on_change=False)
    value = playlist(("one", 1), ("two", 2))
    assert sync(plugin, value).success
    assert clock.current_app == "Time" and clock.switches() == []
    recreated = make_plugin(native_rotation=True, show_on_change=False)
    assert sync(recreated, value).success
    clock.restart()
    assert sync(recreated, value).success
    assert clock.current_app == "Time" and clock.switches() == []


def test_uptime_restart_survives_temporary_inventory_failure(make_plugin, clock):
    plugin = make_plugin(native_rotation=True)
    assert sync(plugin, playlist(("one", 1))).success
    plugin.poll_device()
    clock.restart()
    clock.fail_paths = ("/api/v1/apps",)
    outcome = plugin.poll_device()
    assert outcome is not None and outcome.restarted
    assert not plugin._native.validated
    assert not plugin._native.payloads


def test_capacity_replacement_preserves_old_playlist_until_space_is_available(make_plugin, clock):
    clock.apps.update({f"foreign{i}": {"text": str(i)} for i in range(48)})
    plugin = make_plugin(native_rotation=True)
    assert sync(plugin, playlist(("one", 1), ("two", 2))).success
    apps, order = dict(clock.apps), list(clock.order)
    clock.clear()
    assert not sync(plugin, playlist(("three", 3), ("four", 4))).success
    assert clock.apps == apps and clock.order == order
    assert all(method == "GET" for method, _ in clock.calls())


def test_conflicting_duplicate_duration_rejects_without_mutating_existing_playlist(make_plugin, clock):
    plugin = make_plugin(native_rotation=True)
    assert sync(plugin, playlist(("one", 1))).success
    value = playlist(("one", 1), ("one", 1))
    value.entries[1].duration_ms = 10000
    apps, order = dict(clock.apps), list(clock.order)
    clock.clear()
    assert not sync(plugin, value).success
    assert clock.apps == apps and clock.order == order
    assert all(method == "GET" for method, _ in clock.calls())

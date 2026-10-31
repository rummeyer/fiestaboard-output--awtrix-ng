"""The manifest and the device data agree with what the plugin does."""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.plugins import led_spec_for_model

from .conftest import _ERRORS, COLS, MANIFEST, ROWS

ROOT = Path(__file__).resolve().parent.parent
_MANIFEST_JSON = json.loads((ROOT / "manifest.json").read_text())


def test_the_manifest_loads_as_an_output_plugin():
    assert _ERRORS == []
    assert MANIFEST.id == "ulanzi_tc002"
    assert MANIFEST.plugin_type == "output"


def test_the_capabilities_come_from_the_tc002_model():
    caps = MANIFEST.output.capabilities
    assert caps.technology == "led_matrix"
    assert caps.delivery == "push"
    assert caps.animation == "stream"
    assert caps.min_interval_ms == 1000
    assert caps.read_back.supported is False
    assert caps.native_transitions == frozenset()
    assert caps.device_models == ("ulanzi_tc002",)


def test_the_model_is_one_row_taller_than_the_panel_for_three_text_rows():
    model = MANIFEST.output.model(0)
    assert model["geometry"] == {"kind": "pixels", "width": 52, "height": 17}
    assert model["font"] == "3x5" and model["charset"] == "led_3x5"
    spec = led_spec_for_model(model)
    assert (spec.width, spec.height, spec.font) == (52, 17, "3x5")


def test_the_board_is_three_rows_of_thirteen():
    from src.outputs.geometry import grid_floor, model_cell_grid

    model = MANIFEST.output.model(0)
    assert model_cell_grid(model, None) == (ROWS, COLS)
    assert (ROWS, COLS) >= grid_floor(model)


def test_only_the_password_is_secret():
    props = MANIFEST.output.settings_schema["properties"]
    assert set(props) == {
        "host",
        "app_name",
        "show_on_change",
        "duration_s",
        "tile_gap",
        "block_padding",
        "fade_style",
        "username",
        "password",
        "native_rotation",
        "firmware_transition_override",
        "firmware_transition_effect",
        "firmware_transition_direction",
        "firmware_transition_duration_ms",
        "firmware_auto_rotation",
    }
    secret = {name for name, p in props.items() if p.get("secret") or p.get("ui:widget") == "password"}
    assert secret == {"password"}


def test_the_address_field_is_a_device_picker_bound_to_find_my_clock():
    host = MANIFEST.output.settings_schema["properties"]["host"]
    assert host["ui:widget"] == "device-picker"
    assert host["ui:options"] == {"action": "find_awtrix", "value_key": "host", "label_key": "label"}
    actions = [a.id for a in MANIFEST.output.actions]
    assert actions == ["find_awtrix", "test_connection"]


def test_the_app_name_pattern_is_the_clocks():
    app_name = MANIFEST.output.settings_schema["properties"]["app_name"]
    # The clock's names, or none: FiestaBoard's settings form leaves the default out, and an
    # empty field then means FiestaBoard rather than failing to save.
    assert app_name["pattern"] == "^([A-Za-z0-9_-]{1,32})?$"
    assert re.match(app_name["pattern"], "")
    assert app_name["default"] == "FiestaBoard"


def test_the_display_catalog_finds_it_by_device_and_firmware():
    display = _MANIFEST_JSON["display"]
    assert display["manufacturer"] == "Ulanzi"
    assert display["models"] == ["TC002"]
    assert "AWTRIX NG" in display["keywords"]


def test_the_data_package_has_the_manifests_version_and_only_the_device_data():
    package = json.loads((ROOT / "package.json").read_text())
    assert package["version"] == _MANIFEST_JSON["version"]
    assert package["files"] == ["output/*.json"]
    assert package["private"] is True

"""Optional flexible-frame support never raises the plugin's baseline core requirement."""

import json
from pathlib import Path

import plugins.ulanzi_tc002 as plugin_module
import pytest

from .conftest import MANIFEST, build


def test_manifest_keeps_the_api_one_load_contract():
    output = json.loads((Path(__file__).parents[1] / "manifest.json").read_text())[
        "output"
    ]
    assert output["output_api"] == 1
    assert "physical_frames" not in output


@pytest.mark.parametrize("missing", ["_PhysicalSurface", "_physical_frame_pixels"])
def test_partial_or_older_sdk_does_not_advertise_physical_support(monkeypatch, missing):
    monkeypatch.setattr(plugin_module, missing, None, raising=False)
    declared = plugin_module.AwtrixNG.declared_capabilities(MANIFEST.output)
    runtime = build({"host": "192.0.2.10"}).capabilities()
    assert not getattr(declared, "physical_frames", {})
    assert not getattr(runtime, "physical_frames", {})


def test_optional_physical_declaration_agrees_between_registry_and_runtime():
    declared = plugin_module.AwtrixNG.declared_capabilities(MANIFEST.output)
    runtime = build({"host": "192.0.2.10"}).capabilities()
    if getattr(plugin_module, "_PhysicalSurface", None) is None:
        assert not getattr(declared, "physical_frames", {})
    else:
        surface = declared.physical_frames["ulanzi_tc002"]
        assert (surface.width, surface.height, surface.modes) == (
            52,
            16,
            frozenset({"ordinary", "native"}),
        )
        assert runtime.physical_frames == declared.physical_frames


def test_registry_retains_the_optional_capability_without_a_new_manifest_major(
    monkeypatch,
):
    from src.outputs.plugin_registration import register_output_plugin
    from src.outputs.registry import OutputRegistry

    registry = OutputRegistry()
    monkeypatch.setattr(
        "src.outputs.plugin_registration.output_registry", lambda: registry
    )
    register_output_plugin(plugin_module.AwtrixNG, MANIFEST)
    definition = registry.get("ulanzi_tc002")
    assert definition.output_api == 1
    assert definition.capabilities == plugin_module.AwtrixNG.declared_capabilities(
        MANIFEST.output
    )

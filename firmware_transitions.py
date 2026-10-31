"""Explicit, device-wide AWTRIX transition overrides. Unset means hands off."""
from __future__ import annotations

from src.plugins import RequestCancelled


class FirmwareTransitions:
    def __init__(self, plugin):
        self.plugin = plugin
        self.confirmed = False

    def desired(self):
        config = self.plugin.config
        if config.get("firmware_transition_override") is not True:
            return {}
        result = {}
        effect = config.get("firmware_transition_effect")
        if effect not in (None, "", "device"):
            if not isinstance(effect, str):
                raise ValueError("Firmware transition effect must be a supported name")
            result["transitionEffect"] = effect
        direction = config.get("firmware_transition_direction")
        if direction not in (None, "", "device"):
            if direction not in ("normal", "reverse"):
                raise ValueError("Firmware transition direction must be normal or reverse")
            result["transitionDirection"] = direction
        duration = config.get("firmware_transition_duration_ms")
        if duration is not None and duration != "":
            if type(duration) is not int or not 0 <= duration <= 2147483647:
                raise ValueError("Firmware transition duration must be an integer from 0 to 2147483647 ms")
            result["transitionDurationMs"] = duration
        automatic = config.get("firmware_auto_rotation", "device")
        if automatic not in (None, "", "device"):
            if automatic not in ("on", "off"):
                raise ValueError("Automatic firmware rotation must use device settings, on or off")
            result["autoTransition"] = automatic == "on"
        return result

    def _call(self, method, cancel, body=None, path="/api/v1/settings"):
        if cancel is not None and cancel.cancelled:
            raise RequestCancelled("Firmware transition update cancelled")
        return self.plugin._request(method, path, json=body, setup=True, cancel=cancel)

    def inspect(self, cancel):
        """Validate configured overrides and read actual values without writing."""
        desired = self.desired()
        device = self._call("GET", cancel, path="/api/v1/device")
        capabilities = self._call("GET", cancel, path="/api/v1/capabilities")
        if not isinstance(device, dict) or device.get("boardType") != "tc002":
            raise ValueError("Firmware transition controls require an AWTRIX NG TC002")
        display = capabilities.get("display", {}) if isinstance(capabilities, dict) else {}
        if display.get("width") != 52 or display.get("height") != 16:
            raise ValueError("Firmware transition controls require a verified 52 x 16 TC002")
        if capabilities.get("layout") is not True:
            raise ValueError("Firmware transition controls require TC002 layout capability")
        if "transitionEffect" in desired:
            supported = capabilities.get("transitions", [])
            if desired["transitionEffect"] not in supported:
                raise ValueError(f"Firmware does not advertise transition effect {desired['transitionEffect']!r}")
        current = self._call("GET", cancel)
        if not isinstance(current, dict) or any(key not in current for key in desired):
            raise ValueError("Firmware did not report the selected transition settings")
        return desired, current

    def apply(self, cancel):
        if self.confirmed or not self.desired():
            return
        desired, _ = self.inspect(cancel)
        # A complete acknowledgement plus an independent readback is required.
        # Failed/uncertain requests leave confirmed false, so retries still PATCH
        # even if the device's RAM already reflects the requested values.
        response = self._call("PATCH", cancel, desired)
        current = self._call("GET", cancel)
        if not isinstance(response, dict) or not isinstance(current, dict) or any(
            response.get(key) != value or current.get(key) != value for key, value in desired.items()
        ):
            raise ValueError("Firmware transition readback does not match the requested settings")
        self.confirmed = True

"""Loopback TC002 AWTRIX NG 1.2.2 API fake: physical 52x16 layout payloads,
Time/Status builtins, native rotation inventory/order, and settings faults.
No real-device traffic.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

PUSHED = "/api/v1/apps/pushed/"
ACTIVE = "/api/v1/apps/active"
DEVICE = "/api/v1/device"
CAPABILITIES = "/api/v1/capabilities"
SETTINGS = "/api/v1/settings"

#: TC002 JSON request body limit (docs/reference/limits.md).
MAX_BODY = 2 * 1024 * 1024


class MockAwtrix:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, Any]] = []
        self.mode = "ok"
        self.fail_paths: tuple[str, ...] = ()
        self.board_type = "tc002"
        self.panel_width = 52
        self.panels = 1
        self.layout_supported = True
        #: ``None`` (normal), ``"no_display"`` (a capabilities answer without
        #: ``display``) or ``"not_json"`` (200, not JSON), ``"unauthorized"`` (401) or ``"hang"``
        #: (answers only after :meth:`stop`).
        self.capabilities_fault: str | None = None
        self.password: str | None = None
        self.on_request: Callable[[str, str, Any, int], None] | None = None
        #: Pushed apps by name (as stored, case kept) -> last body.
        self.apps: dict[str, Any] = {}
        self.builtins = ["Time", "Status"]
        self.order: list[str] = []
        self.disabled: list[str] = []
        self.modules: list[str] = []
        self.unsaved_order_once = False
        self.saved_order: list[str] = []
        self.saved_disabled: list[str] = []
        self.transitions = ["Random", "Slide", "Fade", "Rain"]
        self.settings = {"autoTransition": True, "transitionEffect": "Rain", "transitionDirection": "normal", "transitionDurationMs": 1000}
        self.ignore_settings_patch = False
        self.current_app = "Time"
        self._booted = time.monotonic()
        self.uptime_offset = 1000.0
        self._release = threading.Event()
        self._lock = threading.Lock()
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # quiet
                pass

            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw) if raw else None
                except ValueError:
                    body = {"_unparsed": raw.decode("latin-1")}
                status, answer = mock._answer(
                    self.command, self.path, body, self.headers.get("Authorization"), size=length
                )
                if status is None:
                    mock._release.wait(30)
                    return
                payload = answer if isinstance(answer, bytes) else json.dumps(answer).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_PUT = do_POST = do_DELETE = do_PATCH = _handle  # noqa: N815 - http.server API

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    # --- lifecycle -----------------------------------------------------------------------

    def start(self) -> MockAwtrix:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._release.set()
        self.server.shutdown()
        self.server.server_close()

    @property
    def host(self) -> str:
        host, port = self.server.server_address[:2]
        return f"{host}:{port}"

    def restart(self) -> None:
        """A power cycle: pushed apps gone, uptime from zero, the clock's own app current."""
        with self._lock:
            self.apps = {}
            self.current_app = "Time"
            self._booted = time.monotonic()
            self.uptime_offset = 0.0

    # --- what the clock saw ----------------------------------------------------------------

    def uptime(self) -> int:
        return int(self.uptime_offset + time.monotonic() - self._booted)

    def calls(self) -> list[tuple[str, str]]:
        return [(method, path) for method, path, _ in self.requests]

    def pushes(self) -> list[Any]:
        return [body for method, path, body in self.requests if method == "PUT" and path.startswith(PUSHED)]

    def switches(self) -> list[Any]:
        return [body for method, path, body in self.requests if method == "PUT" and path == ACTIVE]

    @staticmethod
    def bitmap(body: Any) -> bytes:
        """The RGB888 one push draws."""
        command = body["layout"]["regions"][0]["draw"][0]
        assert command[0] == "bitmap"
        return base64.b64decode(command[5])

    def clear(self) -> None:
        with self._lock:
            self.requests = []

    # --- behaviour -------------------------------------------------------------------------

    def _answer(
        self, method: str, path: str, body: Any, authorization: str | None, size: int = 0
    ) -> tuple[int | None, Any]:
        with self._lock:
            index = len(self.requests)
            self.requests.append((method, path, body))
        if self.on_request is not None:
            self.on_request(method, path, body, index)
        if self.mode == "hang":
            return None, None
        if self.password is not None:
            expected = "Basic " + base64.b64encode(f"admin:{self.password}".encode()).decode()
            if authorization != expected:
                return 401, {"error": {"code": "unauthorized", "message": "authentication required"}}
        if self.mode == "http_500" or any(path.startswith(p) for p in self.fail_paths):
            return 500, {"error": {"code": "internalError", "message": "internal"}}
        if self.mode == "garbage":
            return 200, b"<html>not a clock</html>"
        if self.mode == "other_json":
            return 200, {"status": "some other device"}
        if method == "GET" and path == DEVICE:
            return 200, {
                "version": "1.2.2",
                "uid": "a4cf12ab34cd",
                "boardType": self.board_type,
                "hostname": "awtrixng-ab34cd",
                "uptimeSeconds": self.uptime(),
                "currentApp": self.current_app,
            }
        if size > MAX_BODY:
            return 413, {"error": {"code": "payloadTooLarge", "message": "JSON body over 2 MiB"}}
        if method == "GET" and path == CAPABILITIES:
            if self.capabilities_fault == "unauthorized":
                return 401, {"error": {"code": "unauthorized", "message": "authentication required"}}
            if self.capabilities_fault == "hang":
                self._release.wait(30)
                return 200, {"display": {"width": 32, "height": 8}}
            if self.capabilities_fault == "no_display":
                return 200, {"features": {}}
            if self.capabilities_fault == "not_json":
                return 200, b"<html>no</html>"
            return 200, {"display": {"width": self.panel_width * self.panels, "height": 16}, "layout": self.layout_supported, "layouts": {"version": 1}, "transitions": self.transitions}
        if method == "GET" and path == SETTINGS:
            return 200, {"panelWidth": self.panel_width, "panels": self.panels, **self.settings}
        if method == "PATCH" and path == SETTINGS:
            if not self.ignore_settings_patch:
                self.settings.update(body)
            return 200, dict(self.settings)
        if method == "GET" and path == "/api/v1/apps":
            names = self.order + [n for n in self.builtins + list(self.apps) if n not in self.order]
            names += [n for n in self.disabled if n not in names]
            return 200, [
                {"name": name, "enabled": name not in self.disabled,
                 "inLoop": name not in self.disabled and (name in self.builtins or name in self.apps),
                 "present": name in self.builtins or name in self.apps,
                 "slot": i if i < len(self.order) else None,
                 "origin": "builtin" if name in self.builtins else "pushed" if name in self.apps else None}
                for i, name in enumerate(names)
            ] + [{"name": name, "origin": "module", "present": True} for name in self.modules]
        if method == "PUT" and path == "/api/v1/apps/order":
            if not isinstance(body, dict) or not isinstance(body.get("disabled"), list):
                return 422, {"error": {"message": "disabled is required"}}
            self.disabled = list(body["disabled"])
            self.order = list(body.get("order", self.order))
            if self.unsaved_order_once:
                self.unsaved_order_once = False
                return 507, {"error": {"message": "active but not saved"}}
            self.saved_order, self.saved_disabled = list(self.order), list(self.disabled)
            return 200, {"ok": True}
        if method == "DELETE" and path.startswith("/api/v1/apps/"):
            self.apps.pop(path.removeprefix("/api/v1/apps/"), None)
            return 200, {"ok": True}
        if method == "PUT" and path.startswith(PUSHED):
            if not isinstance(body, dict) or not body:
                return 422, {"error": {"code": "validationFailed", "message": "a JSON body is required"}}
            with self._lock:
                if path[len(PUSHED):] not in self.apps and len(self.apps) >= 50:
                    return 507, {"error": {"message": "insufficientStorage"}}
                self.apps[path[len(PUSHED) :]] = body
            return 200, {"ok": True}
        if method == "PUT" and path == ACTIVE:
            name = (body or {}).get("name", "")
            match = next((app for app in self.apps if app.lower() == str(name).lower()), None)
            if match is None or match in self.disabled:
                return 404, {"error": {"code": "notFound", "message": "app not found"}}
            self.current_app = match
            return 200, {"ok": True}
        return 404, {"error": {"code": "notFound", "message": "unknown route"}}

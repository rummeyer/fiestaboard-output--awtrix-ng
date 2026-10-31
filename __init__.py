"""AWTRIX NG output plugin for FiestaBoard: a Ulanzi TC002 as a board.

Drives one TC002 running AWTRIX NG over its local HTTP API. The board is a
**pushed app** in the clock's rotation (``PUT /api/v1/apps/pushed/{name}``),
so the clock's own apps and scripts keep running beside it. Core owns the
policy — the send floor (``min_interval_ms``, 1 s between writes), dedupe,
preemption, the write budget and which LED transition a board runs — and this
plugin moves pixels.

What the AWTRIX NG documentation (1.2.2) settles:

- **The panel is 52 x 16.** A pushed app without a layout is drawn at double
  size on a 26 x 8 grid while ``enlargeApps`` is on (the default); a
  **layout** (TC002 only) keeps all 52 x 16 pixels whatever that setting
  says. Each frame is therefore one layout with one region the size of the
  panel, drawing one ``bitmap`` of base64 RGB888.
- **Three rows need 17 pixels.** In core's 3x5 face (1-pixel gaps) a
  16-pixel panel fits two text rows, and core refuses an LED board below
  3 x 10. The device model is 52 x 17 (3 rows x 13 columns) and
  :meth:`AwtrixNG.render` drops :data:`GAP_ROW`, the gap between the second
  and the third text row, so the clock gets exactly 52 x 16. A page that
  leaves the third row dark is two rows, and those are centred instead.
- **A replacement shows at once** while the app is on screen, so every LED
  transition core plans is streamed as paced single frames at up to
  :data:`STREAM_MAX_FPS` (a conservative rate, not yet measured on a clock).
  A transition is only worth streaming while the board is on screen:
  ``show_on_change`` (default on) switches the clock to the board first
  (``PUT /api/v1/apps/active``, ``fast``), and with it off the plugin asks
  ``GET /api/v1/device`` whether the board is the current app and otherwise
  sends only the new page.
- **Pushed apps live in RAM** and vanish when the clock restarts.
  :meth:`AwtrixNG.poll_device` watches ``uptimeSeconds`` and reports a restart,
  so core sends the page again.

All device traffic goes through ``self.http`` (core's helper: the
``FIESTABOARD_OUTPUTS_ALLOW_HOSTS`` fence, no redirects, the run's cancel
token) with ``(connect, read)`` timeouts; everything but the frame push itself
is marked ``setup=True``. Device failures come back as a failed
:class:`WriteResult`, never as an exception.
"""

from __future__ import annotations

import base64
import inspect
import ipaddress
import logging
import os
import re
import socket
import time
from collections.abc import Sequence
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import replace
from functools import lru_cache
from typing import Any

import requests

from .native_rotation import NativeRotation
from .firmware_transitions import FirmwareTransitions

from src.plugins import (
    ActionOutcome,
    BoardToken,
    CancelToken,
    CellFrame,
    ConnectionCheck,
    LedLayoutOptions,
    OutputHostBlocked,
    OutputHttp,
    OutputPluginBase,
    RequestCancelled,
    ResolvedLedTransition,
    RichCellFrame,
    TimedFrame,
    WriteResult,
    cells_from_codes,
    layout_message,
    led_spec_for_model,
    local_ipv4,
    plan_transition,
    rasterize,
    transition_frames,
)

try:
    from src.plugins import DevicePoll
except ImportError:  # a core before the poll_device hook never polls: nothing to report
    DevicePoll = None  # type: ignore[assignment,misc]

try:
    from src.plugins import NativePageSelection
except ImportError:
    NativePageSelection = None

# New flexible-frame SDK is optional: API-1 cores keep their existing paths.
try:
    from src.plugins import PhysicalSurface as _PhysicalSurface
    from src.plugins import physical_frame_pixels as _physical_frame_pixels
except ImportError:
    _PhysicalSurface = None
    _physical_frame_pixels = None


def _physical_pixels(frame: Any) -> bytes | None:
    if _PhysicalSurface is None or _physical_frame_pixels is None:
        return None
    return _physical_frame_pixels(frame, PANEL_WIDTH, PANEL_HEIGHT)


def _with_physical_frames(capabilities: Any) -> Any:
    if (
        _PhysicalSurface is None
        or _physical_frame_pixels is None
        or getattr(capabilities, "physical_frames", None) is None
    ):
        return capabilities
    return replace(
        capabilities,
        physical_frames={
            **capabilities.physical_frames,
            "ulanzi_tc002": _PhysicalSurface(PANEL_WIDTH, PANEL_HEIGHT, frozenset({"ordinary", "native"})),
        },
    )


logger = logging.getLogger(__name__)

__all__ = ["AwtrixError", "AwtrixNG", "candidate_subnets", "is_awtrix_reply", "sweep"]

# --- device facts ---------------------------------------------------------------------------

#: The TC002's matrix (``GET /api/v1/capabilities``: ``display`` 52 x 16).
PANEL_WIDTH = 52
PANEL_HEIGHT = 16

#: The pixel row :meth:`AwtrixNG.render` drops from the model's 52 x 17: in the
#: 3x5 face the text rows sit at y 0-4, 6-10 and 12-16, so 11 is the gap
#: between the second and the third. The first row (often a title) keeps its
#: gap below it.
GAP_ROW = 11

#: The fastest a transition is streamed. Not measured on a clock: one push is
#: one HTTP request of about 3.4 KB, and five a second is what the Pixoo plugin
#: measured as safe for the same kind of stream.
STREAM_MAX_FPS = 5

#: The shortest a continuous transition (slide, wipe, cascade, dissolve,
#: blend) runs when nothing asked for a duration: core's default (480 ms) is
#: three frames at the stream rate, which reads as a jump.
STREAM_TRANSITION_MS = 1000

#: The most frames one explicit sequence streams (compressed evenly beyond it).
SEQUENCE_MAX_FRAMES = 40

#: Three pixel steps to black and three back to the target.
FADE_STEPS = 3

#: ``(connect, read)`` timeouts for one request (the clock's own HTTP client
#: uses the same).
CONNECT_TIMEOUT_S = 3.0
READ_TIMEOUT_S = 5.0

#: How far ``uptimeSeconds`` may lag the time since it was last read before
#: :meth:`AwtrixNG.poll_device` takes it for a restart (the clock reports
#: whole seconds; a request may sit in a queue).
UPTIME_SLACK_S = 30.0

#: The app name a board shows up as when none is set.
DEFAULT_APP_NAME = "FiestaBoard"
_APP_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

#: The region id of the one region a frame's layout has.
REGION_ID = "fiestaboard"

DEVICE_PATH = "/api/v1/device"
PUSHED_PATH = "/api/v1/apps/pushed/"
ACTIVE_PATH = "/api/v1/apps/active"

#: ``boardType`` of a TC002, and of an ESP32 AWTRIX NG (32 x 8: no layouts).
TC002 = "tc002"
_BOARD_TYPES = frozenset({TC002, "awtrixng"})


class AwtrixError(OSError):
    """The clock answered, but not with success."""


class AwtrixStatusError(AwtrixError):
    """The clock answered with an HTTP status other than 2xx."""

    def __init__(self, status: int, detail: str = "") -> None:
        super().__init__(f"HTTP {status}" + (f": {detail}" if detail else ""))
        self.status = status


class AwtrixBadResponse(AwtrixError):
    """A 2xx whose body is not what AWTRIX NG answers."""


class _NoHost(requests.exceptions.InvalidURL):
    """No clock address is configured."""


_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)


def normalise_host(raw: Any) -> str:
    """``host`` or ``host:port``, lowercased, with any scheme or path dropped."""
    text = _SCHEME.sub("", str(raw or "").strip())
    return text.split("/", 1)[0].strip().lower()


def _error_detail(response: requests.Response) -> str:
    """The message of AWTRIX NG's error shape (``{"error": {"code", "message", "field"}}``)."""
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        return ""
    if not isinstance(error, dict):
        return ""
    parts = [str(error.get(k)) for k in ("code", "message") if error.get(k)]
    if error.get("field"):
        parts.append(f"field {error['field']}")
    return ", ".join(parts)


# --- finding a clock -------------------------------------------------------------------------

#: Port, per-host timeout, total budget and concurrency of a LAN sweep.
DISCOVERY_PORT = 80
DISCOVERY_PER_HOST_S = 0.8
DISCOVERY_TOTAL_S = 10.0
DISCOVERY_CONCURRENCY = 64
#: The largest network a sweep accepts (a /22 is 1022 hosts).
DISCOVERY_MAX_PREFIX = 22
_ALLOW_ENV = "FIESTABOARD_OUTPUTS_ALLOW_HOSTS"
#: Where Docker allocates its bridge networks (and nothing a clock joins).
_DOCKER_BRIDGES = ipaddress.IPv4Network("172.16.0.0/12")


def is_awtrix_reply(body: Any) -> bool:
    """Whether *body* is an AWTRIX NG clock's ``GET /api/v1/device`` answer."""
    return isinstance(body, dict) and body.get("boardType") in _BOARD_TYPES and "uid" in body


def _resolve_ipv4(name: str) -> str | None:
    try:
        return socket.gethostbyname(name)
    except (OSError, UnicodeError):
        return None


def _lan_address(value: str | None) -> ipaddress.IPv4Address | None:
    """*value* (an address or hostname) as a private, non-loopback LAN IPv4."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        resolved = _resolve_ipv4(text)
        if resolved is None:
            return None
        address = ipaddress.ip_address(resolved)
    if not isinstance(address, ipaddress.IPv4Address):
        return None
    if not address.is_private or address.is_loopback or address.is_link_local:
        return None
    return address


def candidate_subnets(
    *, subnet: str | None = None, hint_host: str | None = None, local_ip: str | None = None
) -> list[ipaddress.IPv4Network]:
    """The networks a sweep searches, in order: the one asked for, the /24 of
    the browser's address (*hint_host*: in Docker bridge mode FiestaBoard's
    own address is a container network), then FiestaBoard's own /24.

    FiestaBoard's own address is skipped when it is in 172.16.0.0/12 (where
    Docker puts its bridge networks) and the user already named a network.

    Raises:
        ValueError: *subnet* is malformed, not private, or larger than a /22.
    """
    found: list[ipaddress.IPv4Network] = []
    if subnet:
        network = ipaddress.IPv4Network(subnet.strip(), strict=False)
        if not network.is_private:
            raise ValueError(f"{network} is not a private network")
        if network.prefixlen < DISCOVERY_MAX_PREFIX:
            raise ValueError(f"{network} is too large to search; use a /{DISCOVERY_MAX_PREFIX} or smaller")
        found.append(network)
    for value in (hint_host, local_ip):
        address = _lan_address(value)
        if address is None:
            continue
        if value is local_ip and found and address in _DOCKER_BRIDGES:
            continue
        network = ipaddress.IPv4Network(f"{address}/24", strict=False)
        if not any(network.subnet_of(n) for n in found):
            found.append(network)
    return found


def _device(ip: str, port: int, body: dict[str, Any]) -> dict[str, Any]:
    name = str(body.get("hostname") or "AWTRIX NG")
    kind = "TC002" if body.get("boardType") == TC002 else "AWTRIX NG, not a TC002"
    return {
        "ip": ip,
        "port": port,
        "host": ip if port == 80 else f"{ip}:{port}",
        "label": f"{name} at {ip} ({kind})",
        "hostname": name,
    }


def _probe(http: OutputHttp, ip: str, port: int, per_host_s: float) -> dict[str, Any] | None:
    try:
        response = http.get(f"http://{ip}:{port}{DEVICE_PATH}", timeout=(per_host_s, per_host_s))
        body = response.json() if response.status_code == 200 else None
    except Exception:  # unreachable, fenced, not JSON: not a clock
        return None
    return body if is_awtrix_reply(body) else None


def sweep(
    http: OutputHttp,
    targets: list[tuple[str, int]],
    *,
    per_host_s: float = DISCOVERY_PER_HOST_S,
    total_s: float = DISCOVERY_TOTAL_S,
    concurrency: int = DISCOVERY_CONCURRENCY,
) -> list[dict[str, Any]]:
    """Ask every ``(ip, port)`` in *targets* for ``GET /api/v1/device``, at most
    *concurrency* at once; the clocks that answered by *total_s*, in target order.

    Goes through *http*, so ``FIESTABOARD_OUTPUTS_ALLOW_HOSTS`` applies. A clock
    with a password set answers 401 and is not found: type its address.
    """
    deadline = time.monotonic() + total_s
    pool = ThreadPoolExecutor(max_workers=max(1, concurrency), thread_name_prefix="awtrix-sweep")
    futures = {pool.submit(_probe, http, ip, port, per_host_s): i for i, (ip, port) in enumerate(targets)}
    hits: dict[int, dict[str, Any]] = {}
    pending = set(futures)
    while pending:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        done, pending = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
        for future in done:
            body = future.result()
            if body is not None:
                index = futures[future]
                hits[index] = _device(*targets[index], body)
    # Probes still on the wire end within their own timeout; nobody waits.
    pool.shutdown(wait=False, cancel_futures=True)
    return [hits[i] for i in sorted(hits)]


def _targets(networks: list[ipaddress.IPv4Network], port: int) -> list[tuple[str, int]]:
    seen: set[str] = set()
    out = []
    for network in networks:
        for address in network.hosts():
            ip = str(address)
            if ip not in seen:
                seen.add(ip)
                out.append((ip, port))
    return out


def _fence_guidance() -> list[str]:
    if os.environ.get(_ALLOW_ENV, "").strip():
        return [
            f"{_ALLOW_ENV} is set, so only the hosts it lists are contacted. "
            "Development setups set it; unset it (or set it empty) to search your network."
        ]
    return []


# --- rendering helpers -------------------------------------------------------------------------


def _token(cell: Any) -> BoardToken:
    """One cell as a board token: a rich cell as is, a 0-71 code projected."""
    return cell if isinstance(cell, BoardToken) else cells_from_codes([[int(cell)]])[0][0]


@lru_cache(maxsize=4)
def _takes_layers(layout: Any) -> bool:
    """Whether *layout* (core's ``layout_message``) draws bitmap layers:
    FiestaBoard before pixel canvases has no ``layers`` keyword."""
    return "layers" in inspect.signature(layout).parameters


def squeeze(pixels: bytes, width: int, height: int) -> bytes:
    """A *width* x *height* RGB888 frame as the panel's 52 x 16: :data:`GAP_ROW`
    dropped from the model's 52 x 17, anything else cut or padded black.

    A frame whose third text row is dark, gap and all, is two rows of text:
    they keep the gap between them and move to the middle of the panel,
    rather than leaving its bottom third empty.
    """
    stride = width * 3
    rows = [pixels[y * stride : (y + 1) * stride] for y in range(height)]
    if height > PANEL_HEIGHT and not any(any(row) for row in rows[GAP_ROW:]):
        rows = [b""] * ((PANEL_HEIGHT - GAP_ROW) // 2) + rows[:GAP_ROW]
    elif height > PANEL_HEIGHT:
        del rows[GAP_ROW]
    out = bytearray()
    for y in range(PANEL_HEIGHT):
        row = rows[y] if y < len(rows) else b""
        out += row[: PANEL_WIDTH * 3].ljust(PANEL_WIDTH * 3, b"\0")
    return bytes(out)


class AwtrixNG(OutputPluginBase):
    """One Ulanzi TC002 running AWTRIX NG on the local network."""

    plugin_id = "ulanzi_tc002"

    # Device pacing, as instance-overridable attributes (tests shorten them).
    #: Seconds between streamed frames; ``None`` = one frame at the model's
    #: rate (capped at :data:`STREAM_MAX_FPS`). Tests set 0.
    STREAM_STEP_S: float | None = None
    CONNECT_TIMEOUT_S = CONNECT_TIMEOUT_S
    READ_TIMEOUT_S = READ_TIMEOUT_S

    def __init__(self, board_id: str | None, config: dict[str, Any]) -> None:
        super().__init__(board_id, config)
        self.host = normalise_host(self.config.get("host"))
        name = str(self.config.get("app_name") or DEFAULT_APP_NAME).strip()
        self.app_name = name if _APP_NAME.match(name) else DEFAULT_APP_NAME
        self.show_on_change = self.config.get("show_on_change", True) is not False
        duration = self.config.get("duration_s")
        self._duration_ms = int(duration) * 1000 if isinstance(duration, int) and duration > 0 else None
        user, password = self.config.get("username"), self.config.get("password")
        self._auth = (str(user or ""), str(password or "")) if user or password else None
        self._renderer: tuple[Any, LedLayoutOptions] | None = None
        #: An upload failed and nothing has landed since: the next poll that
        #: hears the clock report it back (see :meth:`poll_device`).
        self._upload_failed = False
        #: The clock's ``uptimeSeconds`` last seen, and ``time.monotonic()`` then.
        self._uptime: tuple[float, float] | None = None
        self._native = NativeRotation(self)
        self._firmware = FirmwareTransitions(self)

    @classmethod
    def declared_capabilities(cls, manifest: Any) -> Any:
        # Existing capability hooks let a newer SDK opt in without making the
        # on-disk API-1 manifest unreadable by released cores.
        return _with_physical_frames(super().declared_capabilities(manifest))

    def capabilities(self) -> Any:
        return _with_physical_frames(super().capabilities())

    # --- identity --------------------------------------------------------------------------

    def device_key(self) -> str:
        return self.host or super().device_key()

    def _url(self, path: str) -> str:
        return f"http://{self.host}{path}"

    # --- rendering ---------------------------------------------------------------------------

    def _render_setup(self) -> tuple[Any, LedLayoutOptions]:
        """The board's matrix spec and layout options, from what core resolved:
        its character set and its Tile style / Block padding settings."""
        if self._renderer is None:
            self._renderer = (led_spec_for_model(self.device_model), self.led_layout_options())
        return self._renderer

    def layout(self, frame: Sequence[Sequence[Any]]) -> Any:
        """*frame* (0-71 codes or rich cells) laid out on the model's 52 x 17,
        with the pixel canvases it carries (``frame.layers``) drawn over it."""
        spec, options = self._render_setup()
        tokens = [[_token(cell) for cell in row] for row in frame]
        layers = tuple(getattr(frame, "layers", None) or ())
        if layers and _takes_layers(layout_message):
            return layout_message(tokens, spec, options, layers=layers)
        return layout_message(tokens, spec, options)

    def render(self, frame: Sequence[Sequence[Any]]) -> bytes:
        """*frame* as the panel's 52 x 16 RGB888, row-major from the top left."""
        physical = _physical_pixels(frame)
        if physical is not None:
            return physical
        return self._panel(rasterize(self.layout(frame)))

    @staticmethod
    def _panel(led_frame: Any) -> bytes:
        return squeeze(led_frame.pixels, led_frame.width, led_frame.height)

    # --- the device seam ----------------------------------------------------------------------

    def _request(
        self, method: str, path: str, *, json: Any = None, setup: bool = False, cancel: Any = None
    ) -> Any:
        """One request through ``self.http``; the clock's JSON answer (``None`` for none).

        Raises:
            requests.RequestException: no answer, a fenced host
                (``OutputHostBlocked``), or a cancelled run (``RequestCancelled``).
            AwtrixStatusError / AwtrixBadResponse: an answer that is not success.
        """
        if not self.host:
            raise _NoHost("No clock address configured")
        extra: dict[str, Any] = {}
        if cancel is not None:
            extra["cancel"] = cancel
        if json is not None:
            extra["json"] = json
        if self._auth is not None:
            extra["auth"] = self._auth
        response = self.http.request(
            method, self._url(path), setup=setup, timeout=(self.CONNECT_TIMEOUT_S, self.READ_TIMEOUT_S), **extra
        )
        if not 200 <= response.status_code < 300:
            raise AwtrixStatusError(response.status_code, _error_detail(response))
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise AwtrixBadResponse("the answer is not JSON") from exc

    def _device_state(self) -> dict[str, Any]:
        """``GET /api/v1/device``, remembering the clock's uptime."""
        body = self._request("GET", DEVICE_PATH, setup=True)
        if not isinstance(body, dict):
            raise AwtrixBadResponse("the device state is not a JSON object")
        uptime = body.get("uptimeSeconds")
        if isinstance(uptime, (int, float)) and not isinstance(uptime, bool):
            self._uptime = (float(uptime), time.monotonic())
        return body

    def _payload(self, pixels: bytes) -> dict[str, Any]:
        """One frame as a pushed app: a native-size layout drawing one bitmap."""
        data = base64.b64encode(pixels).decode("ascii")
        payload: dict[str, Any] = {
            "layout": {
                "version": 1,
                "regions": [
                    {
                        "id": REGION_ID,
                        "box": [0, 0, PANEL_WIDTH, PANEL_HEIGHT],
                        "draw": [["bitmap", 0, 0, PANEL_WIDTH, PANEL_HEIGHT, data]],
                    }
                ],
            }
        }
        if self._duration_ms is not None:
            payload["durationMs"] = self._duration_ms
        return payload

    # --- uploads -------------------------------------------------------------------------------

    def _wait(self, seconds: float, cancel: CancelToken) -> bool:
        """Pause; True when the run was cancelled meanwhile."""
        return cancel.wait(seconds)

    def _deliver(self, pixels: bytes, cancel: CancelToken) -> WriteResult | None:
        """One frame pushed, as a write verdict; ``None`` when it landed."""
        if cancel.cancelled:
            return WriteResult(True, False)
        if self.config.get("native_rotation") is True and NativePageSelection is None:
            logger.warning("Upgrade FiestaBoard to a core with native collection rotation, or turn the option off")
            return WriteResult(False, False)
        try:
            self._firmware.apply(cancel)
            self._native.prepare_legacy(cancel)
            if cancel.cancelled:
                return WriteResult(True, False, superseded=True)
            self._request("PUT", PUSHED_PATH + self.app_name, json=self._payload(pixels))
            self._native.legacy_written = True
            if not self._native.legacy_checked:
                self._native.retire(cancel)
        except RequestCancelled:
            # Core's cancel scope refused the request: preempted, not failed.
            return WriteResult(True, False, superseded=True)
        except Exception as exc:
            logger.warning("AWTRIX %s: push failed: %s", self.host or "(no address)", exc)
            self._upload_failed = True
            return WriteResult(False, False)
        self._upload_failed = False
        return None

    def _switch_to_board(self, cancel: CancelToken) -> bool:
        """Make the board the clock's current app at once. A failure (the app
        is not there yet, a disabled app) is logged; it never fails the write."""
        if cancel.cancelled:
            return False
        try:
            self._request("PUT", ACTIVE_PATH, json={"name": self.app_name, "fast": True}, setup=True)
        except Exception as exc:
            logger.info("AWTRIX %s: switching to %s failed: %s", self.host, self.app_name, exc)
            return False
        return True

    def _on_screen(self, cancel: CancelToken) -> bool:
        """Whether the board is the clock's current app (unknown counts as no)."""
        if cancel.cancelled:
            return False
        try:
            current = self._device_state().get("currentApp")
        except Exception as exc:
            logger.info("AWTRIX %s: reading the current app failed: %s", self.host, exc)
            return False
        return isinstance(current, str) and current.lower() == self.app_name.lower()

    def _play(self, frames: list[bytes], holds_s: list[float], cancel: CancelToken) -> WriteResult:
        """Show *frames* where they can be seen, the last one to stay.

        With ``show_on_change`` the clock is switched to the board first, which
        still shows the page before; with it off the frames are streamed only
        while the board is the current app. Otherwise only the last frame is
        pushed (and, with ``show_on_change``, switched to once it exists).
        """
        if not frames:
            return WriteResult(True, False)
        visible = self._switch_to_board(cancel) if self.show_on_change else self._on_screen(cancel)
        if not visible:
            frames, holds_s = frames[-1:], holds_s[-1:]
        result = self._stream(frames, holds_s, cancel)
        if result.was_sent and self.show_on_change and not visible:
            self._switch_to_board(cancel)
        return result

    def _stream(self, frames: list[bytes], holds_s: list[float], cancel: CancelToken) -> WriteResult:
        """Push *frames* one at a time, frame *i* held ``holds_s[i]`` seconds
        (never less than one stream step); the last one stays. A cancel between
        frames stops at once: a newer page is on its way."""
        last = len(frames) - 1
        step = self._step_s()
        for index, pixels in enumerate(frames):
            started = time.monotonic()
            failed = self._deliver(pixels, cancel)
            if failed is not None:
                return failed
            if index < last:
                hold = max(holds_s[index], step) - (time.monotonic() - started)
                if hold > 0 and self._wait(hold, cancel):
                    return WriteResult(True, False)
        return WriteResult(True, True)

    def _still(self, pixels: bytes, cancel: CancelToken) -> WriteResult:
        failed = self._deliver(pixels, cancel)
        if failed is not None:
            return failed
        if self.show_on_change:
            self._switch_to_board(cancel)
        return WriteResult(True, True)

    def _fade_through_black(self, before: bytes, target: bytes, cancel: CancelToken) -> WriteResult:
        """Stream a dimmed old image, black, then the brightening target, as
        pixels (never the clock's saved brightness)."""

        def dim(pixels: bytes, numerator: int) -> bytes:
            return bytes(value * numerator // FADE_STEPS for value in pixels)

        frames = [dim(before, level) for level in range(FADE_STEPS - 1, -1, -1)]
        frames.extend(dim(target, level) for level in range(1, FADE_STEPS + 1))
        return self._play(frames, [0.0] * len(frames), cancel)

    def _step_s(self) -> float:
        """The shortest gap between streamed frames."""
        return 1.0 / self._stream_fps() if self.STREAM_STEP_S is None else self.STREAM_STEP_S

    def _stream_fps(self) -> float:
        """Frames a second to plan a transition at: the model's rate, capped."""
        animation = (self.device_model or {}).get("animation") if isinstance(self.device_model, dict) else None
        rate = animation.get("maxFps") if isinstance(animation, dict) else None
        return float(min(rate, STREAM_MAX_FPS)) if isinstance(rate, (int, float)) and rate > 0 else STREAM_MAX_FPS

    # --- writes --------------------------------------------------------------------------------

    def sync_native_playlist(self, playlist: Any, *, cancel: CancelToken) -> WriteResult:
        """Synchronize owned native apps; ordinary refreshes never select an app."""
        if cancel.cancelled:
            return WriteResult(True, False, superseded=True)
        if self.config.get("native_rotation") is not True:
            return WriteResult(False, False)
        try:
            sent = self._native.sync(playlist, cancel)
        except RequestCancelled:
            return WriteResult(True, False, superseded=True)
        except Exception as exc:
            logger.warning("AWTRIX native rotation failed: %s", exc)
            self._upload_failed = True
            return WriteResult(False, False)
        self._upload_failed = False
        return WriteResult(True, sent)


    def write(self, frame: CellFrame, *, native: Any, cancel: CancelToken) -> WriteResult:
        return self._still(self.render(frame), cancel)

    def write_cells(self, cells: RichCellFrame, *, native: Any, cancel: CancelToken) -> WriteResult:
        return self._still(self.render(cells), cancel)

    def write_transition(
        self,
        before: RichCellFrame,
        after: RichCellFrame,
        transition: ResolvedLedTransition,
        *,
        cancel: CancelToken,
    ) -> WriteResult:
        """Show *after* through the transition core resolved for this board.

        ``none`` is one still push. A fade goes through black unless the
        board's ``fade_style`` is ``blend``. Every other kind is planned with
        core's renderer at the stream rate and played as paced stills.
        """
        # Physical frames already own their geometry. Preserve their final bytes;
        # the legacy cell transition planner cannot mix these two coordinate spaces.
        if _physical_pixels(before) is not None or _physical_pixels(after) is not None:
            return self._still(self.render(after), cancel)
        if transition.spec == "none":
            return self._still(self.render(after), cancel)
        spec = transition.spec
        if getattr(spec, "kind", None) == "fade" and self.config.get("fade_style", "black") != "blend":
            return self._fade_through_black(self.render(before), self.render(after), cancel)
        if spec.kind != "flip" and spec.duration_ms is None:
            spec = replace(spec, duration_ms=STREAM_TRANSITION_MS)
        planned = plan_transition(self.layout(before), self.layout(after), spec)
        # Frame 0 is *before*, which the board already shows.
        frames = [self._panel(f) for f in transition_frames(planned, fps=self._stream_fps())][1:]
        target = self._panel(planned.to_frame)
        if not frames or frames[-1] != target:
            frames.append(target)
        return self._play(frames, [0.0] * len(frames), cancel)

    def write_sequence(self, frames: list[TimedFrame], *, cancel: CancelToken) -> WriteResult:
        """*frames* as paced stills, each held its own duration (never shorter
        than one stream step), at most :data:`SEQUENCE_MAX_FRAMES`
        (compressed evenly, first and last kept); the last one stays."""
        if not frames:
            return WriteResult(True, False)
        if len(frames) > SEQUENCE_MAX_FRAMES:
            last = len(frames) - 1
            picks = sorted({round(i * last / (SEQUENCE_MAX_FRAMES - 1)) for i in range(SEQUENCE_MAX_FRAMES)})
            frames = [frames[i] for i in picks]
        pixels = [self.render(f.frame) for f in frames]
        return self._play(pixels, [max(0, int(f.duration_ms)) / 1000.0 for f in frames], cancel)

    # --- finding the device ------------------------------------------------------------------

    DISCOVERY_PORT = DISCOVERY_PORT
    DISCOVERY_PER_HOST_S = DISCOVERY_PER_HOST_S

    @classmethod
    def discover(cls, timeout: float, hint: str | None = None) -> list[dict]:
        """Core's discover hook: sweep the browser's network (*hint*), then
        FiestaBoard's own, for AWTRIX NG clocks. Bounded by *timeout* (at most 10 s)."""
        networks = candidate_subnets(hint_host=hint, local_ip=local_ipv4())
        return sweep(
            OutputHttp(),
            _targets(networks, DISCOVERY_PORT),
            per_host_s=DISCOVERY_PER_HOST_S,
            total_s=min(float(timeout), DISCOVERY_TOTAL_S),
        )

    def action_find_awtrix(self, inputs: dict[str, Any]) -> ActionOutcome:
        """"Find my clock": sweep the given network, the browser's, then FiestaBoard's own."""
        try:
            networks = candidate_subnets(
                subnet=inputs.get("subnet") or None, hint_host=inputs.get("hint_host"), local_ip=local_ipv4()
            )
        except ValueError as exc:
            return ActionOutcome(status="error", message=f"Cannot search that network: {exc}.")
        manual = [
            "Type the clock's address instead: hold the middle button on the clock and open the "
            "device info, or look for it in your router's list of connected devices.",
            "Its local name works too, for example awtrixng-a1b2c3.local.",
            "Or enter your network, for example 192.168.1.0/24, and search again.",
        ]
        if not networks:
            return ActionOutcome(
                status="warning",
                message="FiestaBoard could not tell which network to search.",
                guidance=tuple(manual),
                devices=(),
            )
        found = sweep(self.http, _targets(networks, self.DISCOVERY_PORT), per_host_s=self.DISCOVERY_PER_HOST_S)
        searched = ", ".join(str(n) for n in networks)
        if not found:
            return ActionOutcome(
                status="warning",
                message=f"No AWTRIX NG clock found on {searched}.",
                guidance=tuple(
                    _fence_guidance()
                    + [
                        "Check that the clock is on and on the same network.",
                        "A clock whose web UI asks for a password cannot be found this way.",
                    ]
                    + manual
                ),
                devices=(),
            )
        return ActionOutcome(message=f"Found {len(found)} AWTRIX NG clock(s) on {searched}.", devices=tuple(found))

    # --- probes -----------------------------------------------------------------------------

    def poll_device(self) -> Any:
        """Core's between-writes look at the clock (core's ``poll_device`` hook).

        AWTRIX NG cannot say what the board's app shows, but it can say it
        restarted, and a restart drops every pushed app. Reported as a restart,
        so core sends the board's content again although the page has not
        changed, when:

        - ``uptimeSeconds`` grew less than the time since it was last read
          (with :data:`UPTIME_SLACK_S` to spare): the clock started again since;
        - it answers again after a push failed: whatever it shows now, core's
          last frame never reached it.
        """
        if DevicePoll is None or not self.host:
            return None
        before = self._uptime
        try:
            state = self._device_state()
        except Exception:
            return None
        restarted = False
        if before is not None and self._uptime is not None and self._uptime is not before:
            (was, seen_at), (now, now_at) = before, self._uptime
            restarted = now < was + (now_at - seen_at) - UPTIME_SLACK_S
        selection = None
        if self._native.reference is not None:
            try:
                rows = self._native.inventory(CancelToken())
                present = {row["name"] for row in rows if row["present"]}
                restarted = restarted or bool(set(self._native.pages) - present)
            except Exception:
                # The device read already advanced our uptime baseline. Do not
                # lose a confirmed restart just because inventory is unavailable.
                if not restarted:
                    return None
            if NativePageSelection is not None:
                selection = NativePageSelection(self._native.pages.get(state.get("currentApp")))
        if restarted:
            self._native.payloads.clear()
            self._native.validated = False
            self._native.order_confirmed = False
            self._firmware.confirmed = False
        if selection is not None:
            recover = restarted or self._upload_failed
            self._upload_failed = False
            return DevicePoll(restarted=recover, native_selection=selection)
        if not (restarted or self._upload_failed):
            return None
        logger.info(
            "AWTRIX %s: %s; asking for the board's content again",
            self.host,
            "restarted" if restarted else "answering again after a failed push",
        )
        self._upload_failed = False
        return DevicePoll(restarted=True)

    def check_connection(self) -> ConnectionCheck:
        if self.config.get("native_rotation") is True and NativePageSelection is None:
            return self._failed("bad_response", "Upgrade FiestaBoard to use native collection rotation.",
                                "Native playlist SDK is unavailable", "Turn native collection rotation off on older core.")
        where = self.host or "the clock"
        try:
            body = self._device_state()
        except OutputHostBlocked:
            return ConnectionCheck.blocked(self.host)
        except _NoHost:
            return self._failed("unreachable", "No clock address is set.", "No address", "Enter the clock's IP address.")
        except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError) as exc:
            return self._failed(
                "unreachable",
                f"Could not connect to {where}.",
                str(exc),
                "Check the address: the clock shows it in its device info, and your router lists it.",
                "Make sure the clock is powered on and on the same network as FiestaBoard.",
            )
        except requests.exceptions.Timeout as exc:
            return self._failed(
                "timeout", f"{where} accepted the connection but did not answer.", str(exc), "Restart the clock and try again."
            )
        except AwtrixStatusError as exc:
            if exc.status == 401:
                return self._failed(
                    "auth",
                    f"{where} asks for a password.",
                    str(exc),
                    "Enter the user name and password of the clock's web UI.",
                )
            failure = "server_error" if exc.status >= 500 else "unexpected_status"
            return self._failed(failure, f"{where} answered with {exc}.", str(exc), "Check that this address is an AWTRIX NG clock.")
        except AwtrixBadResponse as exc:
            return self._failed(
                "bad_response", f"{where} answered, but not like AWTRIX NG.", str(exc), "Check that this address is an AWTRIX NG clock."
            )
        except Exception as exc:
            return self._failed("unreachable", f"Could not reach {where}.", str(exc), "Check the address.")
        if body.get("boardType") != TC002:
            if body.get("boardType") in _BOARD_TYPES:
                return self._failed(
                    "bad_response",
                    f"{where} is an AWTRIX NG clock, but not a TC002.",
                    f"boardType {body.get('boardType')!r}",
                    "This plugin draws on the TC002's 52 x 16 display.",
                )
            return self._failed(
                "bad_response", f"{where} answered, but not like AWTRIX NG.", "no boardType", "Check that this address is an AWTRIX NG clock."
            )
        if self.config.get("native_rotation") is True or self.config.get("firmware_transition_override") is True:
            try:
                capabilities = self._request("GET", "/api/v1/capabilities", setup=True)
                display = capabilities.get("display", {}) if isinstance(capabilities, dict) else {}
                if (display.get("width"), display.get("height")) != (PANEL_WIDTH, PANEL_HEIGHT):
                    raise AwtrixBadResponse("Native controls require a verified 52 x 16 TC002")
                if capabilities.get("layout") is not True:
                    raise AwtrixBadResponse("Native controls require TC002 layout capability")
            except Exception as exc:
                return self._failed("bad_response", str(exc), str(exc), "Check the TC002 firmware and display capabilities.")
        details = {key: body[key] for key in ("version", "hostname", "currentApp") if key in body}
        version = f" (AWTRIX NG {body['version']})" if body.get("version") else ""
        message = f"Connected to the TC002 at {where}{version}."
        if self.config.get("firmware_transition_override") is True:
            try:
                _, current = self._firmware.inspect(None)
            except Exception as exc:
                return self._failed("bad_response", f"Firmware transition settings: {exc}", str(exc),
                                    "Choose settings advertised by this device, or turn the override off.")
            keys = ("transitionEffect", "transitionDirection", "transitionDurationMs", "autoTransition")
            details["firmware_transitions"] = {key: current.get(key) for key in keys}
            message += f" Firmware transitions: {current.get('transitionEffect')}, {current.get('transitionDurationMs')} ms, {current.get('transitionDirection')}; automatic rotation {'on' if current.get('autoTransition') else 'off'}."
        return ConnectionCheck(success=True, message=message, details=details)

    @staticmethod
    def _failed(failure: Any, message: str, error: str, *steps: str) -> ConnectionCheck:
        return ConnectionCheck(success=False, message=message, failure=failure, error=error, troubleshooting=steps)

# Ulanzi TC002 Output Plugin

Shows your FiestaBoard pages on a **Ulanzi TC002** running
[AWTRIX NG](https://github.com/Blueforcer/awtrix-ng), over your local
network. The board becomes an app in the clock's rotation, beside the
clock's own apps and scripts.

<img src="docs/clock-word-clock.png" alt="The TC002 showing a word clock page from FiestaBoard: ES IST FUENF / NACH HALB ELF" width="520">

52 × 16 pixels, 3 rows of 13 characters. Above, the
[Word Clock](https://github.com/rummeyer/fiestaboard-plugin--word-clock)
plugin's page as the clock shows it.

> **Beta.** Output plugins need FiestaBoard 10.0.0 or later, where the
> output-plugin API is still a beta. Runs on a real TC002 with AWTRIX NG
> 1.2.2 and FiestaBoard 10.1.2: setup, connection test, pages and colours.
> How Show on change and the animated page changes time on the clock is still
> to be checked; see [Verify on your clock](#verify-on-your-clock).

## Requirements

- FiestaBoard 10.0.0 or later (output plugins need no switch turned on)
- A Ulanzi TC002 running AWTRIX NG, on the same network as FiestaBoard
- Nothing to install on the clock, and no cloud service or account

**AWTRIX NG only.** The plugin is written for AWTRIX NG and tested with it
alone. AWTRIX 3 and Ulanzi's own firmware are not supported: the plugin
talks to AWTRIX NG's `/api/v1`, which they do not have, and **Test
connection** fails on them with *Check that this address is an AWTRIX NG
clock* rather than letting a board be added that never shows anything.

The 32 × 8 AWTRIX NG clocks (TC001, ESP32 builds) are not supported: they
cannot draw the full-size layouts the plugin sends.

## Quick start

1. In FiestaBoard, open **Displays → Marketplace** and install **Ulanzi
   TC002 (AWTRIX NG)** — search for *AWTRIX* or *TC002*. Where it is not
   listed yet, paste `https://github.com/rummeyer/fiestaboard-output--ulanzi-tc002`
   under **Bring your own display** (or use **Integrations → Marketplace → Add
   from Git**).
   Or from a terminal:

   ```sh
   curl -X POST http://<fiestaboard>:4420/api/plugins/install \
     -H "Content-Type: application/json" \
     -d '{"repository": "https://github.com/rummeyer/fiestaboard-output--ulanzi-tc002"}'
   ```

2. Open **Displays → Add a display** and pick **Ulanzi TC002 (AWTRIX NG)**.
3. Enter the clock's address, or press **Find my clock**. A local name such as
   `awtrixng-a1b2c3.local` works too.
4. Switch on **Show on change**: FiestaBoard's form shows it off, whatever
   the plugin's default, and saves what it shows. **App name** may stay
   empty, for `FiestaBoard`.
5. Press **Test connection**. It answers with the clock's firmware version.
6. Make a page for the clock (see [Pages for the clock](#pages-for-the-clock))
   and put it on the board. The clock switches to the app and shows it.

<img src="docs/display-settings.jpg" alt="FiestaBoard's settings of an AWTRIX NG display: name, Pause sends, Restore automatically, Clock address with Find my clock" width="760">

<img src="docs/display-settings-plugin.jpg" alt="The plugin's own settings below: Network to search, App name FiestaBoard, Show on change on, Display time, Tile style, Block padding" width="760">

## Pages for the clock

A page is made for one size of board, and the clock's is its own: 3 rows of
13. Pages made for a Vestaboard Note (3 × 15) or Flagship do not fit, and
FiestaBoard will not put them on the clock ("none of its pages fit this board
size"); a collection of them neither.

To make one, open **Pages → New page** and choose **Ulanzi TC002 (AWTRIX NG)**
in the menu beside **Preview**. The preview then shows the clock's LED grid,
and the editor counts 13 characters a row.

<img src="docs/page-editor.jpg" alt="A new page in FiestaBoard with AWTRIX NG chosen as its size: the template {{= word_clock.block }} and an LED preview reading ES IST VIERTEL VOR ELF with a red heart" width="760">

A plugin that lays out its own block for the board it is shown on fills the
13 columns by itself: `{{= word_clock.block }}` above, or the Netatmo
Weather plugin's `{{= netatmo_weather.block }}`. Fields cut to a Note's 15
characters, as `title_note` and `artist_note` of Now Playing, run past the
13 and need their own template.

The page list's filter has the clock too, and shows only the pages that fit
it.

## What it shows

FiestaBoard lays each page out in its 3×5 LED font: **3 rows of 13
characters**.

- Letters, digits and punctuation, white on black; capitals read best at
  this size
- Colour tiles `{63}`–`{68}` as solid cells in the board colours, `{69}` white,
  `{70}`/`{71}` unlit
- Colour spans (`{red:HOT}`), block spans and icons (`{icon:sun}`), exactly as
  the board's preview in FiestaBoard draws them
- Pixel canvases over the text, on a FiestaBoard with canvas pages

<img src="docs/clock-colours.png" alt="The clock showing HELLO WORLD, SUNNY in yellow with 21°, and the seven colour tiles red to white" width="420"> <img src="docs/clock-netatmo.png" alt="The clock showing three rows of room temperatures: WOHNEN 22°C, SCHLAF 22°C, BALKON 10°C" width="420">

Left, `HELLO WORLD`, `{yellow:SUNNY} 21°` and the tiles `{63}`–`{69}`; right,
three full rows from the Netatmo Weather plugin.

### Why 3 rows fit on 16 pixels

Three rows of the 3×5 font with a dark pixel row between them need 17 pixel
rows, and FiestaBoard makes no LED board smaller than 3 × 10 characters. So
FiestaBoard draws the board at 52 × 17, and the plugin leaves out the dark row
between the second and the third text row before it sends a frame:

```
 HELLO WORLD        pixel rows 0 to 4
                    row 5 stays dark
 SUNNY 72           pixel rows 6 to 10
 🟥🟧🟨🟩🟦🟪       pixel rows 11 to 15, right under the second
```

The first row, often a title, keeps its space; the second and third touch.
The board's preview in FiestaBoard still shows the dark row. The larger 5×7
font fits only 2 rows on 16 pixels and is not offered.

**Two rows read best.** Leave the third row of a page empty and the plugin
centres the other two, with the gap between them, in the middle of the
panel:

```
                    pixel rows 0 and 1 stay dark
 WOHNEN   22°C      pixel rows 2 to 6
                    row 7 stays dark
 SCHLAF   22°C      pixel rows 8 to 12
                    rows 13 to 15 stay dark
```

Anything in the third row — a word, a colour tile, a canvas pixel — keeps
the three-row layout above. FiestaBoard does not allow a board of two rows,
so the page itself still has three.

## How it behaves on the clock

**An app in the rotation.** Each frame is a pushed app
(`PUT /api/v1/apps/pushed/FiestaBoard`) whose layout covers the whole display
and draws one picture. A layout keeps all 52 × 16 pixels, whatever the clock's
**Enlarge apps** setting says. The app takes its turn like any other, for the
clock's app duration or the board's **Display time**.

**Show on change** (on by default) switches the clock to the board whenever
its page changes, so the change is seen as it happens. Turned off, the page
changes quietly and shows on the board's next turn.

**Always on screen.** To show nothing but the board, switch off **Auto
transition** in the clock's settings, or leave the board alone in the
rotation.

**Animated page changes.** FiestaBoard's LED transitions (flip by default;
slide, wipe, cascade, dissolve, fade) are streamed as single frames at five a
second. Fade dims through black in six frames, or cross-fades with **Fade
style** Blend. A change only animates where it can be seen: with Show on
change off, a board that is not on screen just gets the new page.

**After a restart.** A pushed app lives in the clock's memory only and is gone
after a restart. The plugin reads the clock's uptime between page changes and,
when it restarted, has FiestaBoard send the page again.

**Left alone:** the clock's brightness, its other apps and its settings. The
plugin only pushes its own app and switches to it.

## Settings

| Setting | Default | |
|---|---|---|
| Clock address | — | IP address or local name, as `192.168.1.60` or `awtrixng-a1b2c3.local`. |
| App name | `FiestaBoard` | The app's name in the rotation and on the clock's **Apps** tab. Letters, digits, `-` and `_`, at most 32; empty is `FiestaBoard`. Give each board its own when several boards share one clock. |
| Show on change | on | Switch the clock to the board whenever its page changes. |
| Display time | the clock's | Seconds the board stays on screen in the rotation. |
| Tile style | Gaps | Seamless lights the line between two tiles of the same colour, so a row of tiles reads as one bar. |
| Block padding | Off | 1 pixel grows highlighted text's background by a pixel on every side. |
| Fade style | Through black | Or Blend. |
| User name, Password | — | Only when the clock's web UI asks for a password (`authEnabled`). The password is stored as a secret. |

### Find my clock

Asks every address of a /24 network for `GET /api/v1/device` (at most 64 at
a time, 10 s in all) and lists what answers like AWTRIX NG, with its name. It
searches, in order, the network you type (a /22 at most, private addresses
only), the network of the address you opened FiestaBoard at, and the network
FiestaBoard itself is on. Nothing outside your network is contacted.

A clock with a password answers no one and cannot be found this way: type its
address. `FIESTABOARD_OUTPUTS_ALLOW_HOSTS`, when set, limits which hosts are
contacted at all.

### Test connection

Reads the clock's state and says which AWTRIX NG version it runs. It tells
apart an unreachable or silent clock, a wrong password, a device that is not
AWTRIX NG, and an AWTRIX NG clock that is not a TC002.

## Removing a board

When you rename the app or remove the board, the old app stays on the clock
until it restarts. Delete it from the clock's **Apps** tab, or:

```sh
curl -X DELETE http://<clock>/api/v1/apps/FiestaBoard
```

## Verify on your clock

Taken from the AWTRIX NG documentation, still to be checked on a TC002:

| What | Assumed | Setting it affects |
|---|---|---|
| Stream rate | 5 frames a second all show | `maxFps` in `output/device-models.json` |
| Replacing the app while shown | Shows at once, without the clock's own app transition | every animated change |
| Switching with `fast` | Instant; the app's display time starts again | Show on change |

`tools/try_device.py` shows one page, waits five seconds, changes it with the
transition you name and prints every request with its timing. It runs once
and stops:

```sh
PYTHONPATH=/path/to/FiestaBoard python3 tools/try_device.py --host 192.168.1.60 --transition flip
```

Please open an issue with what you see, and your AWTRIX NG version.

## Development

Tests run against FiestaBoard v10.3.0, which includes the native playlist API:

```sh
./run_tests.sh /path/to/FiestaBoard
```

They use a mock clock on 127.0.0.1 (`tests/mock_awtrix.py`) that records every
request and can fail, hang, ask for a password or restart on purpose; a network
fence refuses any other host. The suite includes FiestaBoard's output-plugin
conformance suite and needs 80 % coverage. CI runs it on every push.

| File | Contents |
|---|---|
| `__init__.py` | The plugin: rendering, pushing, streaming, discovery, restart detection |
| `manifest.json` | Plugin id `ulanzi_tc002`, the board settings and actions |
| `output/device-models.json` | The `ulanzi_tc002` device model: 52 × 17 pixels, 3×5 font, 5 fps stream, with sources |
| `tools/try_device.py` | The one-off check against a real clock |

## Licence

MIT. AWTRIX NG is by [Blueforcer](https://github.com/Blueforcer/awtrix-ng).


## Native collection rotation

Requires FiestaBoard 10.3.0 or later. Enable **Native collection rotation** on this display to put each timed collection page in a separate clock app. The TC002's left/right buttons and AWTRIX app-navigation API then select pages. Collection order and dwell times are preserved. Ordinary data refreshes update the app image without stealing the current selection or restarting its timer. Selected-page feedback comes from the clock. With **Show on change** off, installing or restoring a playlist never forces the clock to switch apps.

Single pages use one owned slot. Random/rule collections keep FiestaBoard's ordinary streaming behavior. Native mode is off by default; the original streaming behavior remains available. Geometry stays 3 rows of 13 characters, using the same 52x17-to-52x16 compression as ordinary pages, including canvas layers. Preview spacing still differs from the physical panel as described above.

Slots use a stable board-specific namespace. Foreign apps and disabled names are preserved. Interrupted uploads are staged outside the rotation and retried; missing slots after a clock restart are restored. The clock has a limit of 50 pushed apps total. Replacing a full collection requires room for the new slots while the old ones remain; if there is insufficient space, the current playlist is preserved and the update fails for retry. At capacity, opting out must retire this board's native slots before it can create the ordinary slot. A failed legacy push then needs retry. Pausing FiestaBoard stops refreshes but may leave clock-owned apps rotating.

**Override firmware transitions** is a separate, optional master switch, off by default. Effect, direction, duration and automatic rotation apply to the whole TC002, including its other apps. “Use device settings” or an empty duration leaves that value alone. Zero duration switches instantly; automatic rotation Off leaves button/API navigation available. Test connection only reads actual settings. Settings writes require verified TC002 identity, 52x16 geometry, layout capability and an advertised effect; acknowledgement and independent readback must agree. Turning the override off preserves the current device values, rather than restoring old values.

Physical acceptance of native rotation is still required on your installed display. A legacy app inherited from a previous process is preserved because its name alone does not prove ownership; remove it only after explicitly verifying it belongs to this board.

Native rotation and firmware-control helpers are adapted from [FiestaBoard’s TC001 plugin](https://github.com/Fiestaboard/fiestaboard-output--ulanzi-tc001). Original TC002 rendering and integration are by Oliver Rummeyer; both MIT copyright notices are retained.

## Flexible physical frames (optional newer-core capability)

Native collection rotation and the device controls above are available with
FiestaBoard **10.3.0**. Flexible JSON pages are a separate capability supplied by
[FiestaBoard PR #2395](https://github.com/Fiestaboard/FiestaBoard/pull/2395); they
are not part of the released 10.3.0 contract.

The plugin keeps its **output API 1** manifest. On a core that supplies both the
physical-frame SDK helpers and capability field, its existing capability hooks
declare a 52 × 16 physical surface for ordinary and native delivery. On older
cores those optional helpers are absent, so the plugin loads normally and keeps
its existing text/Canvas behavior. Native rotation still requires 10.3.0 and
must remain off on earlier cores.

Explicitly tagged flexible frames preserve exact RGB888 bytes and bypass the
legacy 52 × 17 row removal and centering. Unmarked legacy pages keep that
transform. Ordinary writes and native uploads use the same renderer. Transitions
involving tagged frames use one still replacement because the legacy cell
animation planner cannot safely mix the two coordinate spaces.

Tests run against released core 10.3.0 and a pinned revision of PR #2395. The
physical-frame tests skip on the released core, where the capability does not
exist; legacy rendering and native-rotation tests run on both. No flexible page
has been installed or verified on physical hardware by this change. Review the
core reader and backup requirements before enabling flexible documents in a
shared library.

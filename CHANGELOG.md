# Changelog

## 0.3.0 (unreleased)

- Optional clock-owned timed collections with per-page apps, button/API selection, focus-preserving refresh and selected-page feedback on FiestaBoard 10.3.0.
- Preserve TC002 native-size layout rendering and 52x17 virtual-frame compression.
- Optional validated device-wide transition controls; defaults leave device settings unchanged.
- Recover missing apps and interrupted ordering while preserving foreign apps.

## 0.2.0 — 2026-10-26

- Now `fiestaboard-output--ulanzi-tc002`, plugin id `ulanzi_tc002`, named for
  the device; it was `fiestaboard-output--awtrix-ng` with `awtrix_ng`. A board
  added with 0.1.0 belongs to the old id: install this one, add the board
  again, then remove the old board and the old plugin.
- The README says it needs AWTRIX NG: AWTRIX 3 and Ulanzi's own firmware are
  not supported.
- Ready for FiestaBoard's plugin registry: search details for the display
  catalog (LED matrices, Ulanzi TC002, AWTRIX) and a data-only `package.json`
  with the device data, as FiestaBoard's own output plugins have.
- The display is called **Ulanzi TC002 (AWTRIX NG)**: the device first, as
  the device model already was.
- An empty **App name** saves, and means `FiestaBoard`: FiestaBoard's settings
  form does not fill in a setting's default, and the field failed its pattern.
- A page that leaves the third row empty is drawn as two rows in the middle
  of the panel, each with its gap, rather than at the top.
- README: pictures of the clock and of the setup, and how to make pages for
  the clock.

## 0.1.0

- First version: FiestaBoard pages on a Ulanzi TC002 running AWTRIX NG, as a
  pushed app in the clock's rotation, 3 rows × 13 characters in the 3×5 font.
- LED transitions streamed at five frames a second; fade through black or blend.
- Show on change, display time, app name, Basic Auth.
- Find my clock, Test connection, and the page sent again after the clock restarts.

"""Reconcile this board's native AWTRIX apps without owning the whole device.

Names reserve a board-specific namespace. Payloads live in RAM on the clock;
order and disabled names are persistent. Never infer ownership from app titles.
"""
from __future__ import annotations

import hashlib
import re


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]


class NativeRotation:
    def __init__(self, plugin):
        self.plugin = plugin
        self.prefix = f"fb_{_digest(str(plugin.board_id))}_"
        self.pattern = re.compile(re.escape(self.prefix) + r"[0-9a-f]{12}$")
        self.payloads = {}
        self.pages = {}
        self.reference = None
        self.validated = False
        self.legacy_checked = False
        self.order_confirmed = False
        self.legacy_written = False
        self.cleanup_dirty = False

    def _call(self, method, path, cancel, body=None):
        if cancel.cancelled:
            from src.plugins import RequestCancelled
            raise RequestCancelled("Native rotation cancelled")
        return self.plugin._request(method, path, json=body, setup=True, cancel=cancel)

    def inventory(self, cancel):
        rows = self._call("GET", "/api/v1/apps", cancel)
        if isinstance(rows, list):
            # Firmware library modules have no rotation fields at all.
            # Keep their identities for collision detection, but never order them.
            rows = [dict(row, enabled=True, inLoop=False, slot=None)
                    if isinstance(row, dict) and row.get("origin") == "module" else row for row in rows]
        if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not isinstance(row.get("name"), str)
            or not isinstance(row.get("enabled"), bool)
            or not isinstance(row.get("present"), bool)
            for row in rows
        ):
            raise ValueError("AWTRIX app inventory is incomplete")
        return rows

    def owns(self, name):
        return bool(self.pattern.fullmatch(name))

    def _owned(self, rows):
        result = set()
        for row in rows:
            if self.owns(row["name"]):
                if row.get("origin") not in ("pushed", None):
                    raise ValueError("Native slot name conflicts with a firmware app or script")
                result.add(row["name"])
        return result

    def _order(self, rows, desired, removed):
        """Replace our block in place; preserve all other order/duplicate slots."""
        ordered = []
        inserted = False
        for row in rows:
            name = row["name"]
            if name in removed or self.owns(name):
                if not inserted:
                    ordered.extend(desired)
                    inserted = True
            elif row.get("origin") != "module":
                ordered.append(name)
        if not inserted:
            ordered.extend(desired)
        return ordered

    def _arrange(self, rows, desired, removed, cancel):
        order = self._order(rows, desired, removed)
        disabled = list(dict.fromkeys(row["name"] for row in rows
                                     if not row["enabled"] and row["name"] not in removed
                                     and row["name"] not in desired))
        old_order = [row["name"] for row in rows if row.get("slot") is not None]
        old_disabled = list(dict.fromkeys(row["name"] for row in rows if not row["enabled"]))
        changed = not self.order_confirmed or order != old_order or set(disabled) != set(old_disabled) or bool(removed)
        if changed:
            # Readback proves RAM state only. A 507 may have applied but not
            # saved it; keep this dirty until a successful save acknowledgement.
            self.order_confirmed = False
            # Suppress stale apps before deletion: unlisted present apps otherwise
            # rejoin the end of the loop. A failed delete remains safely disabled.
            self._call("PUT", "/api/v1/apps/order", cancel,
                       {"order": order, "disabled": list(dict.fromkeys(disabled + sorted(removed)))})
            for name in sorted(removed):
                self._call("DELETE", f"/api/v1/apps/{name}", cancel)
            if removed:
                self._call("PUT", "/api/v1/apps/order", cancel,
                           {"order": order, "disabled": disabled})
            self.order_confirmed = True
        return changed

    def sync(self, playlist, cancel):
        if not self.plugin.board_id or playlist.board_id != self.plugin.board_id:
            raise ValueError("Native playlist belongs to a different board")
        if not playlist.entries:
            return self.retire(cancel)
        if not self.validated:
            check = self.plugin.check_connection()
            if cancel.cancelled:
                from src.plugins import RequestCancelled
                raise RequestCancelled("Native validation cancelled")
            if not check.success:
                raise ValueError("Native rotation requires a verified AWTRIX NG TC002")
            self.validated = True
        desired, payloads, pages = [], {}, {}
        for entry in playlist.entries:
            if not isinstance(entry.page_id, str) or not entry.page_id:
                raise ValueError("Native page needs an identity")
            if isinstance(entry.duration_ms, bool) or not isinstance(entry.duration_ms, int) or entry.duration_ms < 0:
                raise ValueError("Native page duration must be a nonnegative integer")
            name = self.prefix + _digest(entry.page_id)
            payload = self.plugin._payload(self.plugin.render(entry.cells))
            if entry.duration_ms:
                payload["durationMs"] = entry.duration_ms
            if name in payloads and payloads[name] != payload:
                raise ValueError("Repeated page has conflicting content or duration")
            desired.append(name)
            payloads[name] = payload
            pages[name] = entry.page_id
        rows = self.inventory(cancel)
        owned = self._owned(rows)
        present = {row["name"] for row in rows if row["present"]}
        pushed = {row["name"] for row in rows if row["present"] and row.get("origin") == "pushed"}
        if len(pushed | set(payloads)) > 50:
            raise ValueError("Native rotation exceeds the clock's 50 pushed-app capacity; existing apps were preserved")
        current = self._call("GET", "/api/v1/device", cancel).get("currentApp")
        self.plugin._firmware.apply(cancel)
        missing = set(payloads) - present
        changed = False
        if missing:
            # Reserve new names disabled before pushing any content. If a push
            # fails, no partial collection enters rotation. A later sync (even
            # after process restart) enables our desired slots on commit.
            staging = list(dict.fromkeys([row["name"] for row in rows if not row["enabled"]] + sorted(missing)))
            self.order_confirmed = False
            self._call("PUT", "/api/v1/apps/order", cancel, {"disabled": staging})
            rows = self.inventory(cancel)
        for name, payload in payloads.items():
            if name in missing or self.payloads.get(name) != payload:
                self._call("PUT", f"/api/v1/apps/pushed/{name}", cancel, payload)
                changed = True
        removed = owned - set(payloads)
        # A configured/default name alone does not establish ownership. Only
        # retire a legacy app this live writer successfully created. After a
        # process restart leave ambiguous legacy apps for explicit migration.
        if self.legacy_written and any(row["name"] == self.plugin.app_name and row.get("origin") == "pushed" for row in rows):
            removed.add(self.plugin.app_name)
        changed = self._arrange(rows, desired, removed, cancel) or changed
        if self.plugin.app_name in removed:
            self.legacy_written = False
        eligible = desired
        # Replacements do not restart AWTRIX's dwell timer. Only a new selection
        # or lost slot permits a focus change, never a normal refresh.
        if self.plugin.show_on_change and current not in desired and eligible and (self.reference != playlist.reference_id or missing):
            self._call("PUT", "/api/v1/apps/active", cancel, {"name": eligible[0], "fast": True})
            changed = True
        self.payloads, self.pages, self.reference = payloads, pages, playlist.reference_id
        self.legacy_checked = False
        return changed

    def retire(self, cancel):
        rows = self.inventory(cancel)
        removed = self._owned(rows)
        changed = False
        if removed or self.cleanup_dirty:
            # Keep retrying the final persisted order even when an unsuccessful
            # acknowledgement already removed every owned app from device RAM.
            self.cleanup_dirty = True
            changed = self._arrange(rows, [], removed, cancel)
            self.cleanup_dirty = False
        self.payloads, self.pages, self.reference = {}, {}, None
        self.legacy_checked = True
        return changed

    def prepare_legacy(self, cancel):
        """Free space for opt-out only when a new legacy slot cannot fit.

        At capacity AWTRIX cannot atomically swap names: retire our slots first,
        then retry the ordinary push. No foreign slot is sacrificed for space.
        """
        if self.legacy_checked:
            return
        rows = self.inventory(cancel)
        owned = self._owned(rows)
        if not owned and not self.cleanup_dirty:
            self.legacy_checked = True
            return
        present = {row["name"] for row in rows if row["present"] and row.get("origin") == "pushed"}
        if self.plugin.app_name not in present and len(present) >= 50:
            self.retire(cancel)

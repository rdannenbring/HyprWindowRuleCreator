"""Show a rule's effects on a window that is already open.

A window rule only fires when a window maps, so previewing means reproducing
the rule's effects through a second mechanism and keeping a journal precise
enough to undo it:

  dynamic effects -> hl.dsp.window.set_prop  (the wiki: "All dynamic effects
                     can be set with set_prop")
  static effects  -> the matching dispatcher, where one exists

Neither path is the real rule, so preview is an approximation and says so.
Anything it cannot reproduce is reported rather than silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import catalog, ipc
from .model import Rule


def _lua_table(**kwargs) -> str:
    parts = []
    for key, value in kwargs.items():
        if value is None:
            continue
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, (int, float)):
            rendered = str(value)
        else:
            rendered = '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'
        parts.append(f"{key} = {rendered}")
    return "{ " + ", ".join(parts) + " }"


def _numeric_pair(value) -> tuple[int, int] | None:
    """Accept {800, 600} / "800 600" / "800,600"; reject expressions."""
    if isinstance(value, (list, tuple)):
        parts = [str(v).strip() for v in value]
    else:
        parts = [p for p in str(value).replace(",", " ").split() if p]
    if len(parts) != 2:
        return None
    try:
        return int(float(parts[0])), int(float(parts[1]))
    except ValueError:
        return None  # an expression like monitor_w*0.5 -- dispatchers can't


@dataclass
class PreviewReport:
    applied: list[str]
    skipped: dict[str, str]  # effect -> why

    @property
    def ok(self) -> bool:
        return bool(self.applied)


class Preview:
    """Applies effects to one window and can put it back."""

    def __init__(self, address: str):
        self.address = address
        self.selector = f"address:{address}"
        self._props_set: dict[str, object] = {}
        self._baseline: dict | None = None
        self._geometry_touched = False
        self._state_touched = False

    # -- baseline ---------------------------------------------------------

    def _capture(self) -> dict:
        if self._baseline is None:
            for win in ipc.clients():
                if win["address"] == self.address:
                    self._baseline = win
                    break
            else:
                raise ipc.HyprError(f"window {self.address} is gone")
        return self._baseline

    def window_is_alive(self) -> bool:
        return any(w["address"] == self.address for w in ipc.clients())

    # -- apply ------------------------------------------------------------

    def apply(self, rule: Rule) -> PreviewReport:
        self._capture()
        self.revert()

        applied: list[str] = []
        skipped: dict[str, str] = {}

        for key, value in rule.effects.items():
            f = catalog.EFFECT_BY_KEY[key]
            if not f.preview:
                skipped[key] = "only observable when the window opens"
                continue
            # Held back deliberately: previewing these strands the user with no
            # way back to the editor. They still save and apply normally.
            if f.traps_input and value:
                skipped[key] = f.traps_input
                continue
            try:
                if f.preview == "prop":
                    self._set_prop(key, self._prop_value(f, value))
                else:
                    reason = self._dispatch_effect(key, value)
                    if reason:
                        skipped[key] = reason
                        continue
                applied.append(key)
            except ipc.HyprError as exc:
                skipped[key] = str(exc).split("<--")[0].strip()

        return PreviewReport(applied=applied, skipped=skipped)

    @staticmethod
    def _prop_value(f: catalog.Field, value) -> str:
        if f.kind == "bool":
            return "1" if value else "0"
        if f.kind == "vec2":
            if isinstance(value, (list, tuple)):
                return " ".join(str(v) for v in value)
            return str(value).replace(",", " ")
        return str(value)

    def _set_prop(self, prop: str, value: str) -> None:
        ipc.dispatch(
            f"hl.dsp.window.set_prop({_lua_table(prop=prop, value=value, window=self.selector)})"
        )
        self._props_set[prop] = value

    def _dispatch_effect(self, key: str, value) -> str | None:
        """Returns None on success, or a reason string when not previewable."""
        sel = self.selector
        act = "enable" if value else "disable"

        if key == "float":
            ipc.dispatch(f"hl.dsp.window.float({_lua_table(action=act, window=sel)})")
            self._state_touched = True
        elif key == "tile":
            inv = "disable" if value else "enable"
            ipc.dispatch(f"hl.dsp.window.float({_lua_table(action=inv, window=sel)})")
            self._state_touched = True
        elif key == "pin":
            ipc.dispatch(f"hl.dsp.window.pin({_lua_table(action=act, window=sel)})")
            self._state_touched = True
        elif key == "pseudo":
            ipc.dispatch(f"hl.dsp.window.pseudo({_lua_table(action=act, window=sel)})")
            self._state_touched = True
        elif key == "center":
            if not value:
                return None
            ipc.dispatch(f"hl.dsp.window.center({_lua_table(window=sel)})")
            self._geometry_touched = True
        elif key in ("fullscreen", "maximize"):
            mode = "fullscreen" if key == "fullscreen" else "maximized"
            ipc.dispatch(
                f"hl.dsp.window.fullscreen({_lua_table(mode=mode, action=act, window=sel)})"
            )
            self._state_touched = True
        elif key == "size":
            pair = _numeric_pair(value)
            if pair is None:
                return "expressions can't be previewed (dispatchers take literal px)"
            ipc.dispatch(
                f"hl.dsp.window.resize({_lua_table(x=pair[0], y=pair[1], relative=False, window=sel)})"
            )
            self._geometry_touched = True
        elif key == "move":
            pair = _numeric_pair(value)
            if pair is None:
                return "expressions can't be previewed (dispatchers take literal px)"
            ipc.dispatch(
                f"hl.dsp.window.move({_lua_table(x=pair[0], y=pair[1], relative=False, window=sel)})"
            )
            self._geometry_touched = True
        else:
            return "no dispatcher equivalent"
        return None

    # -- revert -----------------------------------------------------------

    def revert(self) -> None:
        if not self.window_is_alive():
            self._props_set.clear()
            return

        for prop in list(self._props_set):
            self._revert_prop(prop)
        self._props_set.clear()

        base = self._baseline
        if base is None:
            return

        if self._state_touched:
            self._restore_state(base)
            self._state_touched = False
        if self._geometry_touched:
            self._restore_geometry(base)
            self._geometry_touched = False

    def _revert_prop(self, prop: str) -> None:
        f = catalog.EFFECT_BY_KEY.get(prop)
        try:
            self._raw_set_prop(prop, "unset")
            return
        except ipc.HyprError:
            pass
        # Float-parsed props reject "unset" (they error with `stof`), so put
        # back a known-neutral value instead.
        fallback = (f.revert_to if f else None) or "1.0"
        for prop_name in catalog.PROP_EXPANSIONS.get(prop, (prop,)):
            try:
                self._raw_set_prop(prop_name, fallback)
            except ipc.HyprError:
                pass

    def _raw_set_prop(self, prop: str, value: str) -> None:
        ipc.dispatch(
            f"hl.dsp.window.set_prop({_lua_table(prop=prop, value=value, window=self.selector)})"
        )

    def _restore_state(self, base: dict) -> None:
        sel = self.selector
        for name, want in (
            ("float", base.get("floating")),
            ("pin", base.get("pinned")),
        ):
            action = "enable" if want else "disable"
            try:
                ipc.dispatch(f"hl.dsp.window.{name}({_lua_table(action=action, window=sel)})")
            except ipc.HyprError:
                pass
        try:
            was_fs = bool(base.get("fullscreen"))
            ipc.dispatch(
                "hl.dsp.window.fullscreen("
                + _lua_table(action="enable" if was_fs else "disable", window=sel)
                + ")"
            )
        except ipc.HyprError:
            pass

    def _restore_geometry(self, base: dict) -> None:
        sel = self.selector
        at, size = base.get("at"), base.get("size")
        if not (at and size):
            return
        try:
            ipc.dispatch(
                f"hl.dsp.window.resize({_lua_table(x=size[0], y=size[1], relative=False, window=sel)})"
            )
            ipc.dispatch(
                f"hl.dsp.window.move({_lua_table(x=at[0], y=at[1], relative=False, window=sel)})"
            )
        except ipc.HyprError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.revert()
        return False

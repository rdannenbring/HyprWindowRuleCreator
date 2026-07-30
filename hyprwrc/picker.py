"""Click-to-select a window, via slurp.

The approach is lifted from hyprprop (hyprwm/contrib, by Douile), which in turn
took it from grimblast: feed slurp one box per window and let it hand back the
label of whichever box was clicked. We reimplement rather than depend on it
because hyprprop is an AUR package that only prints JSON -- we want the
selection step alone, plus special-workspace handling it doesn't do.

Using `-r` (restrict to predefined boxes) is the important part: it makes the
selection snap to whole windows, so overlapping windows are disambiguated by
the user looking at them rather than by us guessing z-order.
"""

from __future__ import annotations

import shutil
import subprocess

from . import ipc


class PickerError(RuntimeError):
    pass


class Cancelled(Exception):
    """User pressed escape / right-clicked out of the selection."""


def require_slurp() -> str:
    path = shutil.which("slurp")
    if not path:
        raise PickerError("slurp is not installed (pacman -S slurp)")
    return path


def pick_window(border_color: str | None = "#8aadf4ff",
                exclude: set[str] | None = None) -> dict:
    """Block until the user clicks a window. Returns its `hyprctl clients` dict.

    `exclude` drops addresses from the offered boxes -- used when re-picking
    from inside the editor, so the editor is not one of the choices.
    """
    slurp = require_slurp()
    windows = [w for w in ipc.selectable_windows()
               if not exclude or w["address"] not in exclude]
    if not windows:
        raise PickerError("no selectable windows on the active workspaces")

    by_address = {w["address"]: w for w in windows}
    boxes = "\n".join(
        f"{w['at'][0]},{w['at'][1]} {w['size'][0]}x{w['size'][1]} {w['address']}"
        for w in windows
    )

    argv = [slurp, "-r", "-f", "%l"]
    if border_color:
        argv += ["-c", border_color, "-s", "#00000000", "-b", "#00000040", "-w", "3"]

    proc = subprocess.run(
        argv, input=boxes, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise Cancelled()

    address = proc.stdout.strip()
    if address not in by_address:
        raise Cancelled()

    # Re-read: geometry may have shifted while the user was choosing.
    for win in ipc.clients():
        if win["address"] == address:
            return win
    return by_address[address]


def window_by_address(address: str) -> dict | None:
    for win in ipc.clients():
        if win["address"] == address:
            return win
    return None


def _contains(win: dict, x: int, y: int) -> bool:
    wx, wy = win["at"]
    ww, wh = win["size"]
    return wx <= x < wx + ww and wy <= y < wy + wh


def choose_at_point(windows: list[dict], x: int, y: int,
                    active_address: str | None = None) -> dict | None:
    """Pick which of `windows` is under (x, y). Pure; see window_at_cursor.

    Hyprland exposes no window-at-point call, so this hit-tests against client
    geometry. Overlaps are broken in this order:

      1. the compositor's own active window -- under `follow_mouse` (the
         default) that *is* the hovered window, which beats any guess we
         could make;
      2. a window on an open special workspace, which renders above normal
         ones;
      3. most recently focused, via focusHistoryID.
    """
    hits = [w for w in windows if _contains(w, x, y)]
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]

    def rank(win: dict):
        return (
            win["address"] != active_address,
            (win.get("workspace") or {}).get("id", 0) >= 0,
            win.get("focusHistoryID", 9999),
        )

    return sorted(hits, key=rank)[0]


def cursor_position() -> tuple[int, int]:
    pos = ipc.request("cursorpos").strip()
    try:
        x, y = (int(p.strip()) for p in pos.split(","))
    except ValueError:
        raise PickerError(f"could not read cursor position: {pos!r}")
    return x, y


def window_at_cursor() -> dict:
    """The window under the pointer, with no selection step."""
    x, y = cursor_position()
    try:
        active_address = (ipc.request_json("activewindow") or {}).get("address")
    except ipc.HyprError:
        active_address = None

    win = choose_at_point(ipc.selectable_windows(), x, y, active_address)
    if win is None:
        raise PickerError(f"no window under the cursor at {x},{y}")
    return win

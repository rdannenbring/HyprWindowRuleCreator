"""Hyprland IPC over the control socket.

Talks to `$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/.socket.sock`
directly rather than shelling out to hyprctl -- preview fires a dispatch on
every slider tick, and a fork per tick is the difference between smooth and
not.
"""

from __future__ import annotations

import json
import os
import socket


class HyprError(RuntimeError):
    pass


def _socket_path() -> str:
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not xdg or not sig:
        raise HyprError(
            "not inside a Hyprland session "
            "(XDG_RUNTIME_DIR / HYPRLAND_INSTANCE_SIGNATURE unset)"
        )
    path = f"{xdg}/hypr/{sig}/.socket.sock"
    if not os.path.exists(path):
        raise HyprError(f"Hyprland control socket missing at {path}")
    return path


def request(payload: str) -> str:
    """Send one raw command and return the response body."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(_socket_path())
        sock.sendall(payload.encode())
        chunks = []
        while True:
            chunk = sock.recv(8192)
            if not chunk:
                break
            chunks.append(chunk)
    return b"".join(chunks).decode(errors="replace")


def request_json(command: str):
    raw = request(f"j/{command}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HyprError(f"{command!r} returned non-JSON: {raw[:200]!r}") from exc


def dispatch(lua: str) -> str:
    """Run a dispatcher. Since 0.55 these are Lua calls, e.g.
    hl.dsp.window.set_prop({...})."""
    result = request(f"dispatch {lua}").strip()
    if result.startswith("error:") or result == "unknown request":
        raise HyprError(f"dispatch failed: {result}  <-- {lua}")
    return result


def _lua_numeric_literal(text: str) -> str:
    r"""Encode a string as a Lua literal of pure \ddd escapes.

    The control socket mangles some characters in a request -- a bare `-`
    anywhere in the payload is enough to come back as "unknown request", and
    Lua source is full of them (every comment starts with `--`). Rather than
    guess the full set, encode every byte, leaving a payload of nothing but
    digits, backslashes and quotes.
    """
    return '"' + "".join(f"\\{b:03d}" for b in text.encode()) + '"'


def compile_lua(source: str) -> str | None:
    """Compile-check Lua in Hyprland's own VM. Returns an error, or None if ok.

    Hyprland's Lua runtime is the only authority on whether a chunk parses, and
    `load` is side-effect free -- it never runs the chunk.
    """
    probe = (
        f"local f, e = load({_lua_numeric_literal(source)}, 't') "
        "if f then return '' else return tostring(e) end"
    )
    try:
        result = request(f"repl {probe}").strip()
    except HyprError:
        return None  # no repl available -- skip rather than block the save
    if result == "unknown request":
        return None  # could not validate; do not claim the config is broken
    return result or None


# Lua serialiser used by parse_window_rules. Written without comments or any
# `-` character, because those break the socket request (see above).
_SERIALIZE = (
    "local function ser(v) "
    "  local t = type(v) "
    "  if t == 'string' then return '\"' .. v:gsub('[\\\\\"]', '\\\\%0')"
    ":gsub('\\n','\\\\n') .. '\"' end "
    "  if t == 'number' or t == 'boolean' then return tostring(v) end "
    "  if t == 'table' then "
    "    local out = {} "
    "    if #v > 0 then "
    "      for _, x in ipairs(v) do out[#out+1] = ser(x) end "
    "      return '[' .. table.concat(out, ',') .. ']' end "
    "    for k, x in pairs(v) do "
    "      out[#out+1] = '\"' .. tostring(k) .. '\":' .. ser(x) end "
    "    return '{' .. table.concat(out, ',') .. '}' "
    "  end "
    "  return 'null' "
    "end "
)


def parse_window_rules(source: str) -> list[dict]:
    """Read `hl.window_rule{}` calls out of Lua source.

    Runs the chunk in Hyprland's own VM against a sandboxed environment whose
    `hl.window_rule` just records its argument, so the real config is never
    touched. Using the actual Lua parser beats hand-rolling one: it handles
    variables, concatenation and any syntax a user's file might contain.
    """
    if "window_rule" not in source:
        return []
    # The environment is deliberately tiny. Callers pass only extracted rule
    # calls, but a rule value could still be `os.getenv(...)` or worse, and a
    # read should never be able to execute anything.
    probe = (
        _SERIALIZE
        + "local safe = { string = string, table = table, math = math, "
        "tostring = tostring, tonumber = tonumber, type = type, "
        "pairs = pairs, ipairs = ipairs, select = select } "
        "local got = {} "
        "safe.hl = setmetatable("
        "{ window_rule = function(t) got[#got+1] = t end }, "
        "{ __index = function() return function() end end }) "
        "local env = setmetatable(safe, { __index = function() return nil end }) "
        f"local f, e = load({_lua_numeric_literal(source)}, 'r', 't', env) "
        "if not f then return 'ERR' end "
        "local ok = pcall(f) "
        "if not ok then return 'ERR' end "
        "return ser(got)"
    )
    try:
        raw = request(f"repl {probe}").strip()
    except HyprError:
        return []
    if not raw or raw.startswith("ERR") or raw == "unknown request":
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [r for r in parsed if isinstance(r, dict)]


def clients() -> list[dict]:
    return request_json("clients")


def monitors() -> list[dict]:
    return request_json("monitors")


def version_string() -> str:
    return request("version").splitlines()[0] if request("version") else ""


def reload() -> None:
    request("reload config-only")


def selectable_windows() -> list[dict]:
    """Windows the user could plausibly be pointing at.

    Restricted to each monitor's active workspace, plus any special workspace
    currently open -- a special workspace floats above the normal one, so
    omitting it makes the picker unable to target scratchpad windows.
    """
    visible: set[int] = set()
    for mon in monitors():
        active = mon.get("activeWorkspace") or {}
        if isinstance(active.get("id"), int):
            visible.add(active["id"])
        special = mon.get("specialWorkspace") or {}
        # id 0 means "no special workspace open on this monitor"
        if isinstance(special.get("id"), int) and special["id"] != 0:
            visible.add(special["id"])

    out = []
    for win in clients():
        if not win.get("mapped") or win.get("hidden"):
            continue
        if (win.get("workspace") or {}).get("id") not in visible:
            continue
        w, h = win.get("size", [0, 0])
        if w <= 0 or h <= 0:
            continue
        out.append(win)
    return out

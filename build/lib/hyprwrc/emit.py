"""Render a Rule as config text.

Two dialects:
  lua  -- hl.window_rule{}, the syntax for 0.55+ (hyprlang is deprecated)
  conf -- the 0.54-era `windowrule { match:class = ... }` block form

Lua is the default. The conf emitter exists because plenty of live configs are
still on it and a half-migrated setup should not be forced to pick a side.
"""

from __future__ import annotations

from . import catalog
from .model import Rule

DIALECTS = ("lua", "conf")


def _lua_string(value: str) -> str:
    """Quote for a Lua double-quoted literal.

    Backslashes must double: the RE2 pattern `^foo\\.bar$` has to survive Lua's
    own string parsing to reach the regex engine intact.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _split_vec2(value) -> list[str]:
    if isinstance(value, (list, tuple)):
        parts = [str(v).strip() for v in value]
    else:
        parts = [p for p in str(value).replace(",", " ").split(" ") if p]
    return parts[:2]


def _lua_value(f: catalog.Field, value) -> str:
    if f.kind == "bool":
        return "true" if value else "false"
    if f.kind in ("int",):
        return str(int(value))
    if f.kind == "number":
        return str(float(value))
    if f.kind == "vec2":
        parts = _split_vec2(value)
        if not parts:
            return "{}"
        # Mixed forms like {"monitor_w-920", 20} are inconsistent to read and
        # invite confusion about which side is evaluated. If either component
        # is an expression, quote both.
        if all(_is_number(p) for p in parts):
            return "{" + ", ".join(parts) + "}"
        return "{" + ", ".join(_lua_string(p) for p in parts) + "}"
    return _lua_string(str(value))


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def _conf_value(f: catalog.Field, value) -> str:
    if f.kind == "bool":
        return "on" if value else "off"
    if f.kind == "vec2":
        return " ".join(_split_vec2(value))
    return str(value)


def to_lua(rule: Rule, indent: str = "    ") -> str:
    lines = ["hl.window_rule({"]
    if rule.name:
        lines.append(f"{indent}name = {_lua_string(rule.name)},")

    if rule.match:
        lines.append(f"{indent}match = {{")
        for key, value in rule.match.items():
            f = catalog.PROP_BY_KEY[key]
            lines.append(f"{indent*2}{key} = {_lua_value(f, value)},")
        lines.append(f"{indent}}},")
    else:
        # Half-built rules get rendered too, so this has to read sensibly
        # rather than leaving an empty pair of braces on two lines.
        lines.append(f"{indent}match = {{}},")

    for key, value in rule.effects.items():
        f = catalog.EFFECT_BY_KEY[key]
        lines.append(f"{indent}{key} = {_lua_value(f, value)},")

    lines.append("})")
    return "\n".join(lines)


def to_conf(rule: Rule, indent: str = "    ") -> str:
    lines = ["windowrule {"]
    if rule.name:
        lines.append(f"{indent}name = {rule.name}")
    for key, value in rule.match.items():
        f = catalog.PROP_BY_KEY[key]
        lines.append(f"{indent}match:{key} = {_conf_value(f, value)}")
    for key, value in rule.effects.items():
        f = catalog.EFFECT_BY_KEY[key]
        lines.append(f"{indent}{key} = {_conf_value(f, value)}")
    lines.append("}")
    return "\n".join(lines)


def render(rule: Rule, dialect: str = "lua") -> str:
    if dialect == "lua":
        return to_lua(rule)
    if dialect == "conf":
        return to_conf(rule)
    raise ValueError(f"unknown dialect: {dialect!r}")


def detect_dialect(config_dir) -> str:
    """Guess which dialect a config tree is written in.

    A tree with a hyprland.lua entrypoint is on the new syntax even if legacy
    .conf files are still lying around, which is exactly what a half-finished
    migration looks like.
    """
    from pathlib import Path

    config_dir = Path(config_dir)
    if (config_dir / "hyprland.lua").exists():
        return "lua"
    if (config_dir / "hyprland.conf").exists():
        return "conf"
    return "lua"

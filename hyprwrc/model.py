"""The rule being built, plus RE2 helpers for turning window properties into
matchers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import catalog

# RE2 metacharacters. Escaping is deliberately conservative -- over-escaping a
# literal is harmless, under-escaping silently changes what the rule matches.
_RE2_META = r"\.+*?()|[]{}^$"


def escape_re2(literal: str) -> str:
    return "".join("\\" + c if c in _RE2_META else c for c in literal)


def anchored(literal: str) -> str:
    """Exact-match pattern for a literal window property.

    `com.mitchellh.ghostty` -> `^com\\.mitchellh\\.ghostty$`
    Without escaping, those dots are wildcards and the rule matches more than
    the user picked.
    """
    return f"^{escape_re2(literal)}$"


def looks_literal(pattern: str) -> bool:
    """True if the string contains no unescaped RE2 metacharacters."""
    return not re.search(r"(?<!\\)[.+*?()|\[\]{}^$]", pattern)


@dataclass
class Rule:
    name: str | None = None
    match: dict[str, object] = field(default_factory=dict)
    effects: dict[str, object] = field(default_factory=dict)

    def is_valid(self) -> tuple[bool, str]:
        if not self.match:
            return False, "a rule needs at least one match field"
        if not self.effects:
            return False, "a rule needs at least one effect"
        for key in self.match:
            if key not in catalog.PROP_BY_KEY:
                return False, f"unknown match field: {key}"
        for key in self.effects:
            if key not in catalog.EFFECT_BY_KEY:
                return False, f"unknown effect: {key}"
        return True, ""

    def previewable(self) -> dict[str, object]:
        """Effects that can be shown on an already-open window."""
        return {
            k: v for k, v in self.effects.items()
            if catalog.EFFECT_BY_KEY[k].preview
        }

    def unpreviewable(self) -> list[str]:
        return [
            k for k in self.effects
            if not catalog.EFFECT_BY_KEY[k].preview
        ]


# --------------------------------------------------------------------------
# Multiple alternatives for one match field
#
# Hyprland forbids repeating a field inside `match` ("there is only one of one
# type"), so several alternatives for the same prop have to become a single
# RE2 alternation. That gives OR. It does not give AND: RE2 has no lookahead,
# so there is no way to demand that one string match two patterns at once --
# and AND across *different* fields is already what `match` does, since every
# prop must match. Verified against Hyprland 0.56: both `(^a$|^b$)` and
# `^(a|b)$` match as expected.
# --------------------------------------------------------------------------

def _split_top_level(pattern: str, sep: str = "|") -> list[str]:
    """Split on `sep` only at nesting depth zero, respecting escapes."""
    parts, depth, buf, i = [], 0, [], 0
    while i < len(pattern):
        c = pattern[i]
        if c == "\\" and i + 1 < len(pattern):
            buf.append(pattern[i:i + 2])
            i += 2
            continue
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        if c == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(c)
        i += 1
    parts.append("".join(buf))
    return parts


def combine_alternation(parts: list[str]) -> str:
    """Join several patterns into one that matches any of them."""
    parts = [p.strip() for p in parts if p and p.strip()]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    # If every alternative is anchored the same way, hoist the anchors out --
    # `^(a|b)$` is what a person would have written by hand.
    inner = []
    for p in parts:
        if not (p.startswith("^") and p.endswith("$") and len(p) > 2):
            inner = []
            break
        inner.append(p[1:-1])
    if inner and all(len(_split_top_level(p)) == 1 for p in inner):
        return "^(" + "|".join(inner) + ")$"
    return "(" + "|".join(parts) + ")"


def split_alternation(pattern: str) -> list[str]:
    """Inverse of combine_alternation, for loading a rule back into the form.

    Conservative: anything it cannot take apart cleanly comes back as a single
    value, so an exotic hand-written regex is never silently mangled.
    """
    pattern = (pattern or "").strip()
    if not pattern:
        return [""]

    # ^(a|b)$
    if pattern.startswith("^(") and pattern.endswith(")$"):
        inner = pattern[2:-2]
        parts = _split_top_level(inner)
        if len(parts) > 1 and all(parts):
            return [f"^{p}$" for p in parts]

    # (a|b) with each alternative carrying its own anchors
    if pattern.startswith("(") and pattern.endswith(")"):
        inner = pattern[1:-1]
        parts = _split_top_level(inner)
        if len(parts) > 1 and all(parts):
            return parts

    return [pattern]


def rule_from_parsed(parsed: dict) -> Rule:
    """Turn a rule read back out of config into an editable Rule.

    Unknown keys are dropped: they are either from a newer Hyprland than this
    catalog knows, or a typo Hyprland silently ignored. Either way the form
    cannot represent them, and the raw editor is the way to touch them.
    """
    rule = Rule(name=parsed.get("name"))
    for key, value in (parsed.get("match") or {}).items():
        if key in catalog.PROP_BY_KEY:
            rule.match[key] = _coerce(catalog.PROP_BY_KEY[key], value)
    for key, value in parsed.items():
        if key in ("match", "name"):
            continue
        if key in catalog.EFFECT_BY_KEY:
            rule.effects[key] = _coerce(catalog.EFFECT_BY_KEY[key], value)
    return rule


def unknown_keys(parsed: dict) -> list[str]:
    """Keys in a parsed rule this build has no field for."""
    out = [k for k in (parsed.get("match") or {}) if k not in catalog.PROP_BY_KEY]
    out += [k for k in parsed
            if k not in ("match", "name") and k not in catalog.EFFECT_BY_KEY]
    return sorted(out)


def _coerce(f: "catalog.Field", value):
    if f.kind == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "on", "true", "yes")
        return bool(value)
    if f.kind == "vec2":
        if isinstance(value, (list, tuple)):
            return tuple(str(v) for v in value[:2])
        parts = [p for p in str(value).replace(",", " ").split() if p]
        return tuple(parts[:2]) if len(parts) >= 2 else ("", "")
    if f.kind in ("int", "number"):
        try:
            return int(value) if f.kind == "int" else float(value)
        except (TypeError, ValueError):
            return 0
    return str(value)


def suggest_match(window: dict) -> dict[str, object]:
    """Opening guess at a matcher for a picked window.

    Prefers initial_class over class: `class` can drift after map (a browser
    rewrites it rarely, but Electron apps do), and static effects are evaluated
    against the initial values anyway -- the wiki is explicit that matching on
    `class`/`title` for a static effect actually sees `initialClass`/
    `initialTitle`. Title is deliberately left out: it is usually volatile.
    """
    initial = (window.get("initialClass") or "").strip()
    current = (window.get("class") or "").strip()
    chosen = initial or current
    if not chosen:
        return {}
    return {"initial_class" if initial else "class": anchored(chosen)}


def title_is_volatile(window: dict) -> bool:
    """Whether the live title has already drifted from the one at map time."""
    return (window.get("title") or "") != (window.get("initialTitle") or "")

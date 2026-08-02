"""Reuse an existing rule for the window in the editor.

Two operations, both starting from a rule that already exists somewhere in the
config:

  clone  -- keep what the rule *does*, re-aim it at this window
  extend -- leave the rule where it is, add this window to what it matches

Extending is the interesting one. It edits a rule in place, and the rule may
live in a file this tool did not write, so the edit is deliberately surgical:
only the quoted value of one match field is replaced, and only when it can be
located exactly once. Everything else -- formatting, comments, field order, any
key this build has no form field for -- is left byte-for-byte alone. When the
value cannot be pinned down unambiguously the splice refuses rather than
guessing, and the caller offers a clone instead.
"""

from __future__ import annotations

import re

from . import catalog, emit, model, scan

# Match fields that identify *which* window, as opposed to what state it is in.
# Only these are worth extending: adding a window to `float = true` would say
# nothing about the window that was picked.
#
# `tag` is excluded even though it names a window: Hyprland compares tags
# literally, not as RE2, so an alternation there would silently match nothing.
IDENTITY_PROPS = ("class", "initial_class", "title", "initial_title", "xdg_tag")

# prop key -> the field `hyprctl clients` reports it under.
WINDOW_KEY = {
    "class": "class",
    "initial_class": "initialClass",
    "title": "title",
    "initial_title": "initialTitle",
    "xdg_tag": "xdgTag",
}


def window_value(window: dict, prop: str) -> str:
    return (window.get(WINDOW_KEY[prop]) or "").strip()


def identity_fields(rule: dict) -> list[str]:
    """Identity match fields this rule actually uses, in catalog order."""
    match = rule.get("match") or {}
    return [k for k in IDENTITY_PROPS if k in match and str(match[k]).strip()]


def extendable_fields(rule: dict, window: dict) -> list[str]:
    """Identity fields we could add this window to.

    A field the window has no value for is dropped: extending `initial_title`
    with an empty string would produce a pattern matching everything.
    """
    return [k for k in identity_fields(rule) if window_value(window, k)]


def already_covered(pattern: str, literal: str) -> bool:
    """True if the rule's existing pattern already matches this window."""
    return scan._regex_matches(str(pattern), literal)


def extended_pattern(pattern: str, literal: str) -> str | None:
    """`pattern` widened to also match `literal`. None if it already does."""
    if already_covered(pattern, literal):
        return None
    parts = model.split_alternation(str(pattern))
    return model.combine_alternation(parts + [model.anchored(literal)])


# ---------------------------------------------------------------------------
# Cloning
# ---------------------------------------------------------------------------

def clone_for_window(parsed: dict, window: dict) -> model.Rule:
    """A new rule doing what `parsed` does, aimed at `window`.

    Identity matchers are replaced, not merged: the point of a clone is a
    separate rule for a different window, and carrying the original's class
    across would make the copy fight with the rule it came from.

    State matchers (`xwayland`, `float`, `content`, ...) are kept. They express
    a condition the author cared about -- "only when floating" -- that is just
    as true for the new target, and dropping them would quietly widen the rule.
    """
    rule = model.rule_from_parsed(parsed)
    kept = {k: v for k, v in rule.match.items() if k not in IDENTITY_PROPS}
    rule.match = {**model.suggest_match(window), **kept}
    if rule.name:
        rule.name = f"{rule.name} copy"
    return rule


# ---------------------------------------------------------------------------
# Surgical in-place edit of one match value
# ---------------------------------------------------------------------------

def _lua_match_span(source: str) -> tuple[int, int] | None:
    """Offsets of the body of the `match = { ... }` table.

    Scoped on purpose: several effect names double as match props (`tag`,
    `content`, `workspace`), so a search across the whole call could rewrite an
    effect instead of the matcher.
    """
    m = re.search(r"\bmatch\s*=\s*\{", source)
    if not m:
        return None
    depth, i = 0, m.end() - 1
    quote = None
    while i < len(source):
        c = source[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return m.end(), i
        i += 1
    return None


def splice_lua(source: str, key: str, new_value: str) -> str | None:
    """Replace the quoted value of `key` inside the match table.

    Returns None when the assignment is not found exactly once -- a rule built
    from a variable, a `[\"class\"]` subscript, or anything else this cannot
    read is left for the user to edit by hand.
    """
    span = _lua_match_span(source)
    if span is None:
        return None
    start, stop = span
    body = source[start:stop]

    pattern = re.compile(
        rf"(?<![\w.]){re.escape(key)}(\s*=\s*)(\"(?:[^\"\\]|\\.)*\")")
    hits = list(pattern.finditer(body))
    if len(hits) != 1:
        return None

    hit = hits[0]
    replaced = body[:hit.start()] + key + hit.group(1) + \
        emit._lua_string(new_value) + body[hit.end():]
    return source[:start] + replaced + source[stop:]


def splice_conf(source: str, key: str, new_value: str) -> str | None:
    """Same, for the legacy `match:class = ^foo$` block form."""
    pattern = re.compile(rf"^([ \t]*match:{re.escape(key)}[ \t]*=[ \t]*)(.*)$",
                         re.M)
    hits = list(pattern.finditer(source))
    if len(hits) != 1:
        return None
    hit = hits[0]
    return source[:hit.start()] + hit.group(1) + new_value + source[hit.end():]


def splice(source: str, key: str, new_value: str, dialect: str) -> str | None:
    if dialect == "conf":
        return splice_conf(source, key, new_value)
    return splice_lua(source, key, new_value)


# ---------------------------------------------------------------------------
# The whole operation, decided before anything is written
# ---------------------------------------------------------------------------

class Refused(Exception):
    """The edit cannot be made safely. The message is shown to the user."""


def plan_extend(parsed: dict, source: str, window: dict, prop: str,
                dialect: str) -> tuple[str, str]:
    """Work out the rewritten source for adding `window` to a rule.

    Returns (new_source, new_pattern). Raises Refused with a reason a person
    can act on, rather than writing something approximate.
    """
    if prop not in (parsed.get("match") or {}):
        raise Refused(f"this rule does not match on {prop}")

    literal = window_value(window, prop)
    if not literal:
        raise Refused(f"this window reports no {catalog.PROP_BY_KEY[prop].doc.lower()}")

    old = str((parsed.get("match") or {})[prop])
    new_pattern = extended_pattern(old, literal)
    if new_pattern is None:
        raise Refused(f"the rule's {prop} already matches “{literal}”")

    spliced = splice(source, prop, new_pattern, dialect)
    if spliced is None:
        raise Refused(
            f"could not find a plain {prop} = \"…\" to widen in the rule's "
            "source, so it was left alone")
    return spliced, new_pattern

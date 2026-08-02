"""Tests for reusing an existing rule: cloning it, and widening it to also
match another window.

The splice tests carry the weight here. That code edits files the tool did not
write, so "it changed exactly one quoted value and nothing else" is the whole
safety argument, and "it refused rather than guessing" is the other half.

Run: python3 -m pytest tests/ -q     (or: python3 tests/test_merge.py)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hyprwrc import merge  # noqa: E402
from hyprwrc.merge import Refused  # noqa: E402


WINDOW = {
    "class": "Alacritty",
    "initialClass": "Alacritty",
    "title": "zsh — ~/dev",
    "initialTitle": "Alacritty",
    "xdgTag": "",
}


# -- which fields can be extended ------------------------------------------

def test_identity_fields_ignores_state_matchers():
    rule = {"match": {"class": "^kitty$", "float": True, "xwayland": False}}
    assert merge.identity_fields(rule) == ["class"]


def test_tag_is_not_extendable():
    # Hyprland compares tags literally, so an alternation there matches
    # nothing. Offering to widen it would produce a silently dead rule.
    rule = {"match": {"tag": "term"}}
    assert merge.identity_fields(rule) == []


def test_field_the_window_has_no_value_for_is_dropped():
    rule = {"match": {"xdg_tag": "^foo$"}}
    assert merge.identity_fields(rule) == ["xdg_tag"]
    assert merge.extendable_fields(rule, WINDOW) == []


def test_multiple_identity_fields_all_offered():
    rule = {"match": {"class": "^kitty$", "title": "^vim$"}}
    assert merge.extendable_fields(rule, WINDOW) == ["class", "title"]


# -- widening a pattern ----------------------------------------------------

def test_extend_single_value():
    assert merge.extended_pattern("^kitty$", "Alacritty") == "^(kitty|Alacritty)$"


def test_extend_existing_alternation():
    assert (merge.extended_pattern("^(kitty|foot)$", "Alacritty")
            == "^(kitty|foot|Alacritty)$")


def test_extend_escapes_the_new_literal():
    out = merge.extended_pattern("^kitty$", "com.mitchellh.ghostty")
    assert out == r"^(kitty|com\.mitchellh\.ghostty)$"


def test_already_covered_returns_none():
    assert merge.extended_pattern("^Alacritty$", "Alacritty") is None
    assert merge.extended_pattern("^(kitty|Alacritty)$", "Alacritty") is None
    # A loose pattern that happens to cover it counts too -- adding an
    # alternative would change nothing.
    assert merge.extended_pattern("^Alac.*$", "Alacritty") is None


# -- the Lua splice --------------------------------------------------------

LUA = '''hl.window_rule({
    name = "terminals",
    match = {
        class = "^kitty$",   -- my terminal
        float = true,
    },
    opacity = "0.95",
})'''


def test_splice_lua_changes_only_the_value():
    out = merge.splice_lua(LUA, "class", "^(kitty|Alacritty)$")
    assert out is not None
    assert 'class = "^(kitty|Alacritty)$"' in out
    # Everything else survives byte for byte.
    assert "-- my terminal" in out
    assert 'name = "terminals"' in out
    assert "float = true," in out
    assert 'opacity = "0.95"' in out
    assert out.replace('^(kitty|Alacritty)$', '^kitty$') == LUA


def test_splice_lua_escapes_backslashes():
    out = merge.splice_lua(LUA, "class", r"^(kitty|com\.foo)$")
    # The RE2 backslash has to survive Lua's own string parsing.
    assert r'class = "^(kitty|com\\.foo)$"' in out


def test_splice_lua_ignores_effects_outside_the_match_table():
    # `tag` and `content` are both a match prop and an effect. Rewriting the
    # effect instead of the matcher would silently change what the rule does.
    src = '''hl.window_rule({
    match = { content = "game" },
    content = "video",
})'''
    out = merge.splice_lua(src, "content", "photo")
    assert 'match = { content = "photo" }' in out
    assert 'content = "video",' in out


def test_splice_lua_refuses_when_absent():
    assert merge.splice_lua(LUA, "title", "^x$") is None


def test_splice_lua_refuses_a_subscript_form():
    # `["class"]` is valid Lua this cannot rewrite in place. Refusing sends the
    # user to a hand edit instead of mangling their file.
    src = 'hl.window_rule({ match = { ["class"] = "^kitty$" } })'
    assert merge.splice_lua(src, "class", "^x$") is None


def test_splice_lua_refuses_when_ambiguous():
    src = '''hl.window_rule({
    match = { class = "^a$", initial_class = "^b$" },
})'''
    # A naive search for `class` would hit initial_class too; the word boundary
    # keeps them apart, so this stays a clean single match.
    out = merge.splice_lua(src, "class", "^(a|z)$")
    assert 'class = "^(a|z)$"' in out
    assert 'initial_class = "^b$"' in out


def test_splice_lua_survives_braces_inside_a_regex():
    src = 'hl.window_rule({ match = { title = "^a{2}$" }, float = true })'
    out = merge.splice_lua(src, "title", "^(a{2}|b)$")
    assert 'title = "^(a{2}|b)$"' in out
    assert "float = true" in out


# -- the conf splice -------------------------------------------------------

CONF = """windowrule {
    name = terminals
    match:class = ^kitty$
    float = on
}"""


def test_splice_conf():
    out = merge.splice_conf(CONF, "class", "^(kitty|Alacritty)$")
    assert "match:class = ^(kitty|Alacritty)$" in out
    assert "float = on" in out
    assert "name = terminals" in out


def test_splice_conf_refuses_when_absent():
    assert merge.splice_conf(CONF, "title", "^x$") is None


# -- the whole planned edit ------------------------------------------------

def _parsed():
    return {"name": "terminals", "match": {"class": "^kitty$"}, "opacity": "0.95"}


def test_plan_extend_returns_source_and_pattern():
    source, pattern = merge.plan_extend(_parsed(), LUA, WINDOW, "class", "lua")
    assert pattern == "^(kitty|Alacritty)$"
    assert 'class = "^(kitty|Alacritty)$"' in source


def test_plan_extend_refuses_when_already_matching():
    parsed = {"match": {"class": "^Alacritty$"}}
    try:
        merge.plan_extend(parsed, 'hl.window_rule({ match = { class = "^Alacritty$" } })',
                          WINDOW, "class", "lua")
    except Refused as exc:
        assert "already matches" in str(exc)
    else:
        raise AssertionError("should have refused")


def test_plan_extend_refuses_when_source_cannot_be_read():
    parsed = {"match": {"class": "^kitty$"}}
    src = 'hl.window_rule({ match = { ["class"] = "^kitty$" } })'
    try:
        merge.plan_extend(parsed, src, WINDOW, "class", "lua")
    except Refused as exc:
        assert "could not find" in str(exc)
    else:
        raise AssertionError("should have refused")


def test_plan_extend_refuses_a_field_the_rule_does_not_use():
    try:
        merge.plan_extend(_parsed(), LUA, WINDOW, "title", "lua")
    except Refused as exc:
        assert "does not match on title" in str(exc)
    else:
        raise AssertionError("should have refused")


# -- cloning ---------------------------------------------------------------

def test_clone_retargets_identity_and_keeps_effects():
    rule = merge.clone_for_window(_parsed(), WINDOW)
    assert rule.effects == {"opacity": "0.95"}
    assert rule.match == {"initial_class": "^Alacritty$"}
    assert rule.name == "terminals copy"


def test_clone_keeps_state_matchers():
    parsed = {"match": {"class": "^kitty$", "xwayland": True}, "float": True}
    rule = merge.clone_for_window(parsed, WINDOW)
    # The class is replaced, but "only when XWayland" was a deliberate
    # condition and is just as meaningful for the new target.
    assert rule.match == {"initial_class": "^Alacritty$", "xwayland": True}
    assert "class" not in rule.match


def test_clone_drops_every_identity_field():
    parsed = {"match": {"class": "^a$", "title": "^b$", "initial_title": "^c$"},
              "float": True}
    rule = merge.clone_for_window(parsed, WINDOW)
    assert set(rule.match) == {"initial_class"}


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {name}")
            except AssertionError as exc:
                failures += 1
                print(f"  FAIL {name}: {exc}")
    print(f"\n{failures} failure(s)")
    sys.exit(1 if failures else 0)

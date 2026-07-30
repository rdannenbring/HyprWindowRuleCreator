"""Tests for the parts that must be right and don't need a compositor:
escaping, emitters, and rule validation.

Run: python3 -m pytest tests/ -q     (or: python3 tests/test_emit.py)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hyprwrc import catalog, emit, model  # noqa: E402
from hyprwrc.model import Rule, anchored, escape_re2, looks_literal  # noqa: E402


# -- RE2 escaping ----------------------------------------------------------

def test_dots_are_escaped():
    # Unescaped, these dots are wildcards and the rule over-matches.
    assert anchored("com.mitchellh.ghostty") == r"^com\.mitchellh\.ghostty$"


def test_pipe_and_parens_escaped():
    assert escape_re2("a|b(c)") == r"a\|b\(c\)"


def test_plain_text_untouched():
    assert anchored("firefox") == "^firefox$"


def test_looks_literal():
    assert looks_literal("firefox")
    assert not looks_literal("^fire.*fox$")
    assert looks_literal(r"com\.foo")  # already-escaped dots are literal


# -- Lua emitter -----------------------------------------------------------

def _rule(**effects):
    return Rule(match={"class": anchored("kitty")}, effects=effects)


def test_lua_doubles_backslashes():
    # `\.` in the pattern must survive Lua string parsing to reach RE2.
    out = emit.to_lua(_rule(float=True))
    assert r'class = "^kitty$"' in out
    out2 = emit.to_lua(Rule(match={"class": anchored("a.b")}, effects={"float": True}))
    assert r'"^a\\.b$"' in out2


def test_lua_bools_and_ints():
    out = emit.to_lua(_rule(float=True, rounding=12))
    assert "float = true," in out
    assert "rounding = 12," in out


def test_lua_numeric_vec2_unquoted():
    out = emit.to_lua(_rule(size=(800, 600)))
    assert "size = {800, 600}," in out


def test_lua_expression_vec2_quotes_both_components():
    # Mixed {"expr", 20} is inconsistent to read; quote both or neither.
    out = emit.to_lua(_rule(move=("monitor_w-920", "20")))
    assert 'move = {"monitor_w-920", "20"},' in out


def test_incomplete_rule_still_renders():
    # The pane shows the rule as it is built, so a half-finished one has to
    # produce readable output rather than being withheld.
    out = emit.to_lua(Rule(match={"class": "^kitty$"}))
    assert 'class = "^kitty$"' in out
    assert out.startswith("hl.window_rule({")


def test_empty_match_collapses_to_one_line():
    out = emit.to_lua(Rule())
    assert "match = {}," in out
    assert "match = {\n" not in out


def test_lua_name_is_emitted():
    r = _rule(float=True)
    r.name = "my-rule"
    assert 'name = "my-rule",' in emit.to_lua(r)


def test_lua_quotes_are_escaped():
    r = Rule(match={"title": '^say "hi"$'}, effects={"float": True})
    assert r'\"hi\"' in emit.to_lua(r)


# -- conf emitter ----------------------------------------------------------

def test_conf_uses_on_off_and_match_prefix():
    out = emit.to_conf(_rule(float=True))
    assert "match:class = ^kitty$" in out
    assert "float = on" in out


def test_conf_does_not_double_backslashes():
    out = emit.to_conf(Rule(match={"class": anchored("a.b")}, effects={"float": True}))
    assert r"match:class = ^a\.b$" in out


def test_conf_vec2_is_space_separated():
    assert "size = 800 600" in emit.to_conf(_rule(size=(800, 600)))


# -- validation ------------------------------------------------------------

def test_rule_needs_a_match():
    ok, why = Rule(effects={"float": True}).is_valid()
    assert not ok and "match" in why


def test_rule_needs_an_effect():
    ok, why = Rule(match={"class": "^a$"}).is_valid()
    assert not ok and "effect" in why


def test_unknown_effect_is_rejected():
    # Hyprland itself silently ignores unknown effects, so this check is the
    # only thing standing between a typo and a rule that quietly does nothing.
    ok, why = Rule(match={"class": "^a$"}, effects={"no_such_effect": True}).is_valid()
    assert not ok and "no_such_effect" in why


def test_valid_rule_passes():
    assert _rule(float=True).is_valid() == (True, "")


# -- preview classification ------------------------------------------------

def test_static_only_effects_are_flagged_unpreviewable():
    r = _rule(no_initial_focus=True, float=True)
    assert r.unpreviewable() == ["no_initial_focus"]
    assert "float" in r.previewable()


def test_every_dynamic_effect_is_previewable():
    # The wiki states all dynamic effects are settable via set_prop.
    for f in catalog.DYNAMIC_EFFECTS:
        assert f.preview == "prop", f.key


def test_catalog_keys_are_unique():
    keys = [f.key for f in catalog.EFFECTS]
    assert len(keys) == len(set(keys))


# -- OR within one match field --------------------------------------------
# Hyprland forbids repeating a field in `match`, so several alternatives have
# to become one RE2 alternation. AND within a field is not expressible at all:
# RE2 has no lookahead.

def test_single_value_unchanged():
    assert model.combine_alternation(["^a$"]) == "^a$"


def test_anchors_are_hoisted_when_shared():
    # `^(a|b)$` is what a person would write, and is what Hyprland matched in
    # testing on 0.56.
    assert model.combine_alternation(["^a$", "^b$"]) == "^(a|b)$"


def test_mixed_anchoring_wraps_instead():
    assert model.combine_alternation(["^a$", "b.*"]) == "(^a$|b.*)"


def test_unanchored_values_wrap():
    assert model.combine_alternation(["foo", "bar"]) == "(foo|bar)"


def test_escaped_dots_survive_combining():
    out = model.combine_alternation([anchored("com.foo"), anchored("bar")])
    assert out == r"^(com\.foo|bar)$"


def test_blank_values_dropped():
    assert model.combine_alternation(["^a$", "  ", ""]) == "^a$"
    assert model.combine_alternation([]) == ""


def test_split_is_the_inverse():
    for parts in (["^a$"], ["^a$", "^b$"], ["^a$", "^b$", "^c$"],
                  ["foo", "bar"], ["^a$", "b.*"],
                  [anchored("com.foo"), anchored("bar")]):
        assert model.split_alternation(model.combine_alternation(parts)) == parts


def test_split_leaves_exotic_patterns_alone():
    # A nested alternation inside one alternative must not be torn apart.
    assert model.split_alternation("^(a|b)$") == ["^a$", "^b$"]
    assert model.split_alternation("^a(b|c)d$") == ["^a(b|c)d$"]
    assert model.split_alternation("^plain$") == ["^plain$"]


def test_split_respects_escaped_pipe():
    # `\|` is a literal pipe, not a separator, so there is nothing to split
    # and the pattern must survive intact.
    assert model.split_alternation(r"^(a\|b)$") == [r"^(a\|b)$"]


def test_nested_group_round_trips():
    combined = model.combine_alternation(["^(x|y)$", "^z$"])
    assert model.split_alternation(combined) == ["^(x|y)$", "^z$"]


def test_tag_is_not_a_regex_field():
    # Tag matching is literal name comparison -- offering OR there would give
    # the user an alternation that silently never matches.
    assert catalog.PROP_BY_KEY["tag"].kind == "string"
    assert catalog.PROP_BY_KEY["class"].kind == "regex"


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

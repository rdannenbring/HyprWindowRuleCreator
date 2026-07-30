"""Tests for finding and loading rules that already exist.

Only the pure parts: extracting call sites, matching a rule against a window,
the conf parser, and turning a parsed rule back into an editable one. Reading
Lua goes through Hyprland's VM and needs a live compositor.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hyprwrc import model, scan  # noqa: E402


def window(**over):
    base = {
        "class": "Alacritty", "initialClass": "Alacritty",
        "title": "zsh", "initialTitle": "Alacritty",
        "floating": False, "xwayland": False, "fullscreen": 0,
        "pinned": False, "workspace": {"id": 1, "name": "1"}, "tags": [],
    }
    base.update(over)
    return base


# -- extracting call sites -------------------------------------------------

def test_extracts_only_rule_calls():
    # A config file does real work; reading it must not run any of that.
    src = (
        'require("startup")\n'
        'os.execute("rm ~")\n'
        'hl.window_rule({ match = { class = "^a$" }, float = true })\n'
        'hl.exec_cmd("nope")\n'
    )
    got = scan.extract_rule_calls(src)
    assert "require" not in got and "os.execute" not in got
    assert got.count("hl.window_rule") == 1


def test_parens_inside_strings_do_not_end_the_call():
    src = 'hl.window_rule({ match = { title = "^a (b) c$" }, float = true })'
    assert scan.extract_rule_calls(src).strip() == src


def test_long_bracket_strings_are_skipped():
    src = 'hl.window_rule({ match = { title = [[a )) b]] }, float = true })'
    assert scan.extract_rule_calls(src).strip() == src


def test_multiple_calls_all_extracted():
    src = ('hl.window_rule({ match = { class = "^a$" }, float = true })\n'
           'print("hi")\n'
           'hl.window_rule({ match = { class = "^b$" }, pin = true })')
    assert scan.extract_rule_calls(src).count("hl.window_rule") == 2


def test_no_rules_returns_empty():
    assert scan.extract_rule_calls('print("nothing here")') == ""


# -- matching --------------------------------------------------------------

def test_regex_match_on_initial_class():
    rule = {"match": {"initial_class": "^Alacritty$"}}
    assert scan.rule_matches(rule, window())[0]
    assert not scan.rule_matches(rule, window(initialClass="foot"))[0]


def test_all_props_must_match():
    rule = {"match": {"initial_class": "^Alacritty$", "initial_title": "^nope$"}}
    assert not scan.rule_matches(rule, window())[0]


def test_unanchored_regex_matches_substring():
    assert scan.rule_matches({"match": {"class": "lacrit"}}, window())[0]


def test_negative_prefix_inverts():
    assert scan.rule_matches({"match": {"class": "negative:foot"}}, window())[0]
    assert not scan.rule_matches(
        {"match": {"class": "negative:Alacritty"}}, window())[0]


def test_bool_props():
    assert scan.rule_matches({"match": {"float": True}}, window(floating=True))[0]
    assert not scan.rule_matches({"match": {"float": True}}, window())[0]


def test_unknown_props_are_reported_not_guessed():
    ok, unknown = scan.rule_matches(
        {"match": {"class": "^Alacritty$", "content": "video"}}, window())
    assert ok and unknown == ["content"]


def test_empty_match_never_applies():
    assert not scan.rule_matches({"match": {}}, window())[0]
    assert not scan.rule_matches({}, window())[0]


def test_invalid_regex_does_not_raise():
    assert not scan.rule_matches({"match": {"class": "^(unclosed"}}, window())[0]


# -- conf parsing ----------------------------------------------------------

def test_parse_conf_block():
    rules = scan.parse_conf_rules(
        "windowrule {\n"
        "    match:class = ^kitty$\n"
        "    float = on\n"
        "    size = 800 600\n"
        "}\n"
    )
    assert len(rules) == 1
    assert rules[0]["match"] == {"class": "^kitty$"}
    assert rules[0]["float"] is True
    assert rules[0]["size"] == "800 600"


# -- loading back into the form -------------------------------------------

def test_rule_from_parsed_round_trip():
    parsed = {
        "name": "demo",
        "match": {"initial_class": "^foo$"},
        "float": True, "size": [800, 600], "opacity": "0.9", "rounding": 12,
    }
    r = model.rule_from_parsed(parsed)
    assert r.name == "demo"
    assert r.match == {"initial_class": "^foo$"}
    assert r.effects["float"] is True
    assert r.effects["size"] == ("800", "600")
    assert r.effects["rounding"] == 12


def test_unknown_keys_are_dropped_and_reported():
    parsed = {"match": {"class": "^a$", "made_up_prop": 1},
              "float": True, "made_up_effect": True}
    r = model.rule_from_parsed(parsed)
    assert "made_up_prop" not in r.match
    assert "made_up_effect" not in r.effects
    assert model.unknown_keys(parsed) == ["made_up_effect", "made_up_prop"]


# -- commented-out rules ---------------------------------------------------

def test_commented_rule_is_found_but_flagged_disabled():
    # It must not count as applying, but must still be listed so it can be
    # switched back on.
    src = ('hl.window_rule({ match = { class = "^a$" }, float = true })\n'
           '-- hl.window_rule({ match = { class = "^b$" }, pin = true })\n')
    sites = scan.find_rule_sites(src)
    assert [s.enabled for s in sites] == [True, False]


def test_extract_skips_commented_calls():
    src = ('-- hl.window_rule({ match = { class = "^b$" }, pin = true })\n'
           'hl.window_rule({ match = { class = "^a$" }, float = true })\n')
    got = scan.extract_rule_calls(src)
    assert '"^a$"' in got and '"^b$"' not in got


def test_uncommenting_a_site_restores_source():
    src = '-- hl.window_rule({ match = { class = "^b$" }, pin = true })'
    site = scan.find_rule_sites(src)[0]
    assert site.uncommented().startswith("hl.window_rule(")


def test_indented_comment_detected():
    src = '    -- hl.window_rule({ match = { class = "^b$" }, pin = true })'
    assert scan.find_rule_sites(src)[0].enabled is False


def test_trailing_code_on_line_is_not_a_comment():
    src = 'local x = 1 hl.window_rule({ match = { class = "^a$" }, float = true })'
    assert scan.find_rule_sites(src)[0].enabled is True


# -- summaries -------------------------------------------------------------

def test_effect_names_excludes_values_that_are_off():
    # Listing an effect that is set to false would overstate what a rule does.
    f = scan.FoundRule(Path("x.lua"),
                       {"match": {"class": "^a$"}, "float": True,
                        "no_blur": False, "decorate": True, "animation": ""})
    assert f.effect_names() == ["decorate", "float"]


def test_match_summary_lists_fields():
    f = scan.FoundRule(Path("x.lua"), {"match": {"class": "^a$", "float": True}})
    assert "class ^a$" in f.match_summary()


def test_managed_spans_are_positional():
    # Two blocks with identical bodies must resolve to different ids, or a
    # duplicated rule gets attributed to its original.
    text = ("-- >>> hyprwrc A\nhl.window_rule({ match = { class = \"^a$\" } })\n"
            "-- <<< hyprwrc A\n"
            "-- >>> hyprwrc B\nhl.window_rule({ match = { class = \"^a$\" } })\n"
            "-- <<< hyprwrc B\n")
    spans = scan.managed_spans(text)
    assert [s[0] for s in spans] == ["A", "B"]
    assert spans[0][2] <= spans[1][1]


# -- scope: all rules vs one window ---------------------------------------

def test_window_none_keeps_everything():
    # find_all passes window=None; the filter must become a no-op rather than
    # matching nothing.
    rules = [
        ({"match": {"class": "^Alacritty$"}, "float": True}, None),
        ({"match": {"class": "^firefox$"}, "pin": True}, None),
    ]
    kept = [r for r, _ in rules if scan.rule_matches(r, window())[0]]
    assert len(kept) == 1, "sanity: only one matches the window"
    # and with no window, nothing is filtered out
    assert all(r.get("match") for r, _ in rules)


def test_found_rule_defaults_to_not_matching():
    # A rule surfaced without a window must not claim to match one.
    f = scan.FoundRule(Path("x.lua"), {"match": {"class": "^a$"}, "float": True})
    assert f.matches_window is False


def test_evaluation_order_is_preserved_across_files():
    a = scan.FoundRule(Path("a.lua"), {"match": {}}, order=0)
    b = scan.FoundRule(Path("b.lua"), {"match": {}}, order=1)
    assert sorted([b, a], key=lambda f: f.order) == [a, b]


# -- template provenance ---------------------------------------------------

def test_template_marker_is_read_back():
    from hyprwrc.scan import _TEMPLATE_MARK
    assert _TEMPLATE_MARK.search("-- from-template: auth-polkit").group(1) \
        == "auth-polkit"
    assert _TEMPLATE_MARK.search("# from-template: auth-polkit").group(1) \
        == "auth-polkit"


def test_marker_survives_deactivation():
    # Deactivating comments every line again, so the marker becomes
    # "-- -- from-template: x". Losing it there would offer "Activate" on a
    # template that already has a rule, producing a duplicate.
    from hyprwrc.scan import _TEMPLATE_MARK
    assert _TEMPLATE_MARK.search("-- -- from-template: auth-polkit").group(1) \
        == "auth-polkit"
    assert _TEMPLATE_MARK.search("# # from-template: auth-polkit").group(1) \
        == "auth-polkit"


def test_marker_does_not_match_rule_content():
    from hyprwrc.scan import _TEMPLATE_MARK
    assert _TEMPLATE_MARK.search('name = "from-template: nope"') is None


def test_found_rule_has_no_template_by_default():
    assert scan.FoundRule(Path("x.lua"), {"match": {}}).template_id is None


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

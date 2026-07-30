"""Template store, shipped-set sanity, and the override model."""

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hyprwrc import catalog, emit  # noqa: E402
from hyprwrc.model import Rule  # noqa: E402
from hyprwrc.templates import Template, TemplateStore, slugify  # noqa: E402


def store():
    tmp = Path(tempfile.mkdtemp())
    return TemplateStore(tmp / "templates.json"), tmp


# -- the shipped set -------------------------------------------------------

def test_builtins_load():
    st, tmp = store()
    try:
        assert len(st.all()) >= 10
    finally:
        shutil.rmtree(tmp)


def test_builtin_ids_are_unique():
    st, tmp = store()
    try:
        ids = [t.id for t in st.all()]
        assert len(ids) == len(set(ids))
    finally:
        shutil.rmtree(tmp)


def test_every_builtin_uses_known_fields():
    # A template referencing a field this build has no widget for would load
    # silently short of what it claims to do.
    st, tmp = store()
    try:
        for t in st.all():
            assert t.unknown_keys() == [], f"{t.id}: {t.unknown_keys()}"
    finally:
        shutil.rmtree(tmp)


def test_every_builtin_has_effects_and_a_description():
    st, tmp = store()
    try:
        for t in st.all():
            assert t.effects, f"{t.id} sets nothing"
            assert t.description, f"{t.id} has no description"
    finally:
        shutil.rmtree(tmp)


def test_builtins_render_as_valid_rules():
    st, tmp = store()
    try:
        for t in st.all():
            if not t.is_directly_usable():
                continue
            rule = t.to_rule()
            assert rule.is_valid()[0], t.id
            assert "hl.window_rule({" in emit.render(rule, "lua")
    finally:
        shutil.rmtree(tmp)


def test_template_without_match_is_not_directly_usable():
    # It is a starting point; activating it would produce a rule matching
    # nothing, which Hyprland would reject anyway.
    st, tmp = store()
    try:
        t = st.get("float-and-centre")
        assert t.match == {}
        assert not t.is_directly_usable()
    finally:
        shutil.rmtree(tmp)


# -- user templates and overrides -----------------------------------------

def test_save_and_reload():
    st, tmp = store()
    try:
        st.save(Template(id="mine", title="Mine",
                         match={"class": "^a$"}, effects={"float": True}))
        got = st.get("mine")
        assert got.title == "Mine" and got.builtin is False
    finally:
        shutil.rmtree(tmp)


def test_user_copy_shadows_builtin_without_touching_it():
    st, tmp = store()
    try:
        original = st.get("steam-dialogs")
        st.save(Template(id="steam-dialogs", title="Changed",
                         match=dict(original.match), effects={"pin": True}))
        now = st.get("steam-dialogs")
        assert now.title == "Changed"
        assert now.builtin and now.overridden
        # Deleting the override restores the shipped version rather than
        # leaving a hole in the list.
        assert st.delete("steam-dialogs")
        restored = st.get("steam-dialogs")
        assert restored.title == original.title
        assert restored.effects == original.effects
        assert not restored.overridden
    finally:
        shutil.rmtree(tmp)


def test_deleting_an_untouched_builtin_does_nothing():
    st, tmp = store()
    try:
        assert st.delete("auth-polkit") is False
        assert st.get("auth-polkit") is not None
    finally:
        shutil.rmtree(tmp)


def test_unique_id_avoids_collisions():
    st, tmp = store()
    try:
        first = st.unique_id("auth-polkit")
        assert first != "auth-polkit"
        st.save(Template(id=first, title="x"))
        assert st.unique_id("auth-polkit") not in ("auth-polkit", first)
    finally:
        shutil.rmtree(tmp)


def test_corrupt_file_falls_back_to_builtins():
    st, tmp = store()
    try:
        st.path.parent.mkdir(parents=True, exist_ok=True)
        st.path.write_text("{ not json")
        assert len(st.all()) >= 10
    finally:
        shutil.rmtree(tmp)


def test_entries_without_an_id_are_ignored():
    st, tmp = store()
    try:
        st.path.parent.mkdir(parents=True, exist_ok=True)
        st.path.write_text(json.dumps([{"title": "no id here"}]))
        assert all(t.id for t in st.all())
    finally:
        shutil.rmtree(tmp)


# -- conversion ------------------------------------------------------------

def test_from_rule_round_trip():
    rule = Rule(name="n", match={"class": "^a$"},
                effects={"float": True, "size": ("800", "600")})
    t = Template.from_rule(rule, "My template", "why")
    assert t.match == rule.match
    back = t.to_rule()
    assert back.effects["float"] is True
    assert back.effects["size"] == ("800", "600")


def test_to_rule_drops_unknown_keys():
    t = Template(id="x", title="x",
                 match={"class": "^a$", "from_the_future": 1},
                 effects={"float": True, "warp_drive": True})
    rule = t.to_rule()
    assert "from_the_future" not in rule.match
    assert "warp_drive" not in rule.effects
    assert t.unknown_keys() == ["from_the_future", "warp_drive"]


def test_vec2_survives_json():
    st, tmp = store()
    try:
        st.save(Template(id="v", title="v", match={"class": "^a$"},
                         effects={"size": ("800", "600")}))
        assert st.get("v").to_rule().effects["size"] == ("800", "600")
    finally:
        shutil.rmtree(tmp)


def test_slugify():
    assert slugify("Password & GPG prompts") == "password-gpg-prompts"
    assert slugify("!!!") == "template"


def test_builtin_regexes_are_anchored():
    # An unanchored class pattern quietly matches far more than intended.
    st, tmp = store()
    try:
        for t in st.all():
            for key, value in t.match.items():
                if catalog.PROP_BY_KEY.get(key) and \
                        catalog.PROP_BY_KEY[key].kind == "regex":
                    assert value.startswith("^") and value.endswith("$"), \
                        f"{t.id}.{key} is unanchored: {value}"
    finally:
        shutil.rmtree(tmp)


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

"""Naming, and which names are safe to change.

The point of these is not that the current values are right — it is that the
two kinds of name stay separated. APP_NAME is display text and can be changed
freely before release. FENCE_TAG and CONFIG_DIR_NAME are written to users'
disks, and changing them without a migration orphans data.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hyprwrc import branding, scan, store  # noqa: E402

SRC = Path(__file__).resolve().parent.parent / "hyprwrc"


def test_outside_app_uses_the_display_name():
    assert branding.APP_NAME in branding.outside_app()
    assert branding.outside_app() == f"defined outside {branding.APP_NAME}"


def test_fence_tag_is_used_for_markers():
    begin, end = store.BEGIN.format(id="X"), store.END.format(id="X")
    assert branding.FENCE_TAG in begin and branding.FENCE_TAG in end
    assert begin.startswith("-- >>>") and end.startswith("-- <<<")


def test_fence_regexes_agree_between_store_and_scan():
    # Two modules parse the same markers. If they drift, rules written by one
    # stop being found by the other.
    text = (f"-- >>> {branding.FENCE_TAG} ABC\n"
            "hl.window_rule({ match = { class = \"^a$\" } })\n"
            f"-- <<< {branding.FENCE_TAG} ABC\n")
    assert scan.managed_spans(text), "scan cannot see its own fence"
    found = re.compile(
        rf"^[-#]+ >>> {re.escape(branding.FENCE_TAG)} (\S+)\s*$", re.M
    ).findall(text)
    assert found == ["ABC"]


def test_display_name_is_not_hardcoded_in_ui_strings():
    # A stray literal would survive a rename and go out of step with the rest.
    for name in ("ui.py", "settings_ui.py", "templates_ui.py", "cli.py"):
        text = (SRC / name).read_text()
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""'):
                continue
            if '"' not in line and "'" not in line:
                continue
            assert "HyprWindowRuleCreator" not in line, f"{name}: {stripped}"


def test_renaming_the_display_name_does_not_touch_stored_names():
    # The whole reason the constants are split.
    original = branding.APP_NAME
    try:
        branding.APP_NAME = "SomethingElse"
        assert branding.outside_app() == "defined outside SomethingElse"
        assert branding.FENCE_TAG == "hyprwrc"
        assert branding.CONFIG_DIR_NAME == "hyprwrc"
    finally:
        branding.APP_NAME = original


def test_config_dir_name_drives_settings_path():
    from hyprwrc.settings import settings_path
    assert settings_path().parent.name == branding.CONFIG_DIR_NAME


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

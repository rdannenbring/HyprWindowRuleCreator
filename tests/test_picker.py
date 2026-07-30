"""Hit-test / tiebreak tests for the at-cursor mode.

Pure geometry, so no compositor needed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hyprwrc.picker import choose_at_point  # noqa: E402


def win(addr, x, y, w, h, ws=1, fh=0):
    return {
        "address": addr, "at": [x, y], "size": [w, h],
        "workspace": {"id": ws}, "focusHistoryID": fh,
    }


def test_single_hit():
    a = win("a", 0, 0, 100, 100)
    assert choose_at_point([a], 50, 50) is a


def test_miss_returns_none():
    assert choose_at_point([win("a", 0, 0, 100, 100)], 500, 500) is None


def test_edges_are_half_open():
    a = win("a", 0, 0, 100, 100)
    assert choose_at_point([a], 0, 0) is a        # top-left inclusive
    assert choose_at_point([a], 100, 50) is None  # right edge exclusive
    assert choose_at_point([a], 99, 99) is a


def test_active_window_wins_overlap():
    # follow_mouse means the compositor already knows which one is hovered.
    a = win("a", 0, 0, 200, 200, fh=5)
    b = win("b", 50, 50, 200, 200, fh=3)
    assert choose_at_point([a, b], 100, 100, active_address="a") is a
    assert choose_at_point([a, b], 100, 100, active_address="b") is b


def test_special_workspace_beats_normal():
    # Special workspaces render above the normal one.
    normal = win("n", 0, 0, 200, 200, ws=1, fh=0)
    special = win("s", 0, 0, 200, 200, ws=-98, fh=9)
    assert choose_at_point([normal, special], 10, 10) is special


def test_focus_history_breaks_remaining_ties():
    older = win("old", 0, 0, 200, 200, fh=7)
    newer = win("new", 0, 0, 200, 200, fh=1)
    assert choose_at_point([older, newer], 10, 10) is newer


def test_active_beats_special():
    # An explicit compositor answer outranks our layering heuristic.
    special = win("s", 0, 0, 200, 200, ws=-98, fh=9)
    normal = win("n", 0, 0, 200, 200, ws=1, fh=0)
    assert choose_at_point([special, normal], 10, 10, active_address="n") is normal


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

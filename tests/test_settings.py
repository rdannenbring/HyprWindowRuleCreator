"""Settings validation and backup retention."""

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hyprwrc import store  # noqa: E402
from hyprwrc.settings import Settings  # noqa: E402


# -- validation ------------------------------------------------------------

def test_defaults_sort_last():
    # The zz- prefix is what makes "Make Active" mean anything.
    assert Settings().sorts_last()


def test_absolute_path_rejected():
    s = Settings(generated_file="/etc/hypr/evil.lua")
    s.validate()
    assert s.generated_file == "conf.d/zz-windowrule-generated.lua"


def test_parent_traversal_rejected():
    s = Settings(generated_file="../../escape.lua")
    s.validate()
    assert not s.generated_file.startswith("..")


def test_empty_path_falls_back():
    s = Settings(generated_file="   ")
    s.validate()
    assert s.generated_file.endswith(".lua")


def test_relative_path_allowed():
    s = Settings(generated_file="conf.d/my-rules.conf")
    s.validate()
    assert s.generated_file == "conf.d/my-rules.conf"
    assert s.suffix() == "conf"
    assert not s.sorts_last()


def test_bad_dialect_falls_back_to_auto():
    s = Settings(dialect="klingon")
    s.validate()
    assert s.dialect == "auto"


def test_negative_backup_count_clamped():
    s = Settings(backup_keep=-5)
    s.validate()
    assert s.backup_keep == 0


def test_non_numeric_backup_count_falls_back():
    s = Settings(backup_keep="lots")
    s.validate()
    assert s.backup_keep == 10


# -- persistence -----------------------------------------------------------

def test_round_trip():
    tmp = Path(tempfile.mkdtemp())
    try:
        path = tmp / "settings.json"
        s = Settings.load(path)
        s.backup_keep = 3
        s.generated_file = "conf.d/custom.lua"
        s.save()
        again = Settings.load(path)
        assert again.backup_keep == 3
        assert again.generated_file == "conf.d/custom.lua"
    finally:
        shutil.rmtree(tmp)


def test_corrupt_file_yields_defaults():
    tmp = Path(tempfile.mkdtemp())
    try:
        path = tmp / "settings.json"
        path.write_text("{not json at all")
        assert Settings.load(path).backup_keep == 10
    finally:
        shutil.rmtree(tmp)


def test_unknown_keys_ignored():
    tmp = Path(tempfile.mkdtemp())
    try:
        path = tmp / "settings.json"
        path.write_text(json.dumps({"backup_keep": 4, "from_the_future": True}))
        s = Settings.load(path)
        assert s.backup_keep == 4
        assert not hasattr(s, "from_the_future")
    finally:
        shutil.rmtree(tmp)


# -- backup retention ------------------------------------------------------

def _make_backups(target: Path, n: int):
    target.write_text("x")
    for i in range(n):
        target.with_suffix(target.suffix + f".bak.2026010{i}-000000").write_text("x")


def test_prune_keeps_newest():
    tmp = Path(tempfile.mkdtemp())
    try:
        target = tmp / "rules.lua"
        _make_backups(target, 6)
        assert len(store.backups_for(target)) == 6
        store.prune_backups(target, 2)
        left = store.backups_for(target)
        assert len(left) == 2
        # sorted newest-first by timestamp in the name
        assert "20260105" in left[0].name and "20260104" in left[1].name
    finally:
        shutil.rmtree(tmp)


def test_prune_zero_keeps_everything():
    tmp = Path(tempfile.mkdtemp())
    try:
        target = tmp / "rules.lua"
        _make_backups(target, 4)
        store.prune_backups(target, 0)
        assert len(store.backups_for(target)) == 4
    finally:
        shutil.rmtree(tmp)


def test_backup_name_collision_within_one_second():
    tmp = Path(tempfile.mkdtemp())
    try:
        target = tmp / "rules.lua"
        target.write_text("x")
        a = store._write_backup(target)
        b = store._write_backup(target)
        assert a != b, "two writes in the same second must not overwrite"
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

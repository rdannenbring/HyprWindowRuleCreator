"""Whether the generated file is actually read by the config.

The bug this guards against is silent by construction: the rule is written,
it compiles, Hyprland reloads, `configerrors` is empty -- and nothing applies,
because no file in the config tree ever opens the one the rule went into. So
these tests care most about the two ways of being wrong. Saying "loaded" when
nothing loads it puts the silence straight back; saying "not loaded" when a
loader is right there sends people to edit a config that was already fine.

No compositor needed: the Lua is read, never run.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hyprwrc import reach  # noqa: E402

# The loader as it is actually written into a config, with the directory left
# open so a test can point it at a temp tree.
LOADER = """\
do
  local pipe = io.popen('ls -1 "' .. {dir} .. '"/*.lua 2>/dev/null')
  if pipe then
    for path in pipe:lines() do dofile(path) end
    pipe:close()
  end
end
"""


class Tree:
    """A throwaway config dir, with HOME and XDG_CONFIG_HOME pointed at it.

    The env matters: `os.getenv("HOME")` in a loader and `require("hypr.x")`
    both resolve through it, and a test that left the real HOME in place would
    be reading the developer's own config.
    """

    def __init__(self, dialect="lua"):
        self.root = Path(tempfile.mkdtemp())
        self.dir = self.root / ".config" / "hypr"
        (self.dir / "conf.d").mkdir(parents=True)
        self.dialect = dialect
        suffix = "lua" if dialect == "lua" else "conf"
        self.target = self.dir / "conf.d" / f"zz-windowrule-generated.{suffix}"
        self.target.write_text("-- generated\n")
        self._saved = {k: os.environ.get(k) for k in ("HOME", "XDG_CONFIG_HOME")}
        os.environ["HOME"] = str(self.root)
        os.environ["XDG_CONFIG_HOME"] = str(self.root / ".config")

    def write(self, name, text):
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def entry(self, text):
        name = "hyprland.lua" if self.dialect == "lua" else "hyprland.conf"
        return self.write(name, text)

    def loader(self, expr=None):
        return LOADER.format(
            dir=expr or 'os.getenv("HOME") .. "/.config/hypr/conf.d"')

    def check(self):
        return reach.check(self.dir, self.target, self.dialect)

    def close(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def tree(dialect="lua"):
    return Tree(dialect)


# -- the case that started this --------------------------------------------

def test_stock_omarchy_entrypoint_does_not_reach_conf_d():
    # An explicit require list and no glob: exactly what ships, and exactly
    # what makes every generated rule inert without a word of complaint.
    t = tree()
    try:
        t.entry('dofile((os.getenv("OMARCHY_PATH") or "/usr/share/omarchy")'
                ' .. "/default/hypr/bootstrap.lua")\n'
                'require("default.hypr.omarchy")\n'
                'require("hypr.monitors")\n'
                'require("hypr.bindings")\n')
        t.write("monitors.lua", "-- personal\n")
        t.write("bindings.lua", "-- personal\n")
        result = t.check()
        assert not result.loaded
        assert result.kind == "none"
        assert "hyprland.lua" in result.summary()
        assert result.summary().startswith("NO")
    finally:
        t.close()


def test_the_loader_snippet_is_recognised():
    t = tree()
    try:
        t.entry('require("hypr.monitors")\n' + t.loader())
        result = t.check()
        assert result.loaded, result.summary()
        assert result.kind == "glob"
        assert result.via == t.dir / "hyprland.lua"
        assert result.line == 3      # the io.popen line
        assert "io.popen" in result.evidence
    finally:
        t.close()


def test_generated_snippet_is_recognised_by_the_checker():
    # The fix the app hands out has to satisfy the check the app runs, or it
    # tells people to add something and then keeps warning them.
    t = tree()
    try:
        t.entry("-- nothing here\n")
        assert not t.check().loaded
        entry, backup = reach.install_loader(t.dir, t.target, "lua")
        assert entry == t.dir / "hyprland.lua"
        assert backup is not None and backup.exists()
        assert t.check().loaded
    finally:
        t.close()


# -- not being fooled ------------------------------------------------------

def test_commented_out_loader_does_not_count():
    t = tree()
    try:
        t.entry("\n".join("-- " + ln for ln in t.loader().splitlines()))
        assert not t.check().loaded
    finally:
        t.close()


def test_block_commented_loader_does_not_count():
    t = tree()
    try:
        t.entry("--[[\n" + t.loader() + "]]\n")
        assert not t.check().loaded
    finally:
        t.close()


def test_prose_about_conf_d_does_not_count():
    # The generated file's own header talks about a conf.d/*.lua glob. A
    # comment describing the loader is the most likely false positive there is.
    t = tree()
    try:
        t.entry("-- Rules live in conf.d/*.lua, loaded by the glob below.\n"
                "-- (which was never actually added)\n"
                'require("hypr.monitors")\n')
        t.write("monitors.lua", "-- personal\n")
        assert not t.check().loaded
    finally:
        t.close()


def test_a_path_without_a_loader_call_does_not_count():
    t = tree()
    try:
        t.entry('local junk = os.getenv("HOME") .. "/.config/hypr/conf.d"'
                '/*.lua"\nprint(junk)\n')
        assert not t.check().loaded
    finally:
        t.close()


def test_missing_entrypoint_is_reported_as_unknown_not_as_loaded():
    t = tree()
    try:
        result = t.check()
        assert not result.loaded
        assert result.kind == "no-entrypoint"
        assert result.summary().startswith("UNKNOWN")
    finally:
        t.close()


# -- the other shapes a loader comes in ------------------------------------

def test_direct_dofile_of_the_target_counts():
    t = tree()
    try:
        t.entry(f'dofile("{t.target}")\n')
        result = t.check()
        assert result.loaded and result.kind == "direct"
    finally:
        t.close()


def test_relative_dofile_of_the_target_counts():
    t = tree()
    try:
        t.entry('dofile("conf.d/zz-windowrule-generated.lua")\n')
        assert t.check().loaded
    finally:
        t.close()


def test_glob_assembled_through_a_local_counts():
    t = tree()
    try:
        t.entry('local dir = os.getenv("HOME") .. "/.config/hypr/conf.d"\n'
                'for f in io.popen("ls " .. dir .. "/*.lua"):lines() do\n'
                '  dofile(f)\n'
                'end\n')
        assert t.check().loaded
    finally:
        t.close()


def test_require_all_files_on_the_directory_counts():
    # Omarchy's own idiom: hand a directory to a helper that globs it.
    t = tree()
    try:
        t.entry('local require_all = require("default.hypr.require_all")\n'
                'require_all.files(os.getenv("HOME") .. '
                '"/.config/hypr/conf.d", nil)\n')
        assert t.check().loaded
    finally:
        t.close()


def test_loader_reached_through_a_require_counts():
    # The walk has to follow requires, or a config that keeps its loader in a
    # tidy module gets told it has none.
    t = tree()
    try:
        t.entry('require("hypr.extras")\n')
        t.write("extras.lua", t.loader())
        result = t.check()
        assert result.loaded
        assert result.via == t.dir / "extras.lua"
    finally:
        t.close()


def test_loader_reached_through_a_dofile_counts():
    t = tree()
    try:
        t.entry(f'dofile("{t.dir}/extras.lua")\n')
        t.write("extras.lua", t.loader())
        assert t.check().loaded
    finally:
        t.close()


def test_a_require_cycle_terminates():
    t = tree()
    try:
        t.entry('require("hypr.a")\n')
        t.write("a.lua", 'require("hypr.b")\n')
        t.write("b.lua", 'require("hypr.a")\n')
        assert not t.check().loaded      # the point is that it returns at all
    finally:
        t.close()


# -- hyprlang ---------------------------------------------------------------

def test_conf_source_glob_counts():
    t = tree("conf")
    try:
        t.entry("source = conf.d/*.conf\n")
        result = t.check()
        assert result.loaded and result.kind == "glob"
    finally:
        t.close()


def test_commented_conf_source_does_not_count():
    t = tree("conf")
    try:
        t.entry("# source = conf.d/*.conf\n")
        assert not t.check().loaded
    finally:
        t.close()


def test_conf_source_of_another_file_that_sources_the_glob_counts():
    t = tree("conf")
    try:
        t.entry("source = extra.conf\n")
        t.write("extra.conf", "source = conf.d/*.conf\n")
        assert t.check().loaded
    finally:
        t.close()


# -- the snippet itself -----------------------------------------------------

def test_snippet_globs_lua_so_it_skips_the_backups():
    # Backups are written as `.lua.bak.<stamp>`, which a *.lua glob does not
    # match. If that ever changes, every save would load its own history.
    t = tree()
    try:
        snippet = reach.loader_snippet(t.target, "lua")
        assert "*.lua" in snippet
        backup = t.target.with_suffix(t.target.suffix + ".bak.20260101-000000")
        assert not backup.name.endswith(".lua")
    finally:
        t.close()


def test_snippet_points_at_the_configured_directory():
    t = tree()
    try:
        assert "/.config/hypr/conf.d" in reach.loader_snippet(t.target, "lua")
        assert 'os.getenv("HOME")' in reach.loader_snippet(t.target, "lua")
    finally:
        t.close()


def test_snippet_outside_home_uses_an_absolute_path():
    t = tree()
    try:
        # HOME is the temp root; a target elsewhere cannot be written relative
        # to it, and a snippet that silently pointed at $HOME would be wrong.
        os.environ["HOME"] = "/nonexistent-home"
        snippet = reach.loader_snippet(t.target, "lua")
        assert str(t.target.parent) in snippet
        assert 'os.getenv("HOME")' not in snippet
    finally:
        t.close()


def test_conf_snippet_is_a_source_line():
    t = tree("conf")
    try:
        assert reach.loader_snippet(t.target, "conf").strip() == \
            "source = conf.d/*.conf"
    finally:
        t.close()


def test_install_refuses_when_there_is_no_entrypoint():
    t = tree()
    try:
        try:
            reach.install_loader(t.dir, t.target, "lua")
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("appended a loader to a file that is not there")
    finally:
        t.close()


# -- how it reads -----------------------------------------------------------

def test_summary_leads_with_the_verdict():
    t = tree()
    try:
        t.entry("-- nothing\n")
        bad = t.check()
        assert bad.verdict() == "NO"
        assert bad.summary().startswith("NO — ")
        assert bad.explanation()

        t.entry(t.loader())
        good = t.check()
        assert good.verdict() == "yes"
        assert good.explanation() == ""
        assert "hyprland.lua:" in good.where()
    finally:
        t.close()


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

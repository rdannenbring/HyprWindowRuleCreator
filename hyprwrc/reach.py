"""Is the generated file actually read by the running config?

Writing a rule correctly and having it apply are different things. The rules
this tool writes go into a drop-in file under `conf.d/`, which only works if
something in the config tree loads that directory. A stock Omarchy
`hyprland.lua` does not: it has an explicit `require("hypr.monitors")` list and
no glob. Every generated rule is then written, parsed as valid Lua by the
pre-flight check, reloaded, and never read -- and `hyprctl configerrors` stays
clean precisely because the file is never opened. Nothing anywhere reports a
problem, so the tool looks broken.

So the config gets read the same way Hyprland reads it: start at the
entrypoint, follow `require` and `dofile` to the files it pulls in, and look
for something that would reach the target -- a directory glob covering it, or
a load of it by name.

The Lua is not executed, and is not fully parsed either. Comments are stripped
(a commented-out loader must not count), `os.getenv` and simple locals are
substituted, string concatenation is glued back together, and what is left is
searched for a path that would reach the target. Heuristic on purpose: a
config file is a program, and the loader idioms in the wild are a handful of
one-liners rather than anything worth writing an interpreter for.
"""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

#: How far from a path reference a loader call may sit and still be credited.
#: The common idiom spreads over a few lines -- the glob is built on one, the
#: `dofile` runs in a loop below it -- so the reference alone is not enough and
#: the whole file is too loose.
LOADER_WINDOW = 6

_LOADER_CALL = re.compile(
    r"\b(dofile|loadfile|require|require_all\.files|source)\b")

#: `require("a.b")` -- read before quotes are stripped, since a module name is
#: only meaningful as a whole string.
_REQUIRE = re.compile(r"""\brequire\s*\(?\s*(["'])([\w.\-]+)\1""")

#: `source = conf.d/*.conf`, the hyprlang equivalent of the glob.
_SOURCE = re.compile(r"^\s*source\s*=\s*(.+?)\s*$", re.M)

#: Two adjacent string literals joined by `..`; removing the quotes and the
#: operator glues them into one path.
_CONCAT = re.compile(r"""["']\s*\.\.\s*["']""")

#: `local name = "value"` after flattening, for one round of inlining.
_LOCAL = re.compile(r"""^\s*local\s+([A-Za-z_]\w*)\s*=\s*(\S.*?)\s*$""", re.M)

#: A path-ish run of characters: anything that is not Lua punctuation.
_TOKEN = re.compile(r"[^\s;,()\[\]{}=]+")

#: Ceiling on how many files one walk will read. Following requires into
#: Omarchy's defaults is worth doing -- a future release could add the glob --
#: but not worth walking a whole distribution for.
MAX_FILES = 200


@dataclass
class Reach:
    """What the config tree does, or does not, do with the target file."""

    target: Path
    entrypoint: Path | None
    kind: str = "none"          # glob | direct | none | no-entrypoint
    via: Path | None = None     # the file holding the loader
    line: int = 0               # 1-based, in `via`
    evidence: str = ""          # the source line, as written
    scanned: list[Path] = field(default_factory=list)

    @property
    def loaded(self) -> bool:
        return self.kind in ("glob", "direct")

    def where(self) -> str:
        """`hyprland.lua:41`, for pointing at the loader that was found."""
        if not self.via:
            return ""
        return f"{self.via.name}:{self.line}"

    def detail(self) -> str:
        """What was found, without the verdict in front of it."""
        if self.kind == "glob":
            return (f"{self.target.parent.name}/*{self.target.suffix} glob "
                    f"at {self.where()}")
        if self.kind == "direct":
            return f"loaded by name at {self.where()}"
        if self.kind == "no-entrypoint":
            name = self.entrypoint.name if self.entrypoint else "hyprland.lua"
            return f"no {name} to read"
        entry = self.entrypoint.name if self.entrypoint else "your config"
        return f"nothing in {entry} reads {self.target.parent.name}/"

    def verdict(self) -> str:
        if self.loaded:
            return "yes"
        return "UNKNOWN" if self.kind == "no-entrypoint" else "NO"

    def summary(self) -> str:
        """One line, phrased the way the answer matters to the user."""
        return f"{self.verdict()} — {self.detail()}"

    def explanation(self) -> str:
        """Why an unloaded file is worse than an error, spelled out."""
        if self.loaded:
            return ""
        return ("Every rule in that file is inert: it is written and it is "
                "valid, but nothing loads it. Hyprland reports no error, "
                "because the file is never read.")


# -- reading Lua without running it ----------------------------------------

def _strip_comments(text: str) -> str:
    """Blank out Lua comments, keeping every byte offset and newline.

    Line numbers have to survive so the evidence can be pointed at, and a
    commented-out loader has to stop counting -- half the configs that look
    like they glob conf.d have the glob sitting behind a `--`.
    """
    out = list(text)
    i, n = 0, len(text)
    quote = None
    long_close = None

    while i < n:
        c = text[i]
        if long_close:                          # inside [[ ]] or --[[ ]]
            if text.startswith(long_close, i):
                i += len(long_close)
                long_close = None
                continue
        elif quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif text.startswith("--", i):
            m = re.match(r"--\[(=*)\[", text[i:])
            if m:                               # --[[ block comment ]]
                close = "]" + m.group(1) + "]"
                stop = text.find(close, i + len(m.group(0)))
                stop = n if stop == -1 else stop + len(close)
            else:                               # -- to end of line
                stop = text.find("\n", i)
                stop = n if stop == -1 else stop
            for j in range(i, stop):
                if out[j] != "\n":
                    out[j] = " "
            i = stop
            continue
        elif (m := re.match(r"\[(=*)\[", text[i:])):
            long_close = "]" + m.group(1) + "]"
            i += len(m.group(0))
            continue
        i += 1
    return "".join(out)


def _env() -> dict[str, str]:
    home = str(Path.home())
    return {
        "HOME": home,
        "XDG_CONFIG_HOME": os.environ.get("XDG_CONFIG_HOME", f"{home}/.config"),
        "OMARCHY_PATH": os.environ.get("OMARCHY_PATH", "/usr/share/omarchy"),
    }


def _substitute_env(text: str) -> str:
    """`os.getenv("HOME")` -> `"/home/you"`, so concatenation can be glued."""
    def repl(m):
        value = _env().get(m.group(2))
        return f'"{value}"' if value is not None else m.group(0)
    return re.sub(r"""os\.getenv\s*\(\s*(["'])(\w+)\1\s*\)""", repl, text)


def _glue(line: str) -> str:
    """Collapse a line's string expressions into bare, unquoted text.

    `io.popen('ls "' .. "/home/you" .. '/.config/hypr/conf.d"/*.lua')` becomes
    `io.popen(ls /home/you/.config/hypr/conf.d/*.lua)`. Crude, and that is the
    point: it does not matter how the path was assembled, only whether the
    assembled thing reaches the target.
    """
    prev = None
    while prev != line:
        prev = line
        line = _CONCAT.sub("", line)
    return line.replace('"', "").replace("'", "")


def _inline_locals(text: str) -> str:
    """One pass of `local dir = "..."` substitution.

    Enough for `local dir = home .. "/.config/hypr/conf.d"` followed by a glob
    built from `dir`, which is the other shape this loader is written in. One
    pass only: chains deeper than that are not worth chasing, and a missed
    substitution costs a false "not loaded", which is the safe direction.
    """
    values = {}
    for m in _LOCAL.finditer(text):
        glued = _glue(m.group(2))
        if "/" in glued and " " not in glued.strip():
            values[m.group(1)] = glued.strip()
    if not values:
        return text
    pattern = re.compile(r"\b(" + "|".join(map(re.escape, values)) + r")\b")
    return pattern.sub(lambda m: f'"{values[m.group(1)]}"', text)


def _flatten(text: str) -> list[str]:
    """Comment-free, env-expanded, concatenation-glued lines.

    Same number of lines as the input, so a hit can still be reported against
    the line the user would go and look at.
    """
    stripped = _substitute_env(_strip_comments(text))
    return [_glue(line) for line in _inline_locals(stripped).splitlines()]


# -- resolving what a line refers to ---------------------------------------

def _resolve(token: str, base: Path, need_sep: bool = True) -> Path | None:
    """A token as a path, relative tokens resolved against `base`.

    `need_sep` filters the Lua scan, which walks every token on a line and
    would otherwise treat bare identifiers as filenames. A value read from a
    `source =` line is already known to be a path, so that scan turns it off.
    """
    token = token.strip().rstrip("/")
    if not token or (need_sep and "/" not in token):
        return None
    if token.startswith("~"):
        token = str(Path.home()) + token[1:]
    path = Path(token)
    return path if path.is_absolute() else (base / path)


def _module_paths(module: str) -> list[Path]:
    """Where `require("hypr.monitors")` could resolve to.

    Mirrors the `package.path` Omarchy's bootstrap installs: user modules under
    `~/.config`, generated state under `~/.local/state`, defaults under
    `$OMARCHY_PATH`.
    """
    env = _env()
    rel = Path(*module.split("."))
    roots = [Path(env["XDG_CONFIG_HOME"]), Path.home() / ".local/state",
             Path(env["OMARCHY_PATH"])]
    out = []
    for root in roots:
        out.append(root / rel.with_suffix(".lua"))
        out.append(root / rel / "init.lua")
    return out


def _reaches(candidate: Path, target: Path) -> str:
    """How `candidate` relates to the target: direct, glob, dir, or "".

    A bare directory counts as a lead rather than a hit -- `require_all.files`
    is handed the directory and does the globbing itself -- so the caller still
    has to find a loader call beside it.
    """
    text = str(candidate)
    if text == str(target):
        return "direct"
    if any(ch in text for ch in "*?[") and fnmatch.fnmatch(str(target), text):
        return "glob"
    if text == str(target.parent):
        return "dir"
    return ""


def _scan_lua(path: Path, target: Path, config_dir: Path):
    """(hit, requires) for one Lua file.

    `hit` is (kind, line_no, source_line) or None; `requires` are the paths
    this file pulls in, for the walk to follow.
    """
    try:
        raw = path.read_text()
    except OSError:
        return None, []

    raw_lines = raw.splitlines()
    lines = _flatten(raw)
    base = path.parent

    hit = None
    for i, line in enumerate(lines):
        if hit:
            break
        for token in _TOKEN.findall(line):
            # A bare word is noise unless it carries the suffix being hunted;
            # anything with a separator in it is worth resolving either way.
            strict = "/" not in token and not token.endswith(target.suffix)
            candidate = (_resolve(token, base, strict)
                         or _resolve(token, config_dir, strict))
            if candidate is None:
                continue
            kind = _reaches(candidate, target)
            if not kind:
                # A relative token is only meaningful against the config dir.
                other = _resolve(token, config_dir, strict)
                kind = _reaches(other, target) if other else ""
                if not kind:
                    continue
            window = "\n".join(lines[max(0, i - LOADER_WINDOW):
                                     i + LOADER_WINDOW + 1])
            if not _LOADER_CALL.search(window):
                continue
            hit = ("direct" if kind == "direct" else "glob", i + 1,
                   raw_lines[i].strip() if i < len(raw_lines) else "")
            break

    requires: list[Path] = []
    stripped = _strip_comments(raw)
    for _, module in _REQUIRE.findall(stripped):
        requires.extend(_module_paths(module))
    for line in lines:
        if "dofile" not in line and "loadfile" not in line:
            continue
        for token in _TOKEN.findall(line):
            if token.endswith(".lua"):
                resolved = (_resolve(token, base, False)
                            or _resolve(token, config_dir, False))
                if resolved:
                    requires.append(resolved)
    return hit, requires


def _scan_conf(path: Path, target: Path, config_dir: Path):
    """The hyprlang equivalent: `source = conf.d/*.conf` and nothing else."""
    try:
        raw = path.read_text()
    except OSError:
        return None, []

    hit = None
    requires: list[Path] = []
    for i, line in enumerate(raw.splitlines()):
        code = line.split("#")[0]
        m = _SOURCE.match(code)
        if not m:
            continue
        candidate = _resolve(m.group(1), config_dir, need_sep=False)
        if candidate is None:
            continue
        kind = _reaches(candidate, target)
        if kind in ("direct", "glob") and hit is None:
            hit = (kind, i + 1, line.strip())
        if not any(ch in str(candidate) for ch in "*?["):
            requires.append(candidate)
        else:
            requires.extend(sorted(candidate.parent.glob(candidate.name)))
    return hit, requires


# -- the walk ---------------------------------------------------------------

def entrypoint_for(config_dir: Path, dialect: str = "lua") -> Path:
    name = "hyprland.lua" if dialect == "lua" else "hyprland.conf"
    return Path(config_dir) / name


def check(config_dir, target, dialect: str = "lua") -> Reach:
    """Walk the config from its entrypoint and decide whether `target` is read.

    Follows `require` and `dofile` outside the config dir too. Omarchy's
    defaults live in /usr/share/omarchy, and if a future release grows the glob
    the honest answer is that the file *is* loaded.
    """
    config_dir = Path(config_dir)
    target = Path(target)
    try:
        target = target.resolve()
    except OSError:
        pass

    entry = entrypoint_for(config_dir, dialect)
    result = Reach(target=target, entrypoint=entry)
    if not entry.exists():
        result.kind = "no-entrypoint"
        return result

    scan_one = _scan_lua if dialect == "lua" else _scan_conf
    seen: set[Path] = set()
    queue = [entry]

    while queue and len(seen) < MAX_FILES:
        path = queue.pop(0)
        try:
            path = path.resolve()
        except OSError:
            continue
        if path in seen or not path.is_file() or path == target:
            continue
        seen.add(path)
        result.scanned.append(path)

        hit, requires = scan_one(path, target, config_dir)
        if hit:
            result.kind, result.line, result.evidence = hit
            result.via = path
            return result
        queue.extend(requires)

    return result


# -- the fix ----------------------------------------------------------------

def loader_snippet(target, dialect: str = "lua") -> str:
    """The lines to add to the entrypoint so `target` gets read.

    Built from the real directory rather than hardcoded: the config dir moves
    with XDG_CONFIG_HOME, and a snippet that points somewhere else is worse
    than none. Kept HOME-relative when it can be, so it survives being copied
    between machines.
    """
    directory = Path(target).parent
    suffix = Path(target).suffix or ".lua"

    if dialect != "lua":
        try:
            rel = directory.relative_to(Path(target).parent.parent)
        except ValueError:  # pragma: no cover - parent is always a parent
            rel = directory
        return f"source = {rel}/*{suffix}\n"

    home = Path.home()
    try:
        quoted = f"""os.getenv("HOME") .. "/{directory.relative_to(home)}\""""
    except ValueError:
        quoted = f'"{directory}"'

    # Sorted so the `zz-` prefix keeps generated rules last, and thus winning.
    return (
        f"-- Load drop-in rule files from {directory.name}/. Sorted, so a\n"
        f"-- `zz-` prefix keeps generated rules last and thus winning.\n"
        f"do\n"
        f"""  local pipe = io.popen('ls -1 "' .. {quoted} .. '"/*{suffix}"""
        f""" 2>/dev/null')\n"""
        f"  if pipe then\n"
        f"    for path in pipe:lines() do dofile(path) end\n"
        f"    pipe:close()\n"
        f"  end\n"
        f"end\n"
    )


def install_loader(config_dir, target,
                   dialect: str = "lua") -> tuple[Path, Path | None]:
    """Append the loader to the entrypoint. Returns (entrypoint, backup).

    Appended at the end rather than spliced into the require block: last in the
    file means last evaluated, which is the same reason the generated file is
    named to sort last. Raises if there is no entrypoint to append to.
    """
    from .store import write_backup

    entry = entrypoint_for(Path(config_dir), dialect)
    if not entry.exists():
        raise FileNotFoundError(f"no {entry} to add the loader to")

    backup = write_backup(entry)
    text = entry.read_text()
    if text and not text.endswith("\n"):
        text += "\n"
    entry.write_text(text + "\n" + loader_snippet(target, dialect))
    return entry, backup

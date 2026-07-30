"""Find window rules that already apply to a given window.

Two reasons this matters. Obviously, so an existing rule can be edited instead
of a near-duplicate being appended. Less obviously: rules are evaluated top to
bottom and later ones win, so a rule you did not write can be the reason your
new one appears to do nothing. Foreign rules are surfaced read-only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import ipc
from .branding import FENCE_TAG

# Where a rule's `match` value gets compared to on a `hyprctl clients` entry.
_REGEX_PROPS = {
    "class": "class",
    "title": "title",
    "initial_class": "initialClass",
    "initial_title": "initialTitle",
    "xdg_tag": "xdgTag",
}
_BOOL_PROPS = {
    "xwayland": "xwayland",
    "float": "floating",
    "pin": "pinned",
}


@dataclass
class FoundRule:
    path: Path
    rule: dict
    managed_id: str | None = None      # set when we wrote it
    unmatched_props: list[str] = field(default_factory=list)
    enabled: bool = True
    site: "RuleSite | None" = None     # where it lives, for in-place edits
    order: int = 0                     # position in evaluation order
    matches_window: bool = False       # applies to the window in the editor
    template_id: str | None = None     # created from this template

    @property
    def editable(self) -> bool:
        """Ours, so the form can round-trip it and we can rewrite the block."""
        return self.managed_id is not None

    @property
    def name(self) -> str:
        return self.rule.get("name") or "unnamed"

    @property
    def key(self) -> tuple:
        """Identity that survives a rescan.

        Offsets shift as soon as anything in the file is rewritten, so a rule
        cannot be tracked by position across a multi-step operation. Managed
        rules have an id; foreign ones are identified by their content.
        """
        if self.managed_id:
            return (str(self.path), self.managed_id)
        return (str(self.path), self.rule.get("name"),
                tuple(sorted((self.rule.get("match") or {}).items())),
                tuple(self.effect_names()))

    def effect_names(self) -> list[str]:
        """Effects actually set. Values that are off are not in force, and
        listing them would overstate what the rule does."""
        out = []
        for key, value in self.rule.items():
            if key in ("match", "name"):
                continue
            if value is False or value == "" or value is None:
                continue
            out.append(key)
        return sorted(out)

    def summary(self) -> str:
        return ", ".join(self.effect_names()) or "no effects"

    def match_summary(self) -> str:
        return " · ".join(
            f"{k} {v}" for k, v in (self.rule.get("match") or {}).items()
        ) or "no match fields"


def _regex_matches(pattern: str, value: str) -> bool:
    """Approximate Hyprland's RE2 with Python's re.

    Good enough to decide whether to offer an existing rule for editing. RE2
    is close to a subset of Python's syntax for anything a window rule uses,
    and a wrong answer here costs a missed suggestion, not a broken config.
    """
    negate = False
    if pattern.startswith("negative:"):
        negate, pattern = True, pattern[len("negative:"):]
    try:
        hit = re.search(pattern, value or "") is not None
    except re.error:
        return False
    return not hit if negate else hit


def rule_matches(rule: dict, window: dict) -> tuple[bool, list[str]]:
    """Does `rule` apply to `window`? Returns (matched, props_we_could_not_check).

    All props must match. Props this cannot evaluate are reported rather than
    assumed either way -- the caller decides how much to trust the result.
    """
    match = rule.get("match")
    if not isinstance(match, dict) or not match:
        return False, []

    unknown: list[str] = []
    for prop, expected in match.items():
        if prop in _REGEX_PROPS:
            if not _regex_matches(str(expected), window.get(_REGEX_PROPS[prop], "")):
                return False, unknown
        elif prop in _BOOL_PROPS:
            if bool(expected) != bool(window.get(_BOOL_PROPS[prop])):
                return False, unknown
        elif prop == "fullscreen":
            if bool(expected) != bool(window.get("fullscreen")):
                return False, unknown
        elif prop == "tag":
            if str(expected).lstrip("+-") not in (window.get("tags") or []):
                return False, unknown
        elif prop == "workspace":
            ws = window.get("workspace") or {}
            if str(expected) not in (str(ws.get("id")), str(ws.get("name"))):
                return False, unknown
        else:
            unknown.append(prop)
    return True, unknown


# --------------------------------------------------------------------------
# Reading rules out of config
# --------------------------------------------------------------------------

#: Provenance line written above a rule created from a template.
#: Leading comment markers are matched loosely because deactivating a rule
#: comments every line again -- the marker then reads `-- -- from-template: x`,
#: and a stricter pattern would lose the link exactly when the template row
#: needs it to offer "Reactivate" instead of a second copy.
_TEMPLATE_MARK = re.compile(r"^[-#\s]*from-template:\s*(\S+)\s*$", re.M)

_FENCE = re.compile(rf"^[-#]+ >>> {re.escape(FENCE_TAG)} (\S+)[ \t]*$"
                    rf"(.*?)^[-#]+ <<< {re.escape(FENCE_TAG)} \1[ \t]*$",
                    re.M | re.S)


def parse_conf_rules(text: str) -> list[dict]:
    """Parse the legacy `windowrule { ... }` block form."""
    rules = []
    for body in re.findall(r"windowrule\s*\{(.*?)\}", text, re.S):
        rule: dict = {"match": {}}
        for line in body.splitlines():
            line = line.split("#")[0].strip()
            if not line or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key.startswith("match:"):
                rule["match"][key[len("match:"):]] = value
            elif key == "name":
                rule["name"] = value
            else:
                rule[key] = {"on": True, "off": False}.get(value, value)
        if rule["match"]:
            rules.append(rule)
    return rules


@dataclass
class RuleSite:
    """One `hl.window_rule(...)` call as it sits in a file."""
    text: str        # the call source, with any leading comment markers kept
    start: int       # offset of the first character of the line it starts on
    end: int         # offset just past the closing paren
    enabled: bool    # False when commented out

    def uncommented(self) -> str:
        if self.enabled:
            return self.text
        return "\n".join(
            re.sub(r"^(\s*)--\s?", r"\1", line) for line in self.text.splitlines()
        )


def _line_start(text: str, index: int) -> int:
    return text.rfind("\n", 0, index) + 1


def _is_commented(text: str, index: int) -> bool:
    """Whether the call at `index` sits behind a `--` on its own line."""
    line = text[_line_start(text, index):index]
    return line.lstrip().startswith("--")


def find_rule_sites(text: str) -> list[RuleSite]:
    """Locate every rule call, including ones commented out.

    Commented rules matter in both directions: they must not be reported as
    applying, and they must still be listed so they can be switched back on.
    """
    sites: list[RuleSite] = []
    for start, end in _call_spans(text):
        enabled = not _is_commented(text, start)
        line_start = _line_start(text, start)
        sites.append(RuleSite(text[line_start:end], line_start, end, enabled))
    return sites


def _call_spans(text: str):
    """Yield (start, end) for each `hl.window_rule(...)` call.

    Config files do real work -- `require`, `io.popen`, spawning startup apps.
    Executing one wholesale to read its rules would run all of that as a side
    effect of opening a window. So only the rule calls are located and run;
    everything around them is never evaluated.

    Scans for balanced parentheses while skipping over quoted strings and long
    brackets, so a `)` inside a title regex does not end the call early.
    """
    needle = "hl.window_rule"
    i = 0
    while (start := text.find(needle, i)) != -1:
        j = start + len(needle)
        while j < len(text) and text[j] in " \t\n":
            j += 1
        if j >= len(text) or text[j] != "(":
            i = start + len(needle)
            continue

        depth, k, quote, long_close = 0, j, None, None
        while k < len(text):
            c = text[k]
            if long_close:
                if text.startswith(long_close, k):
                    k += len(long_close) - 1
                    long_close = None
            elif quote:
                if c == "\\":
                    k += 1
                elif c == quote:
                    quote = None
            elif c in "\"'":
                quote = c
            elif c == "[" and (m := re.match(r"\[(=*)\[", text[k:])):
                long_close = "]" + m.group(1) + "]"
                k += len(m.group(0)) - 1
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    yield start, k + 1
                    break
            k += 1
        i = k + 1


def extract_rule_calls(text: str) -> str:
    """Just the enabled rule calls, concatenated. See _call_spans."""
    return "\n".join(
        text[s:e] for s, e in _call_spans(text) if not _is_commented(text, s)
    )


def rules_in_text(text: str, dialect: str) -> list[dict]:
    if dialect != "lua":
        return parse_conf_rules(text)
    calls = extract_rule_calls(text)
    return ipc.parse_window_rules(calls) if calls else []


def sited_rules(text: str, dialect: str) -> list[tuple[dict, "RuleSite | None"]]:
    """Rules paired with where they sit, so they can be rewritten in place.

    Parsed one at a time: a single malformed or commented-out call would
    otherwise desynchronise rules from their sites and every later edit would
    land on the wrong block.
    """
    if dialect != "lua":
        return [(r, None) for r in parse_conf_rules(text)]

    out = []
    for site in find_rule_sites(text):
        parsed = ipc.parse_window_rules(site.uncommented())
        if parsed:
            out.append((parsed[0], site))
    return out


def managed_blocks(path: Path) -> dict[str, str]:
    """rule_id -> the raw text of that fenced block."""
    if not path.exists():
        return {}
    return {rid: body for rid, body in _FENCE.findall(path.read_text())}


def managed_spans(text: str) -> list[tuple[str, int, int]]:
    """(rule_id, start, end) for each fenced block, by position.

    Positional rather than by body text: a duplicated rule has a byte-identical
    body, so searching for the text would attribute the copy to the original
    and leave the copy looking like somebody else's rule.
    """
    return [(m.group(1), m.start(), m.end()) for m in _FENCE.finditer(text)]


def config_files(config_dir: Path, dialect: str = "lua") -> list[Path]:
    """Config files that could actually contribute a rule.

    Filtered by dialect on purpose. A config whose entrypoint is `hyprland.lua`
    cannot source hyprlang `.conf` files at all, so any rules still sitting in
    them are inert -- reporting those as "already applies to this window" would
    be actively misleading, since half-migrated setups are full of them.

    Backups and the DMS-managed tree are skipped too: DMS re-serialises its own
    files, so presenting those as editable would be a lie.
    """
    suffixes = (".lua",) if dialect == "lua" else (".conf",)
    patterns = ("*", "conf.d/*", "conf/*")
    out: list[Path] = []
    for pattern in patterns:
        for p in sorted(config_dir.glob(pattern)):
            if p.suffix not in suffixes or not p.is_file():
                continue
            if ".bak" in p.name or "backup" in p.name:
                continue
            if "dms" in p.parts or p.name.startswith("dms"):
                continue
            out.append(p)
    return out


def find_all(config_dir: Path, managed_path: Path | None = None,
             dialect: str = "lua") -> list[FoundRule]:
    """Every window rule in the config tree, whatever it matches.

    `matches_window` comes back False on all of them -- with no window in hand
    there is nothing to compare against, and guessing would be worse than
    saying nothing.
    """
    return find_for_window(None, config_dir, managed_path, dialect)


def find_for_window(window: dict | None, config_dir: Path,
                    managed_path: Path | None = None,
                    dialect: str = "lua") -> list[FoundRule]:
    """Rules in the config tree, filtered to those applying to `window`.

    Pass `window=None` to get every rule regardless of what it matches.

    Returned in evaluation order -- file load order, then position within the
    file -- because that is what decides which rule wins, and the UI ranks on
    it. Disabled (commented out) rules are included but flagged, so they can be
    switched back on.
    """
    managed_path = managed_path.resolve() if managed_path else None
    found: list[FoundRule] = []
    order = 0

    for path in config_files(config_dir, dialect):
        try:
            text = path.read_text()
        except OSError:
            continue
        file_dialect = "lua" if path.suffix == ".lua" else "conf"
        is_managed = managed_path is not None and path.resolve() == managed_path
        spans = managed_spans(text) if is_managed else []

        for rule, site in sited_rules(text, file_dialect):
            enabled = site.enabled if site else True
            if window is None:
                ok, unknown, matches = True, [], False
            else:
                ok, unknown = rule_matches(rule, window)
                matches = ok
            if not ok:
                continue
            rule_id, template_id = (_owning_block(spans, site, text)
                                    if is_managed else (None, None))
            found.append(FoundRule(path, rule, rule_id, unknown, enabled,
                                   site, order, matches, template_id))
            order += 1

    return found


def _owning_block(spans: list[tuple[str, int, int]], site: "RuleSite | None",
                  text: str = "") -> tuple[str | None, str | None]:
    """(rule_id, template_id) for the fenced block a call site falls inside."""
    if site is None:
        return None, None
    for rule_id, start, end in spans:
        if start <= site.start < end:
            mark = _TEMPLATE_MARK.search(text[start:end]) if text else None
            return rule_id, (mark.group(1) if mark else None)
    return None, None

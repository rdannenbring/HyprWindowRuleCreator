"""Reusable rule templates.

A template is a rule without a home: a match and a set of effects that can be
dropped into the editor, saved straight out as a rule, or edited like anything
else. Shipped ones live in builtin_templates.py; the user's live in
~/.config/hyprwrc/templates.json.

Editing a shipped template does not modify the shipped data -- it writes a user
copy under the same id, which shadows it. That keeps "reset to the original"
available forever, and means an updated built-in set never silently overwrites
someone's edits.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import catalog
from .builtin_templates import BUILTIN
from .model import Rule, _coerce
from .settings import settings_path


def templates_path() -> Path:
    return settings_path().parent / "templates.json"


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or "template"


@dataclass
class Template:
    id: str
    title: str
    description: str = ""
    match: dict = field(default_factory=dict)
    effects: dict = field(default_factory=dict)
    sources: list = field(default_factory=list)
    builtin: bool = False        # came from the shipped set
    overridden: bool = False     # shipped, but a user copy shadows it

    # -- conversion -------------------------------------------------------

    def to_rule(self, name: str | None = None) -> Rule:
        """A Rule ready for the editor. Unknown keys are dropped, so a
        template written for a newer Hyprland cannot inject fields the form
        has no widget for."""
        rule = Rule(name=name if name is not None else self.title)
        for key, value in self.match.items():
            f = catalog.PROP_BY_KEY.get(key)
            if f:
                rule.match[key] = _coerce(f, value)
        for key, value in self.effects.items():
            f = catalog.EFFECT_BY_KEY.get(key)
            if f:
                rule.effects[key] = _coerce(f, value)
        return rule

    @classmethod
    def from_rule(cls, rule: Rule, title: str, description: str = "") -> "Template":
        return cls(
            id=slugify(title),
            title=title,
            description=description,
            match=dict(rule.match),
            effects=dict(rule.effects),
        )

    def unknown_keys(self) -> list[str]:
        out = [k for k in self.match if k not in catalog.PROP_BY_KEY]
        out += [k for k in self.effects if k not in catalog.EFFECT_BY_KEY]
        return sorted(out)

    def summary(self) -> str:
        effects = ", ".join(sorted(self.effects)) or "no effects"
        return effects

    def match_summary(self) -> str:
        if not self.match:
            return "no match criteria — you supply them"
        return " · ".join(f"{k} {v}" for k, v in self.match.items())

    def is_directly_usable(self) -> bool:
        """Whether it can be saved as a rule as-is.

        A template with no match criteria is a starting point, not something
        that can be switched on: a rule needs at least one match field.
        """
        return bool(self.match and self.effects)

    def as_dict(self) -> dict:
        data = asdict(self)
        for transient in ("builtin", "overridden"):
            data.pop(transient, None)
        # vec2 values arrive as tuples; JSON would make them lists anyway, but
        # being explicit keeps saved files stable across runs.
        for section in ("match", "effects"):
            data[section] = {
                k: (list(v) if isinstance(v, tuple) else v)
                for k, v in data[section].items()
            }
        return data


class TemplateStore:
    def __init__(self, path: Path | None = None):
        self.path = path or templates_path()

    # -- reading ----------------------------------------------------------

    def _user_raw(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        return [d for d in data if isinstance(d, dict) and d.get("id")]

    def all(self) -> list[Template]:
        """Shipped templates first, then the user's, with user copies
        shadowing shipped ones of the same id."""
        user = {d["id"]: d for d in self._user_raw()}
        out: list[Template] = []

        for raw in BUILTIN:
            data = user.pop(raw["id"], None)
            if data is not None:
                out.append(self._make(data, builtin=True, overridden=True))
            else:
                out.append(self._make(raw, builtin=True))

        for data in user.values():
            out.append(self._make(data))
        return out

    def get(self, template_id: str) -> Template | None:
        return next((t for t in self.all() if t.id == template_id), None)

    @staticmethod
    def _make(raw: dict, builtin: bool = False,
              overridden: bool = False) -> Template:
        return Template(
            id=raw["id"],
            title=raw.get("title") or raw["id"],
            description=raw.get("description", ""),
            match=dict(raw.get("match") or {}),
            effects=dict(raw.get("effects") or {}),
            sources=list(raw.get("sources") or []),
            builtin=builtin,
            overridden=overridden,
        )

    # -- writing ----------------------------------------------------------

    def _write(self, entries: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(entries, indent=2) + "\n")

    def unique_id(self, base: str) -> str:
        taken = {t.id for t in self.all()}
        slug, n = slugify(base), 2
        candidate = slug
        while candidate in taken:
            candidate = f"{slug}-{n}"
            n += 1
        return candidate

    def save(self, template: Template) -> None:
        entries = [d for d in self._user_raw() if d["id"] != template.id]
        entries.append(template.as_dict())
        self._write(entries)

    def delete(self, template_id: str) -> bool:
        """Remove a user template, or drop a user override of a shipped one.

        Shipped templates cannot be deleted -- deleting the override restores
        the original rather than leaving a hole.
        """
        entries = self._user_raw()
        remaining = [d for d in entries if d["id"] != template_id]
        if len(remaining) == len(entries):
            return False
        self._write(remaining)
        return True

    def is_user_copy(self, template_id: str) -> bool:
        return any(d["id"] == template_id for d in self._user_raw())

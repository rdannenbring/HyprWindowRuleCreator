"""User preferences, stored as JSON outside the Hyprland config.

Deliberately not kept in the generated rule file: that file is machine-managed
and gets rewritten, and settings surviving a "delete all my rules" is the
behaviour people expect.
"""

from __future__ import annotations

import json
import os
from .branding import CONFIG_DIR_NAME
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


def _config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))


def settings_path() -> Path:
    return _config_home() / CONFIG_DIR_NAME / "settings.json"


@dataclass
class Settings:
    #: Where generated rules are written, relative to the hypr config dir.
    #: The `zz-` prefix makes it sort last in a conf.d/*.lua glob, which is
    #: what makes "make active" mean anything -- change it and a rule at the
    #: bottom of the file may still lose to another file.
    generated_file: str = "conf.d/zz-windowrule-generated.lua"

    #: How many timestamped .bak files to keep per file. 0 keeps every one.
    backup_keep: int = 10

    #: "auto" detects from your config; otherwise force lua or conf.
    dialect: str = "auto"

    #: Open the matching rule straight away when there is exactly one of ours.
    auto_load_single_match: bool = True

    #: Shrink to a corner strip while previewing.
    shrink_while_previewing: bool = True

    #: Ask before commenting out a rule in a file hyprwrc did not write.
    #: Off is not offered in the UI -- it exists so tests can skip the dialog.
    confirm_foreign_edits: bool = True

    _path: Path | None = field(default=None, repr=False, compare=False)

    # -- persistence ------------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or settings_path()
        obj = cls()
        obj._path = path
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return obj
        known = {f.name for f in fields(cls) if not f.name.startswith("_")}
        for key, value in (raw or {}).items():
            if key in known:
                setattr(obj, key, value)
        obj.validate()
        return obj

    def save(self) -> None:
        path = self._path or settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        path.write_text(json.dumps(data, indent=2) + "\n")

    def validate(self) -> None:
        """Clamp anything nonsensical rather than failing to start."""
        if self.dialect not in ("auto", "lua", "conf"):
            self.dialect = "auto"
        try:
            self.backup_keep = max(0, int(self.backup_keep))
        except (TypeError, ValueError):
            self.backup_keep = 10
        rel = str(self.generated_file).strip()
        # Must stay inside the config dir: an absolute or climbing path would
        # write rules somewhere Hyprland never reads.
        if not rel or rel.startswith("/") or ".." in Path(rel).parts:
            rel = "conf.d/zz-windowrule-generated.lua"
        self.generated_file = rel

    # -- derived ----------------------------------------------------------

    def suffix(self) -> str:
        return "conf" if self.generated_file.endswith(".conf") else "lua"

    def sorts_last(self) -> bool:
        """Whether the filename still sorts last in a conf.d/* glob.

        Not enforced -- it is the user's file name -- but the settings screen
        says so, because "make active" quietly stops meaning anything when
        this is false.
        """
        name = Path(self.generated_file).name
        return name.startswith("zz")

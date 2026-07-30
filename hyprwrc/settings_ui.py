"""The settings dialog.

Kept apart from ui.py, which is already the biggest file in the project.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from . import store  # noqa: E402
from .branding import APP_NAME  # noqa: E402
from .settings import Settings  # noqa: E402
from . import gtkutil  # noqa: E402

DIALECTS = ("auto", "lua", "conf")


class SettingsDialog(Adw.PreferencesDialog):
    """Edits a Settings and calls `on_saved` when something changes.

    Applied immediately rather than behind an OK button: everything here is
    reversible, and a settings screen that silently discards edits when you
    close it is worse than one that just applies them.
    """

    def __init__(self, prefs: Settings, config_dir, on_saved=None):
        super().__init__(title=f"{APP_NAME} Settings")
        self.prefs = prefs
        self.config_dir = config_dir
        self._on_saved = on_saved

        page = Adw.PreferencesPage(title="General",
                                   icon_name="preferences-system-symbolic")
        page.add(self._group_output())
        page.add(self._group_backups())
        page.add(self._group_behaviour())
        page.add(self._group_about())
        self.add(page)

    # -- where rules are written ------------------------------------------

    def _group_output(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Generated rules file",
            description="Relative to your Hyprland config directory.",
        )

        self.file_row = Adw.EntryRow(title="Path")
        self.file_row.set_text(self.prefs.generated_file)
        self.file_row.connect("apply", self._apply_file)
        self.file_row.set_show_apply_button(True)
        group.add(self.file_row)

        self.file_note = gtkutil.action_row(title="", subtitle="")
        self.file_note.add_css_class("property")
        group.add(self.file_note)

        self.dialect_row = Adw.ComboRow(
            title="Syntax",
            subtitle="auto follows your config: hyprland.lua means lua",
            model=Gtk.StringList.new(list(DIALECTS)),
        )
        self.dialect_row.set_selected(DIALECTS.index(self.prefs.dialect))
        self.dialect_row.connect("notify::selected", self._apply_dialect)
        group.add(self.dialect_row)

        self._refresh_file_note()
        return group

    def _refresh_file_note(self):
        full = self.config_dir / self.prefs.generated_file
        exists = "exists" if full.exists() else "will be created on first save"
        self.file_note.set_title(str(full))
        if self.prefs.sorts_last():
            self.file_note.set_subtitle(
                f"{exists} · sorts last in a conf.d/* glob, so rules saved "
                "here are evaluated after your other conf.d files")
            self.file_note.remove_css_class("warning")
        else:
            self.file_note.set_subtitle(
                f"{exists} · does NOT start with “zz”, so other conf.d files "
                "may load after it and override rules saved here")
            self.file_note.add_css_class("warning")

    def _apply_file(self, row):
        self.prefs.generated_file = row.get_text().strip()
        self.prefs.validate()
        row.set_text(self.prefs.generated_file)  # validate may have reset it
        self._refresh_file_note()
        self._save()

    def _apply_dialect(self, row, _param):
        self.prefs.dialect = DIALECTS[row.get_selected()]
        self._save()

    # -- backups ----------------------------------------------------------

    def _group_backups(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Backups",
            description="A timestamped copy is written before every change.",
        )

        self.keep_row = Adw.SpinRow(
            title="Backups to keep",
            subtitle="Oldest are deleted beyond this. 0 keeps every one.",
            adjustment=Gtk.Adjustment(lower=0, upper=500, step_increment=1,
                                      value=self.prefs.backup_keep),
        )
        self.keep_row.connect("notify::value", self._apply_keep)
        group.add(self.keep_row)

        self.count_row = gtkutil.action_row(title="Stored now")
        self.count_row.add_css_class("property")
        prune = Gtk.Button(label="Prune now", valign=Gtk.Align.CENTER)
        prune.connect("clicked", self._prune_now)
        self.count_row.add_suffix(prune)
        group.add(self.count_row)

        self._refresh_backup_count()
        return group

    def _target_path(self):
        return self.config_dir / self.prefs.generated_file

    def _refresh_backup_count(self):
        found = store.backups_for(self._target_path())
        self.count_row.set_subtitle(
            f"{len(found)} backup(s)" +
            (f" · oldest {found[-1].name}" if found else ""))

    def _apply_keep(self, row, _param):
        self.prefs.backup_keep = int(row.get_value())
        self._save()
        self._refresh_backup_count()

    def _prune_now(self, _btn):
        removed = store.prune_backups(self._target_path(),
                                      self.prefs.backup_keep)
        self._refresh_backup_count()
        self.count_row.set_title(
            f"Stored now — removed {len(removed)}" if removed else "Stored now")

    # -- behaviour --------------------------------------------------------

    def _group_behaviour(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Behaviour")

        self.autoload_row = Adw.SwitchRow(
            title="Open the matching rule automatically",
            subtitle="When exactly one rule of ours already matches the "
                     "picked window",
            active=self.prefs.auto_load_single_match,
        )
        self.autoload_row.connect("notify::active", self._apply_autoload)
        group.add(self.autoload_row)

        self.shrink_row = Adw.SwitchRow(
            title="Get out of the way while previewing",
            subtitle="Shrink to a pinned strip in the corner",
            active=self.prefs.shrink_while_previewing,
        )
        self.shrink_row.connect("notify::active", self._apply_shrink)
        group.add(self.shrink_row)

        self.confirm_row = Adw.SwitchRow(
            title="Confirm edits to other people's config",
            subtitle=f"Ask before commenting out a rule in a file {APP_NAME} did "
                     "not write. Strongly recommended.",
            active=self.prefs.confirm_foreign_edits,
        )
        self.confirm_row.connect("notify::active", self._apply_confirm)
        group.add(self.confirm_row)
        return group

    def _apply_autoload(self, row, _p):
        self.prefs.auto_load_single_match = row.get_active()
        self._save()

    def _apply_shrink(self, row, _p):
        self.prefs.shrink_while_previewing = row.get_active()
        self._save()

    def _apply_confirm(self, row, _p):
        self.prefs.confirm_foreign_edits = row.get_active()
        self._save()

    # -- about ------------------------------------------------------------

    def _group_about(self) -> Adw.PreferencesGroup:
        from . import __version__
        from .settings import settings_path

        group = Adw.PreferencesGroup(title="About")
        for title, value in (
            ("Version", __version__),
            ("Settings file", str(settings_path())),
            ("Hyprland config", str(self.config_dir)),
        ):
            row = gtkutil.action_row(title=title, subtitle=value)
            row.add_css_class("property")
            group.add(row)
        return group

    # -- persistence ------------------------------------------------------

    def _save(self):
        try:
            self.prefs.save()
        except OSError:
            pass  # a read-only config dir should not break the editor
        if self._on_saved:
            self._on_saved()

"""Turning an existing rule into a template.

Browsing and editing templates lives in ui.py, as the third scope of the main
rules list -- one list with a filter beats a second window that reimplements
the same rows.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .templates import Template, TemplateStore  # noqa: E402
from . import gtkutil  # noqa: E402


class SaveAsTemplateDialog(Adw.AlertDialog):
    """Ask for a title/description, then hand back a Template."""

    def __init__(self, store: TemplateStore, rule, on_saved=None):
        super().__init__(
            heading="Save as template",
            body="Templates are reusable — the match and effects are kept, "
                 "nothing about this particular window is.")
        self.store = store
        self.rule = rule
        self._on_saved = on_saved

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.title_entry = Adw.EntryRow(title="Title")
        self.title_entry.set_text(rule.name or "")
        self.desc_entry = Adw.EntryRow(title="Description (optional)")

        group = Adw.PreferencesGroup()
        group.add(self.title_entry)
        group.add(self.desc_entry)
        box.append(group)
        self.set_extra_child(box)

        self.add_response("cancel", "Cancel")
        self.add_response("save", "Save template")
        self.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        self.set_default_response("save")
        self.connect("response", self._answered)

    def _answered(self, _d, response):
        if response != "save":
            return
        title = self.title_entry.get_text().strip() or "Untitled template"
        template = Template.from_rule(
            self.rule, title, self.desc_entry.get_text().strip())
        template.id = self.store.unique_id(title)
        self.store.save(template)
        if self._on_saved:
            self._on_saved(template)

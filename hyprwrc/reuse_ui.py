"""Pick an existing rule to clone, or to add the current window to.

Split out of ui.py because it is self-contained: it is handed a list of rules
and two callbacks, and knows nothing about the editor's state machine.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from . import gtkutil, merge, scan  # noqa: E402
from .branding import outside_app  # noqa: E402


class ReuseRuleDialog(Adw.Dialog):
    """A searchable list of every rule in the config, with both reuse actions.

    Both actions live on the same row on purpose. "Clone this" and "add my
    window to this" are the same decision seen from two sides, and making them
    separate dialogs would mean picking the verb before seeing the rules.
    """

    def __init__(self, rules: list[scan.FoundRule], window: dict,
                 on_clone, on_extend):
        super().__init__(title="Use an existing rule",
                         content_width=880, content_height=680)
        self.rules = rules
        self.window_info = window
        self._on_clone = on_clone
        self._on_extend = on_extend

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)

        self.search = Gtk.SearchEntry(
            placeholder_text="Search by name, match, effect or file",
            hexpand=True)
        self.search.connect("search-changed", lambda *_: self._rebuild())
        search_bar = Gtk.Box(margin_start=12, margin_end=12, margin_top=6,
                             margin_bottom=6)
        search_bar.append(self.search)
        toolbar.add_top_bar(search_bar)

        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                           margin_start=12, margin_end=12, margin_top=6,
                           margin_bottom=12)
        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroller.set_child(self.box)
        toolbar.set_content(scroller)

        self.set_child(toolbar)
        self._rebuild()

    # -- list -------------------------------------------------------------

    def _needle(self) -> str:
        return (self.search.get_text() or "").strip().lower()

    def _filtered(self) -> list[scan.FoundRule]:
        needle = self._needle()
        if not needle:
            return self.rules
        return [
            f for f in self.rules
            if needle in " ".join([f.name, f.path.name, f.match_summary(),
                                   f.summary()]).lower()
        ]

    def _rebuild(self):
        while (child := self.box.get_first_child()) is not None:
            self.box.remove(child)

        rules = self._filtered()
        if not rules:
            lst = self._section()
            empty = gtkutil.action_row(
                title=("Nothing matches that search" if self._needle()
                       else "No window rules found in your config"),
                subtitle=("Try a different term."
                          if self._needle() else
                          "There is nothing to reuse yet."))
            empty.add_css_class("dim-label")
            lst.append(empty)
            return

        current, section = None, None
        for found in rules:
            if found.path != current:
                current = found.path
                section = self._section(found.path.name, str(found.path.parent))
            section.append(self._row(found))

    def _section(self, title: str | None = None,
                 subtitle: str | None = None) -> Gtk.ListBox:
        if title:
            head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1,
                           margin_top=10, margin_bottom=4)
            lbl = Gtk.Label(label=title, xalign=0)
            lbl.add_css_class("heading")
            head.append(lbl)
            if subtitle:
                sub = Gtk.Label(label=subtitle, xalign=0)
                sub.add_css_class("caption")
                sub.add_css_class("dim-label")
                head.append(sub)
            self.box.append(head)
        lst = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        lst.add_css_class("boxed-list")
        self.box.append(lst)
        return lst

    def _row(self, found: scan.FoundRule) -> Adw.ActionRow:
        row = gtkutil.action_row(
            title=found.name,
            subtitle=f"{found.match_summary()}  →  {found.summary()}")
        if not found.enabled:
            row.add_css_class("dim-label")

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                          valign=Gtk.Align.CENTER)

        add = Gtk.Button(label="Add this window", valign=Gtk.Align.CENTER)
        why = self._cannot_extend(found)
        if why:
            add.set_sensitive(False)
            add.set_tooltip_text(why)
        else:
            add.set_tooltip_text(self._extend_tooltip(found))
            add.connect("clicked", lambda *_: self._chose(self._on_extend, found))
        actions.append(add)

        clone = Gtk.Button(label="Clone", valign=Gtk.Align.CENTER)
        clone.add_css_class("suggested-action")
        clone.set_tooltip_text(
            "Start a new rule doing the same thing, aimed at the current window")
        clone.connect("clicked", lambda *_: self._chose(self._on_clone, found))
        actions.append(clone)

        row.add_suffix(actions)
        return row

    def _extend_tooltip(self, found: scan.FoundRule) -> str:
        """What widening this rule would actually do, in concrete terms.

        The button says "Add this window"; the detail belongs here, where it
        can name the field and show the real before/after rather than
        describing the operation in the abstract.
        """
        head = ("Adds this window to the rule's match condition. The rule "
                "stays where it is and nothing else about it changes.")
        match = found.rule.get("match") or {}
        fields = merge.extendable_fields(found.rule, self.window_info)
        if len(fields) > 1:
            return (f"{head}\n\nThis rule matches on "
                    + ", ".join(fields)
                    + " — you will be asked which one to widen.")
        key = fields[0]
        old = str(match[key])
        new = merge.extended_pattern(old, merge.window_value(self.window_info, key))
        return f"{head}\n\n{key}\n  {old}\n  → {new}"

    def _cannot_extend(self, found: scan.FoundRule) -> str:
        """Why "add this window" is unavailable, or "" when it is."""
        fields = merge.extendable_fields(found.rule, self.window_info)
        if not fields:
            if merge.identity_fields(found.rule):
                return ("This window has no value for the field this rule "
                        "matches on — clone it instead.")
            return ("This rule does not match on a class or title, so there "
                    "is nothing to add a window to. Clone it instead.")
        if not found.editable and found.site is None:
            return (f"This rule is {outside_app()} and its exact position in "
                    "the file could not be located, so it cannot be edited "
                    "in place. Clone it instead.")
        # Every field it could widen already covers this window. Letting the
        # click through would only produce a toast saying nothing happened.
        match = found.rule.get("match") or {}
        if all(merge.already_covered(str(match[k]),
                                     merge.window_value(self.window_info, k))
               for k in fields):
            return ("This rule's "
                    + " and ".join(fields)
                    + " already matches this window.")
        return ""

    def _chose(self, callback, found: scan.FoundRule):
        self.close()
        callback(found)

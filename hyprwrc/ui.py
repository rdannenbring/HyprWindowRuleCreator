"""GTK4 / libadwaita editor.

Shape of the interaction: the window is picked *before* this module runs. That
ordering is not incidental -- the moment this GUI maps it becomes the active
window, so anything that resolved the target lazily would resolve to itself.
"""

from __future__ import annotations

import contextlib
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

try:
    gi.require_version("GtkSource", "5")
    from gi.repository import GtkSource  # noqa: E402
    HAVE_SOURCEVIEW = True
except (ValueError, ImportError):  # pragma: no cover - depends on host
    HAVE_SOURCEVIEW = False

from . import (catalog, emit, ipc, merge, model, picker,  # noqa: E402
               preview as preview_mod, scan, store, templates)
from .model import Rule  # noqa: E402
from . import gtkutil  # noqa: E402
from . import branding  # noqa: E402
from .branding import APP_ID, APP_NAME, outside_app  # noqa: E402

DEBOUNCE_MS = 180


# ---------------------------------------------------------------------------
# One editable field (a match prop or an effect)
# ---------------------------------------------------------------------------

class FieldRow(Adw.ActionRow):
    """Check to include, plus a value widget matched to the field's kind."""

    def __init__(self, field: catalog.Field, on_change, initial=None):
        super().__init__()
        # Same reason as gtkutil: these come from the catalog today, but a row
        # that parses markup is a latent bug the moment the text changes.
        self.set_use_markup(False)
        self.set_title(field.key)
        self.set_subtitle(field.doc)
        self.field = field
        self._on_change = on_change
        self._widgets: list[Gtk.Widget] = []

        self.check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
        self.check.connect("toggled", self._changed)
        self.add_prefix(self.check)

        self._build_value_widget(initial)
        if initial is not None:
            self.check.set_active(True)
        self._sync_sensitivity()

    # -- construction -----------------------------------------------------

    def _build_value_widget(self, initial):
        kind = self.field.kind

        if kind == "bool":
            self.switch = Gtk.Switch(valign=Gtk.Align.CENTER, active=True)
            if initial is not None:
                self.switch.set_active(bool(initial))
            self.switch.connect("notify::active", self._changed)
            self._attach(self.switch)

        elif kind in ("int", "number"):
            digits = 0 if kind == "int" else 2
            step = 1 if kind == "int" else 0.05
            adj = Gtk.Adjustment(lower=-100000, upper=100000, step_increment=step)
            self.spin = Gtk.SpinButton(
                adjustment=adj, digits=digits, valign=Gtk.Align.CENTER, width_chars=7
            )
            if initial is not None:
                self.spin.set_value(float(initial))
            self.spin.connect("value-changed", self._changed)
            self._attach(self.spin)

        elif kind == "enum":
            self.dropdown = Gtk.DropDown.new_from_strings(list(self.field.choices))
            self.dropdown.set_valign(Gtk.Align.CENTER)
            if initial is not None and initial in self.field.choices:
                self.dropdown.set_selected(self.field.choices.index(initial))
            self.dropdown.connect("notify::selected", self._changed)
            self._attach(self.dropdown)

        elif kind == "vec2":
            self.entry_x = self._entry("x / w", initial[0] if initial else "")
            self.entry_y = self._entry("y / h", initial[1] if initial else "")
            self._attach(self.entry_x)
            self._attach(self.entry_y)

        elif kind == "regex":
            # Several alternatives are allowed here, joined into one RE2
            # alternation on the way out -- Hyprland refuses a repeated field.
            self._build_multi_value(initial)

        else:  # string, gradient
            self.entry = self._entry("value", initial or "", width=26)
            self._attach(self.entry)

    def _build_multi_value(self, initial):
        self.values_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                                  valign=Gtk.Align.CENTER)
        self.value_rows: list[Gtk.Box] = []

        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4,
                        valign=Gtk.Align.CENTER)
        outer.append(self.values_box)

        add = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.START,
                         tooltip_text="Match any of several values (OR)")
        add.add_css_class("flat")
        add.connect("clicked", lambda *_: self._add_value(focus=True))
        outer.append(add)

        for text in (model.split_alternation(initial) if initial else [""]):
            self._add_value(text)
        self._attach(outer)

    def _add_value(self, text: str = "", focus: bool = False):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        entry = self._entry("^exact$", text, width=24)
        row.append(entry)

        remove = Gtk.Button(icon_name="list-remove-symbolic",
                            valign=Gtk.Align.CENTER,
                            tooltip_text="Remove this alternative")
        remove.add_css_class("flat")
        remove.connect("clicked", lambda *_: self._remove_value(row))
        row.append(remove)

        row.entry = entry
        row.remove_btn = remove
        self.values_box.append(row)
        self.value_rows.append(row)
        self._sync_value_rows()
        if focus:
            entry.grab_focus()
        self._changed()

    def _remove_value(self, row):
        if len(self.value_rows) <= 1:
            return
        self.values_box.remove(row)
        self.value_rows.remove(row)
        self._sync_value_rows()
        self._changed()

    def _sync_value_rows(self):
        """The remove button is meaningless when there is only one value."""
        multiple = len(self.value_rows) > 1
        for row in self.value_rows:
            row.remove_btn.set_visible(multiple)

    def _entry(self, placeholder: str, text: str = "", width: int = 10) -> Gtk.Entry:
        e = Gtk.Entry(
            placeholder_text=placeholder,
            valign=Gtk.Align.CENTER,
            width_chars=width,
            text=str(text),
        )
        e.connect("changed", self._changed)
        return e

    def _attach(self, widget: Gtk.Widget):
        self.add_suffix(widget)
        self._widgets.append(widget)

    # -- state ------------------------------------------------------------

    def _sync_sensitivity(self):
        for w in self._widgets:
            w.set_sensitive(self.check.get_active())

    def _changed(self, *_a):
        self._sync_sensitivity()
        self._on_change()

    @property
    def enabled(self) -> bool:
        return self.check.get_active()

    def set_enabled(self, value: bool):
        self.check.set_active(value)

    @property
    def value(self):
        kind = self.field.kind
        if kind == "bool":
            return self.switch.get_active()
        if kind == "int":
            return int(self.spin.get_value())
        if kind == "number":
            return round(self.spin.get_value(), 4)
        if kind == "enum":
            idx = self.dropdown.get_selected()
            return self.field.choices[idx] if idx < len(self.field.choices) else ""
        if kind == "vec2":
            return (self.entry_x.get_text().strip(), self.entry_y.get_text().strip())
        if kind == "regex":
            return model.combine_alternation(
                [r.entry.get_text() for r in self.value_rows])
        return self.entry.get_text().strip()

    def set_value(self, value):
        """Push a value in from a rule read back out of config."""
        kind = self.field.kind
        if kind == "bool":
            self.switch.set_active(bool(value))
        elif kind in ("int", "number"):
            try:
                self.spin.set_value(float(value))
            except (TypeError, ValueError):
                pass
        elif kind == "enum":
            if value in self.field.choices:
                self.dropdown.set_selected(self.field.choices.index(value))
        elif kind == "vec2":
            parts = (list(value) if isinstance(value, (list, tuple))
                     else str(value).replace(",", " ").split())
            parts += ["", ""]
            self.entry_x.set_text(str(parts[0]))
            self.entry_y.set_text(str(parts[1]))
        elif kind == "regex":
            for row in list(self.value_rows):
                self.values_box.remove(row)
            self.value_rows.clear()
            for part in model.split_alternation(str(value)):
                self._add_value(part)
        else:
            self.entry.set_text(str(value))

    def has_usable_value(self) -> bool:
        kind = self.field.kind
        if kind == "vec2":
            x, y = self.value
            return bool(x and y)
        if kind in ("string", "regex", "gradient"):
            return bool(self.value)
        return True

    def value_count(self) -> int:
        """How many alternatives are filled in (regex fields only)."""
        return sum(1 for r in getattr(self, "value_rows", [])
                   if r.entry.get_text().strip())


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class EditorWindow(Adw.ApplicationWindow):
    def __init__(self, app, window: dict, rule_store: store.RuleStore):
        super().__init__(application=app, default_width=1500, default_height=880)
        self.window_info = window
        self.store = rule_store
        self.preview = preview_mod.Preview(window["address"])
        self._debounce_id = 0
        self._preview_on = False
        self._compact = False
        self._restore_geometry = None
        self._raw_dirty = False       # user hand-edited the generated text
        self._suppress_buffer = False  # our own writes to the buffer
        self._editing_id = None       # set when updating an existing rule
        # Invariant: exactly one thing is being edited at a time -- either an
        # existing rule (_editing_id) or an unsaved draft. The draft gets a row
        # in the list so what you are typing is visible where rules live.
        self._draft = False
        self._draft_row = None
        self._template_edit = None    # set while editing a template
        self._template_is_new = False # ...and it is not on disk yet
        self._scope = "window"        # or "all"
        self._picking = False         # a click-to-pick is in flight
        self._closing = False         # close already confirmed
        self._viewed_source = None    # a rule being inspected
        self._viewed_key = None
        self._viewed_label = "Rules"
        self.template_store = templates.TemplateStore()
        self._rows_match: dict[str, FieldRow] = {}
        self._rows_effect: dict[str, FieldRow] = {}

        try:
            self.existing = scan.find_for_window(
                window, rule_store.config_dir, rule_store.path, rule_store.dialect)
        except (ipc.HyprError, OSError):
            self.existing = []

        self.set_title(f"Window Rule — {window.get('class') or 'window'}")
        self.connect("close-request", self._on_close)

        self.header = self._build_header()
        # Before the body: the rules section reads add_btn and scope_bar while
        # it builds, and its labels are kept in step with the active scope.
        scope_bar = self._build_scope_bar()
        self.footer = self._build_footer()

        # Not homogeneous: otherwise the stack always requests the full
        # editor's size and the compact strip can never actually be small.
        self.stack = Gtk.Stack(hhomogeneous=False, vhomogeneous=False)
        self.stack.add_named(self._build_body(), "full")
        self.stack.add_named(self._build_compact(), "compact")
        self.stack.set_visible_child_name("full")

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(self.header)
        toolbar.add_top_bar(scope_bar)
        toolbar.set_content(self.stack)
        toolbar.add_bottom_bar(self.footer)

        self.toast_overlay = Adw.ToastOverlay(child=toolbar)
        self.set_content(self.toast_overlay)

        self._seed_from_window()
        self._refresh()

        # One rule of ours, unambiguously this window's: just open it. Anything
        # more and the user picks, since guessing wrong means silently editing
        # the wrong rule.
        # No draft on open. A draft is something the user asks for with +,
        # not a thing that appears because a window happens to have no rules.
        ours = [f for f in self.existing if f.editable and f.enabled]
        if len(ours) == 1 and self.store.prefs.auto_load_single_match:
            GLib.idle_add(self._load_rule, ours[0])

        esc = Gtk.ShortcutController()
        esc.add_shortcut(Gtk.Shortcut.new(
            Gtk.ShortcutTrigger.parse_string("Escape"),
            Gtk.CallbackAction.new(lambda *_: self.close() or True),
        ))
        self.add_controller(esc)

    # -- chrome -----------------------------------------------------------

    def _build_header(self) -> Adw.HeaderBar:
        header = Adw.HeaderBar()
        self.cancel_btn = Gtk.Button(label="Close")
        self.cancel_btn.connect("clicked", self._on_cancel)
        header.pack_start(self.cancel_btn)

        self.save_btn = Gtk.Button(label="Save & Reload")
        self.save_btn.add_css_class("suggested-action")
        self.save_btn.connect("clicked", self._on_save)
        header.pack_end(self.save_btn)

        settings_btn = Gtk.Button(icon_name="preferences-system-symbolic",
                                  tooltip_text="Settings")
        settings_btn.add_css_class("flat")
        settings_btn.connect("clicked", self._on_settings)
        header.pack_end(settings_btn)

        win = self.window_info
        header.set_title_widget(Adw.WindowTitle(
            title=win.get("class") or "window",
            subtitle=(win.get("title") or "")[:70],
        ))
        return header

    def _build_scope_bar(self) -> Gtk.Widget:
        """The view switcher, in its own bar under the header.

        A toolbar row rather than the top of the scrolled content, so it stays
        put while a long rule list scrolls under it.
        """
        self.scope_toggle = Adw.ToggleGroup(valign=Gtk.Align.CENTER)
        self.scope_toggle.add(Adw.Toggle(name="window", label="This window"))
        self.scope_toggle.add(Adw.Toggle(name="all", label="All rules"))
        self.scope_toggle.add(Adw.Toggle(name="templates", label="All templates"))
        self.scope_toggle.set_active_name("window")
        self.scope_toggle.connect("notify::active-name", self._on_scope_changed)

        # One + whose meaning follows the scope, rather than a second button
        # that only applies to one of three views.
        self.add_btn = Gtk.Button(icon_name="list-add-symbolic",
                                  valign=Gtk.Align.CENTER)
        self.add_btn.add_css_class("flat")
        self.add_btn.connect("clicked", lambda *_: self._on_add_clicked())

        # Reuse sits next to + because it answers the same question -- "how do
        # I get a rule for this window" -- and the answer is usually that one
        # already exists somewhere. Only in the window scope: both of the
        # actions behind it are about the window currently in the editor.
        self.reuse_btn = Gtk.Button(
            icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER,
            tooltip_text="Clone an existing rule, or add this window to one")
        self.reuse_btn.add_css_class("flat")
        self.reuse_btn.connect("clicked", lambda *_: self._open_reuse())

        # + travels with the tabs rather than sitting at the far edge of the
        # window: it acts on whichever list the tabs select, and parked on the
        # right it would hang over the code pane it has nothing to do with.
        centre = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        centre.append(self.scope_toggle)
        centre.append(self.reuse_btn)
        centre.append(self.add_btn)

        self.scope_bar = Gtk.CenterBox(margin_top=4, margin_bottom=8,
                                       margin_start=12, margin_end=12)
        self.scope_bar.set_center_widget(centre)
        return self.scope_bar

    def _build_footer(self) -> Gtk.Widget:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                      margin_top=8, margin_bottom=8, margin_start=12, margin_end=12)

        self.preview_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.preview_switch.connect("notify::active", self._on_preview_toggled)
        bar.append(self.preview_switch)

        label_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        lbl = Gtk.Label(label="Live preview", xalign=0)
        lbl.add_css_class("heading")
        label_box.append(lbl)
        sub = Gtk.Label(
            label="Approximates the rule on the open window. Reverted on close.",
            xalign=0,
        )
        sub.add_css_class("dim-label")
        sub.add_css_class("caption")
        label_box.append(sub)
        bar.append(label_box)

        self.shrink_check = Gtk.CheckButton(
            label="Get out of the way while previewing",
            valign=Gtk.Align.CENTER,
            active=self.store.prefs.shrink_while_previewing,
            tooltip_text="Shrink to a pinned strip in the corner so you can "
                         "see the window you are styling",
        )
        bar.append(self.shrink_check)

        bar.append(Gtk.Box(hexpand=True))

        bar.append(Gtk.Label(label="Write as", css_classes=["dim-label", "caption"]))
        self.dialect_drop = Gtk.DropDown.new_from_strings(["lua", "conf"])
        self.dialect_drop.set_valign(Gtk.Align.CENTER)
        self.dialect_drop.set_selected(0 if self.store.dialect == "lua" else 1)
        self.dialect_drop.set_tooltip_text(
            "Detected from your config. lua is the 0.55+ syntax; conf is the "
            "deprecated hyprlang block form."
        )
        self.dialect_drop.connect("notify::selected", self._on_dialect_changed)
        bar.append(self.dialect_drop)

        self.dialect_label = Gtk.Label(label=f"→ {self.store.path.name}")
        self.dialect_label.add_css_class("dim-label")
        self.dialect_label.add_css_class("caption")
        bar.append(self.dialect_label)
        return bar

    # -- compact mode -----------------------------------------------------

    def _build_compact(self) -> Gtk.Widget:
        """The strip shown while previewing, so the target window is visible."""
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10,
                      valign=Gtk.Align.CENTER,
                      margin_top=10, margin_bottom=10,
                      margin_start=14, margin_end=14)

        dot = Gtk.Image.new_from_icon_name("media-record-symbolic")
        dot.add_css_class("accent")
        dot.set_valign(Gtk.Align.CENTER)
        bar.append(dot)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True,
                      valign=Gtk.Align.CENTER)
        title = Gtk.Label(label="Previewing", xalign=0)
        title.add_css_class("heading")
        box.append(title)
        self.compact_sub = Gtk.Label(xalign=0, ellipsize=3)
        self.compact_sub.add_css_class("dim-label")
        self.compact_sub.add_css_class("caption")
        box.append(self.compact_sub)
        bar.append(box)

        stop = Gtk.Button(label="Stop", valign=Gtk.Align.CENTER,
                          tooltip_text="Stop previewing and reopen the editor")
        stop.connect("clicked", self._on_compact_stop)
        bar.append(stop)

        save = Gtk.Button(label="Save", valign=Gtk.Align.CENTER)
        save.add_css_class("suggested-action")
        save.connect("clicked", self._on_save)
        bar.append(save)
        return bar

    def _own_window(self) -> dict | None:
        """Find our own Hyprland window. Matched on pid, because class is
        shared by any other instance the user has open."""
        import os
        pid = os.getpid()
        for win in ipc.clients():
            if win.get("pid") == pid:
                return win
        return None

    def _set_compact(self, compact: bool):
        if compact == self._compact:
            return
        self._compact = compact
        self.header.set_visible(not compact)
        self.footer.set_visible(not compact)

        # Order matters. A Stack still measures hidden children, so the strip
        # cannot shrink below the full editor's minimum until the big child is
        # really hidden -- but a Stack also refuses to switch *to* a hidden
        # child. Setting visibility first and the visible child second keeps
        # both true; the other way round leaves the window showing the strip at
        # full size.
        full = self.stack.get_child_by_name("full")
        if full:
            full.set_visible(not compact)
        self.stack.set_visible_child_name("compact" if compact else "full")
        if compact:
            # Carry the current status across; notes are computed before the
            # switch, so the strip would otherwise come up with a blank line.
            lines = [s for s in (self.report.get_label() or "").split("\n") if s]
            sub = lines[0] if lines else ""
            self.compact_sub.set_label(sub.removeprefix("Previewing: "))

        own = self._own_window()
        if own is None:
            return
        sel = f"address:{own['address']}"
        try:
            if compact:
                self._restore_geometry = (own["at"][:], own["size"][:],
                                          own["floating"])
                # Asks for 96 tall; something below GTK enforces ~200 and wins.
                # Harmless -- the strip is cornered either way -- but that is
                # why there is empty space in it.
                w, h = 460, 96
                ipc.dispatch(f'hl.dsp.window.float({{ action = "enable", window = "{sel}" }})')
                ipc.dispatch(f'hl.dsp.window.pin({{ action = "enable", window = "{sel}" }})')
                ipc.dispatch(
                    f'hl.dsp.window.resize({{ x = {w}, y = {h}, relative = false, window = "{sel}" }})')
                # Resize is async and GTK enforces a minimum height, so the
                # size right now is neither what we asked for nor what we will
                # get. Corner it once it has settled.
                GLib.timeout_add(200, self._snap_to_corner)
            elif self._restore_geometry:
                at, size, was_floating = self._restore_geometry
                ipc.dispatch(f'hl.dsp.window.pin({{ action = "disable", window = "{sel}" }})')
                ipc.dispatch(
                    f'hl.dsp.window.resize({{ x = {size[0]}, y = {size[1]}, relative = false, window = "{sel}" }})')
                ipc.dispatch(
                    f'hl.dsp.window.move({{ x = {at[0]}, y = {at[1]}, relative = false, window = "{sel}" }})')
                if not was_floating:
                    ipc.dispatch(f'hl.dsp.window.float({{ action = "disable", window = "{sel}" }})')
        except ipc.HyprError:
            pass  # worst case the strip sits wherever the layout put it

    def _snap_to_corner(self):
        """Move the settled compact strip to the bottom-right of its monitor."""
        if not self._compact:
            return GLib.SOURCE_REMOVE
        own = self._own_window()
        if own is None:
            return GLib.SOURCE_REMOVE
        mon = self._monitor_for(own)
        aw, ah = own["size"]
        x = max(mon["x"], mon["x"] + mon["width"] - aw - 24)
        y = max(mon["y"], mon["y"] + mon["height"] - ah - 24)
        try:
            ipc.dispatch(
                f'hl.dsp.window.move({{ x = {x}, y = {y}, relative = false, '
                f'window = "address:{own["address"]}" }})')
        except ipc.HyprError:
            pass
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _monitor_for(win: dict) -> dict:
        mons = ipc.monitors()
        for mon in mons:
            if mon.get("id") == win.get("monitor"):
                return mon
        return mons[0]

    def _on_compact_stop(self, *_a):
        self.preview_switch.set_active(False)
        self._set_compact(False)

    def _build_body(self) -> Gtk.Widget:
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL,
                          position=760, shrink_start_child=False,
                          shrink_end_child=False)
        paned.set_start_child(self._build_form())
        paned.set_end_child(self._build_output())
        return paned

    # -- left: the form ---------------------------------------------------

    def _build_form(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()

        page.add(self._group_window())
        page.add(self._group_rules())

        # The rule form only makes sense once there is a rule to edit. Shown
        # when one is loaded or a draft is started; hidden otherwise, so an
        # empty window does not present fields that write to nothing.
        self._form_groups = [self._group_name(), self._group_match(),
                             self._group_effects()]
        for group in self._form_groups:
            page.add(group)
        self._sync_form_visibility()

        scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroller.set_child(page)
        return scroller

    # -- the combined "existing rules" section ----------------------------

    def _group_window(self) -> Adw.PreferencesGroup:
        self._window_group = Adw.PreferencesGroup(title="Window")

        change = Gtk.MenuButton(label="Change", valign=Gtk.Align.CENTER)
        change.add_css_class("flat")
        change.set_tooltip_text("Point this editor at a different window")
        change.set_popover(self._window_popover())
        self._window_group.set_header_suffix(change)

        self._window_row = self._row_picked_window()
        self._window_group.add(self._window_row)
        return self._window_group

    # -- switching target window ------------------------------------------

    def _window_popover(self) -> Gtk.Popover:
        popover = Gtk.Popover(width_request=420)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      margin_top=8, margin_bottom=8,
                      margin_start=8, margin_end=8)

        pick = Gtk.Button(valign=Gtk.Align.CENTER)
        pick.set_child(Adw.ButtonContent(icon_name="find-location-symbolic",
                                         label="Click a window to pick it"))
        pick.connect("clicked", lambda *_: (popover.popdown(),
                                            self._repick_by_click()))
        box.append(pick)

        search = Gtk.SearchEntry(placeholder_text="Filter by class or title")
        box.append(search)

        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")

        scroller = Gtk.ScrolledWindow(min_content_height=260,
                                      max_content_height=420, propagate_natural_height=True)
        scroller.set_child(listbox)
        box.append(scroller)
        popover.set_child(box)

        popover.connect("show", lambda *_: self._fill_window_list(
            listbox, search.get_text(), popover))
        search.connect("search-changed", lambda e: self._fill_window_list(
            listbox, e.get_text(), popover))
        return popover

    def _fill_window_list(self, listbox: Gtk.ListBox, needle: str,
                          popover: Gtk.Popover):
        while (child := listbox.get_first_child()) is not None:
            listbox.remove(child)

        import os
        own_pid = os.getpid()
        needle = (needle or "").strip().lower()

        try:
            windows = ipc.selectable_windows()
        except ipc.HyprError:
            windows = []

        shown = 0
        for win in windows:
            # Skip ourselves: retargeting the editor at itself would preview
            # effects on the window you are reading.
            if win.get("pid") == own_pid:
                continue
            label = f"{win.get('class')} {win.get('title')}".lower()
            if needle and needle not in label:
                continue
            row = gtkutil.action_row(
                title=win.get("class") or "window",
                subtitle=(win.get("title") or "no title")[:70],
                activatable=True,
            )
            ws = (win.get("workspace") or {}).get("name")
            tag = Gtk.Label(label=str(ws))
            tag.add_css_class("dim-label")
            tag.add_css_class("caption")
            row.add_suffix(tag)
            if win["address"] == self.window_info.get("address"):
                row.add_prefix(Gtk.Image.new_from_icon_name("object-select-symbolic"))
            row.connect("activated", lambda _r, w=win: (popover.popdown(),
                                                        self._switch_target(w)))
            listbox.append(row)
            shown += 1

        if not shown:
            empty = gtkutil.action_row(title="Nothing matches", activatable=False)
            empty.add_css_class("dim-label")
            listbox.append(empty)

    HIDDEN_WORKSPACE = f"special:{branding.CLI_NAME}-picking"

    def _move_self(self, workspace: str, own: dict | None = None) -> bool:
        own = own or self._own_window()
        if own is None:
            return False
        try:
            ipc.dispatch(
                f'hl.dsp.window.move({{ workspace = "{workspace}", '
                f'follow = false, window = "address:{own["address"]}" }})')
            return True
        except ipc.HyprError:
            return False

    def _repick_by_click(self):
        """Pick a new target, with the editor out of the way while you do.

        slurp waits on a human, so it must not run on the GTK main loop:
        blocking there for the seconds it takes someone to aim is exactly what
        makes the compositor decide the app has hung and offer to kill it. The
        pick runs on a worker thread and comes back through the main loop.

        Hiding has to be compositor-side for the same reason the thread is
        needed -- and two obvious ways do not work: `set_prop opacity 0` leaves
        the *focused* window fully visible, and `alter_zorder bottom` does
        nothing when the editor is alone on its workspace. Moving it away
        genuinely removes it, and moving it back restores its geometry.
        """
        if self._picking:
            return
        own = self._own_window()
        home = (own or {}).get("workspace", {}).get("name")
        parked = bool(home) and self._move_self(self.HIDDEN_WORKSPACE, own)
        self._picking = True
        exclude = {own["address"]} if own else None

        def worker():
            result, error = None, None
            try:
                result = picker.pick_window(exclude=exclude)
            except picker.Cancelled:
                pass
            except Exception as exc:              # noqa: BLE001 - reported below
                error = str(exc)
            GLib.idle_add(finish, result, error)

        def finish(result, error):
            self._picking = False
            if parked:
                # Not conditional on success: an editor left on a special
                # workspace looks exactly like the app having crashed.
                if not self._move_self(home) and not self._move_self(home):
                    self._toast("Could not bring the editor back — it is on "
                                f"{self.HIDDEN_WORKSPACE}")
            if error:
                self._toast(error)
            elif result:
                self._switch_target(result)
            return GLib.SOURCE_REMOVE

        threading.Thread(target=worker, daemon=True).start()

    def _switch_target(self, win: dict):
        """Point the whole editor at a different window."""
        if win.get("address") == self.window_info.get("address"):
            return
        if self._is_dirty():
            self._confirm_discard(
                lambda: self._do_switch_target(win),
                heading="Change window and lose these changes?")
            return
        self._do_switch_target(win)

    def _do_switch_target(self, win: dict):
        # Undo anything applied to the window we are leaving; otherwise it
        # keeps a preview it has no way to lose.
        try:
            self.preview.revert()
        except ipc.HyprError:
            pass
        self.preview_switch.set_active(False)
        self._set_compact(False)

        self.window_info = win
        self.preview = preview_mod.Preview(win["address"])
        self.set_title(f"Window Rule — {win.get('class') or 'window'}")
        self.header.set_title_widget(Adw.WindowTitle(
            title=win.get("class") or "window",
            subtitle=(win.get("title") or "")[:70]))

        self._window_group.remove(self._window_row)
        self._window_row = self._row_picked_window()
        self._window_group.add(self._window_row)

        self._rescan_only()
        self._draft = False        # a new target is not a new draft
        self._reset_form()
        ours = [f for f in self.existing if f.editable and f.enabled]
        if len(ours) == 1 and self.store.prefs.auto_load_single_match:
            self._load_rule(ours[0])
        else:
            self._rebuild_existing()
            self._refresh()
        self._toast(f"Now editing rules for {win.get('class')}")

    def _group_rules(self) -> Adw.PreferencesGroup:
        """Built by hand rather than as a plain PreferencesGroup, so the list
        can carry its own sub-heading, scope switcher and a + in the corner."""
        # No group title: the tabs above already name the view, and the
        # per-scope heading below says which one, so a third label would just
        # be noise.
        group = Adw.PreferencesGroup()

        self.rules_heading = Gtk.Label(label="Matching rules", xalign=0)
        self.rules_heading.add_css_class("heading")
        self.rules_desc = Gtk.Label(xalign=0, wrap=True)
        self.rules_desc.add_css_class("caption")
        self.rules_desc.add_css_class("dim-label")

        self.rules_search = Gtk.SearchEntry(
            placeholder_text="Filter by name, file, match or effect",
            margin_top=6, visible=False)
        self.rules_search.connect(
            "search-changed", lambda *_: self._rebuild_existing())

        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                         margin_bottom=8)
        header.append(self.rules_heading)
        header.append(self.rules_desc)
        header.append(self.rules_search)
        group.add(header)
        self._sync_scope_labels()

        # A box rather than one list: the All scope groups rules under the
        # file that defines them, which needs a heading and a separate
        # boxed-list per file.
        self._rules_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                  spacing=0)
        group.add(self._rules_box)

        # Shown in place of the list while editing, so there is always one
        # obvious way back.
        self._edit_banner = gtkutil.action_row(
            title="Editing", visible=False,
            subtitle="Save when you are done, or discard to go back.")
        self._edit_banner.add_css_class("accent")
        self._edit_banner.add_prefix(
            Gtk.Image.new_from_icon_name("document-edit-symbolic"))
        back = Gtk.Button(valign=Gtk.Align.CENTER)
        back.set_child(Adw.ButtonContent(icon_name="go-previous-symbolic",
                                         label="Back"))
        back.connect("clicked", lambda *_: self._confirm_discard(self._leave_edit))
        self._edit_banner.add_suffix(back)
        group.add(self._edit_banner)

        # Everything that belongs to browsing, hidden while editing.
        self._browse_widgets = [self.scope_bar, header, self._rules_box]

        self._rebuild_existing()
        return group

    @property
    def has_selection(self) -> bool:
        """Whether something is actually being edited."""
        return bool(self._draft or self._editing_id or self._template_edit)

    # -- browse vs edit ---------------------------------------------------

    def _snapshot(self) -> tuple:
        """What the form holds, in a form that can be compared later."""
        rule = self.build_rule()
        return (
            rule.name or "",
            tuple(sorted((k, str(v)) for k, v in rule.match.items())),
            tuple(sorted((k, str(v)) for k, v in rule.effects.items())),
        )

    def _set_baseline(self):
        self._baseline = self._snapshot()

    def _is_dirty(self) -> bool:
        """Whether anything was actually typed since editing began.

        Compared against a baseline taken on entry rather than "is anything
        filled in", so a draft that only holds the matcher we seeded from the
        window does not count -- prompting for that would be a nag about work
        the user never did.
        """
        if not self.has_selection:
            return False
        if self._raw_dirty:
            return True
        return self._snapshot() != getattr(self, "_baseline", None)

    def _confirm_discard(self, proceed, heading="Discard unsaved changes?"):
        """Run `proceed` — after confirmation if there is work to lose."""
        if not self._is_dirty():
            proceed()
            return
        dialog = Adw.AlertDialog(
            heading=heading,
            body="The changes you have made here have not been saved.")
        dialog.add_response("keep", "Keep editing")
        dialog.add_response("discard", "Discard")
        dialog.set_response_appearance("discard",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("keep")
        dialog.connect("response",
                       lambda _d, r: proceed() if r == "discard" else None)
        dialog.present(self)

    def _editing_label(self) -> str:
        if self._template_edit is not None:
            what = "template"
            name = (self.name_entry.get_text().strip()
                    or self._template_edit.title)
        elif self._editing_id:
            what = "rule"
            name = self.name_entry.get_text().strip() or self._editing_id
        else:
            what = "new rule"
            name = self.name_entry.get_text().strip() or "unnamed"
        return f"Editing {what} — {name}"

    def _leave_edit(self):
        """Back to browsing. Assumes any confirmation already happened."""
        if self._template_edit is not None:
            self._finish_template_edit()
            return
        self._editing_id = None
        self._draft = False
        self._reset_form()
        self._rebuild_existing()
        self._refresh()

    def _on_template_expanded(self, row, t: templates.Template):
        key = ("template", t.id)
        if not row.get_expanded():
            if self._viewed_key == key:
                self._viewed_source = None
                self._viewed_key = None
                self._viewed_label = "Rules"
                self._refresh()
            return
        self._viewed_key = key
        self._viewed_source = self._source_for_template(t)
        self._viewed_label = "Template rule"
        self._refresh()

    def _source_for_template(self, t: templates.Template) -> str:
        """What activating this template would write.

        A template has no file to quote, unlike a rule, so this is rendered
        rather than read -- but it is rendered through the same emitter that
        would write it, so what you see is what you would get.
        """
        comment = "--" if self.store.dialect == "lua" else "#"
        origin = ("shipped" if t.builtin and not t.overridden else
                  "shipped, edited" if t.builtin else "yours")
        lines = [f"{comment} template “{t.title}”  ·  {origin}"]

        state, rules = self._template_state(t)
        if state == "enabled":
            lines.append(f"{comment} already active as a rule in your config")
        elif state == "disabled":
            lines.append(f"{comment} present in your config but deactivated")
        else:
            lines.append(f"{comment} not in your config yet — this is what "
                         f"Activate would write")

        if not t.is_directly_usable():
            lines.append(f"{comment} no match criteria: Use it and fill them "
                         f"in, it cannot be activated as-is")

        body = emit.render(t.to_rule(), self.store.dialect)
        if t.id:
            body = self.store.template_marker(t.id) + "\n" + body
        return "\n".join(lines) + "\n\n" + body

    def _on_row_expanded(self, row, found: scan.FoundRule):
        if not row.get_expanded():
            if self._viewed_key == found.key:
                self._viewed_source = None
                self._viewed_key = None
                self._viewed_label = "Rules"
                self._refresh()
            return
        self._viewed_key = found.key
        self._viewed_source = self._source_for(found)
        self._viewed_label = "Rule source"
        self._refresh()

    def _source_for(self, found: scan.FoundRule) -> str:
        """The rule exactly as it sits in the file, with a little context."""
        comment = "--" if found.path.suffix == ".lua" else "#"
        lines = [
            # Just the file name: the section heading above the row already
            # shows the directory, and an absolute path wraps over four lines.
            f"{comment} {found.path.name}  ·  position {found.order} in "
            f"evaluation order"
            + ("" if found.editable else f"  ·  {branding.outside_app()}"),
        ]
        if not found.enabled:
            lines.append(f"{comment} deactivated — commented out")
        body = (found.site.text if found.site
                else emit.render(model.rule_from_parsed(found.rule),
                                 self.store.dialect))
        return "\n".join(lines) + "\n\n" + body

    def _preview_applies(self) -> bool:
        """Whether previewing the form's rule on the picked window is honest.

        Editing a rule for some other app -- easy to do from the All scope --
        would otherwise apply its effects to whatever window happens to be in
        the editor, which is a lie about what the rule does.
        """
        if not self.has_selection:
            return False
        rule = self.build_rule()
        if not rule.match:
            return False
        return scan.rule_matches({"match": rule.match}, self.window_info)[0]

    def _sync_form_visibility(self):
        """One window, two modes.

        Browsing shows the list and hides the form; editing does the reverse,
        so there is no way to be halfway through a rule and not notice, and no
        way to wander into another scope mid-edit. A second window would give
        the same focus, but the form is large enough that the dialog would be
        near-fullscreen anyway, and it would tangle live preview -- which parks
        and resizes *this* window.
        """
        editing = self.has_selection
        for group in getattr(self, "_form_groups", []):
            group.set_visible(editing)

        for widget in getattr(self, "_browse_widgets", []):
            widget.set_visible(not editing)
        if hasattr(self, "_edit_banner"):
            self._edit_banner.set_visible(editing)
            if editing:
                self._edit_banner.set_title(self._editing_label())
        if hasattr(self, "cancel_btn"):
            self.cancel_btn.set_label("Discard" if editing else "Close")
        if hasattr(self, "save_btn"):
            self.save_btn.set_visible(editing)

        if not hasattr(self, "preview_switch"):
            return
        can = self._preview_applies()
        self.preview_switch.set_sensitive(can)
        if not can and self.preview_switch.get_active():
            self.preview_switch.set_active(False)
        self.preview_switch.set_tooltip_text(
            None if can else
            "This rule does not match the window in the editor, so there is "
            "nothing here to preview it on. Change the window above, or edit "
            "a rule that matches it.")

    # -- scope ------------------------------------------------------------

    @property
    def showing_all(self) -> bool:
        return getattr(self, "_scope", "window") == "all"

    @property
    def showing_templates(self) -> bool:
        return getattr(self, "_scope", "window") == "templates"

    def _on_add_clicked(self):
        if self.showing_templates:
            self._new_template()
        else:
            self._start_new_rule()

    def _on_scope_changed(self, *_a):
        self._scope = self.scope_toggle.get_active_name() or "window"
        self._viewed_source = None
        self._viewed_key = None
        self._viewed_label = "Rules"
        self._sync_scope_labels()
        self._rescan_only()
        self._rebuild_existing()
        self._refresh()

    def _sync_scope_labels(self):
        if self.showing_templates:
            self.rules_heading.set_label("Templates")
            self.rules_desc.set_label(
                "Reusable rules. Use one as the starting point for a new "
                "rule, or activate it to write it out on its own match "
                "criteria — no window needs to be involved.")
            self.rules_search.set_placeholder_text(
                "Filter by name, description, match or effect")
            self.add_btn.set_tooltip_text("Create a new template")
        elif self.showing_all:
            self.rules_heading.set_label("All rules")
            self.rules_desc.set_label(
                "Every window rule in your config, in evaluation order. "
                "Which one wins depends on the window, so nothing is marked "
                "active here.")
            self.rules_search.set_placeholder_text(
                "Filter by name, file, match or effect")
            self.add_btn.set_tooltip_text("Start a new blank rule")
        else:
            self.rules_heading.set_label("Matching rules")
            self.rules_desc.set_label(
                "Listed in evaluation order — the last rule to set a "
                "property is the one that wins.")
            self.add_btn.set_tooltip_text("Start a new blank rule")
        self.rules_search.set_visible(self.showing_all or self.showing_templates)
        self.reuse_btn.set_visible(not self.showing_templates)

    def _needle(self) -> str:
        if not (self.showing_all or self.showing_templates):
            return ""
        return (self.rules_search.get_text() or "").strip().lower()

    def _filtered(self, rules: list[scan.FoundRule]) -> list[scan.FoundRule]:
        needle = self._needle()
        if not needle:
            return rules
        out = []
        for f in rules:
            hay = " ".join([
                f.name, f.path.name, f.match_summary(), f.summary(),
            ]).lower()
            if needle in hay:
                out.append(f)
        return out

    def active_rule(self) -> scan.FoundRule | None:
        """The rule that actually wins: the last enabled one to be evaluated.

        Not the one loaded in the form -- what is in the editor is a draft, and
        calling that "active" would misreport what the window is doing now.

        Only meaningful for a specific window. In the All scope the list spans
        rules for many different windows, and "active" would be a claim about
        nothing in particular, so there is no active rule there.
        """
        if self.showing_all:
            return None
        enabled = [f for f in self.existing if f.enabled]
        return max(enabled, key=lambda f: f.order) if enabled else None

    def _add_section(self, title: str | None = None,
                     subtitle: str | None = None) -> Gtk.ListBox:
        """A boxed list, optionally under a heading."""
        if title:
            head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1,
                           margin_top=14, margin_bottom=6)
            lbl = Gtk.Label(label=title, xalign=0)
            lbl.add_css_class("heading")
            head.append(lbl)
            if subtitle:
                sub = Gtk.Label(label=subtitle, xalign=0,
                                ellipsize=Pango.EllipsizeMode.MIDDLE)
                sub.add_css_class("caption")
                sub.add_css_class("dim-label")
                head.append(sub)
            self._rules_box.append(head)
        lst = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        lst.add_css_class("boxed-list")
        self._rules_box.append(lst)
        return lst

    def rendered_rows(self) -> list:
        """Every row currently in the list, across sections."""
        out = []
        child = self._rules_box.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.ListBox):
                row = child.get_first_child()
                while row is not None:
                    out.append(row)
                    row = row.get_next_sibling()
            child = child.get_next_sibling()
        return out

    def _rebuild_existing(self):
        if not hasattr(self, "_rules_box"):
            return
        while (child := self._rules_box.get_first_child()) is not None:
            self._rules_box.remove(child)

        if self.showing_templates:
            self._build_template_rows(self._add_section())
            return

        self._draft_row = None
        lst = self._add_section()
        if self._template_edit is not None:
            row = gtkutil.action_row(
                title=f"Editing template \u201c{self._template_edit.title}\u201d",
                subtitle="Saving updates the template. Your window rules are "
                         "untouched until you use or activate it.")
            row.add_prefix(Gtk.Image.new_from_icon_name(
                "view-list-bullet-symbolic"))
            done = Gtk.Button(label="Done", valign=Gtk.Align.CENTER)
            done.connect("clicked", lambda *_: self._finish_template_edit())
            row.add_suffix(done)
            row.add_css_class("accent")
            lst.append(row)
        elif self._draft:
            self._draft_row = self._build_draft_row()
            lst.append(self._draft_row)
            self._update_draft_row()

        active = self.active_rule()
        rules = self._filtered(self.existing)

        if not rules and not self._draft and self.showing_all:
            searching = bool((self.rules_search.get_text() or "").strip())
            empty = gtkutil.action_row(
                title=("Nothing matches that filter" if searching
                       else "No window rules found in your config"),
                subtitle=("Try a different term." if searching else
                          f"Nothing in the files {APP_NAME} scans defines one yet."))
            empty.add_css_class("dim-label")
            lst.append(empty)
            return

        if not rules and not self._draft:
            empty = gtkutil.action_row(
                title="No matching rules found for this window",
                subtitle="Nothing in your config applies to it yet.",
            )
            empty.add_prefix(Gtk.Image.new_from_icon_name(
                "dialog-information-symbolic"))
            lst.append(empty)

            # The buttons get their own row rather than riding along as a
            # suffix. An ActionRow gives its suffix all the width it asks for
            # and squeezes the title into whatever is left, so three buttons
            # collapse the text to one character per line.
            buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                              halign=Gtk.Align.END, margin_top=10,
                              margin_bottom=10, margin_start=12, margin_end=12)
            reuse = Gtk.Button(label="Use an existing rule",
                               tooltip_text="Clone one of your rules for this "
                                            "window, or add this window to it")
            reuse.connect("clicked", lambda *_: self._open_reuse())
            buttons.append(reuse)
            from_tpl = Gtk.Button(label="Create from template")
            from_tpl.connect("clicked", lambda *_: self._open_templates())
            buttons.append(from_tpl)
            create = Gtk.Button(label="Create a rule")
            create.add_css_class("suggested-action")
            create.connect("clicked", lambda *_: self._start_new_rule())
            buttons.append(create)

            button_row = Gtk.ListBoxRow(activatable=False, selectable=False)
            button_row.set_child(buttons)
            lst.append(button_row)
            return

        if self.showing_all:
            # Grouped by defining file, files kept in load order so the
            # sections themselves read as the evaluation order.
            current, section = None, None
            for found in rules:
                if found.path != current:
                    current = found.path
                    section = self._add_section(
                        found.path.name, str(found.path.parent))
                section.append(self._row_for_rule(found, False))
            return

        for found in sorted(
                rules,
                key=lambda f: (active is None or f.key != active.key, f.order)):
            lst.append(self._row_for_rule(
                found, active is not None and found.key == active.key))

    # -- templates, shown in the same list --------------------------------

    def _template_rules(self) -> dict:
        """template id -> the rules created from it, across the whole config.

        Scanned once per rebuild rather than per row: with a dozen templates
        that is a dozen scans saved, and they would all return the same thing.
        """
        out: dict[str, list] = {}
        try:
            for f in scan.find_all(self.store.config_dir, self.store.path,
                                   self.store.dialect):
                if f.template_id:
                    out.setdefault(f.template_id, []).append(f)
        except (ipc.HyprError, OSError):
            pass
        return out

    def _build_template_rows(self, lst):
        needle = self._needle()
        self._tpl_rules = self._template_rules()
        shown = 0

        # An unsaved template is not in the store yet, so it has to be drawn
        # explicitly -- otherwise pressing + shows a form with nothing in the
        # list to say what is being edited.
        if self._template_edit is not None and self._template_is_new:
            row = gtkutil.action_row(
                title=f"{self.name_entry.get_text().strip() or 'New template'}"
                      "  ·  Not saved",
                subtitle="Fill in the form below and save, or discard it.")
            row.add_css_class("accent")
            row.add_prefix(Gtk.Image.new_from_icon_name("document-new-symbolic"))
            discard = Gtk.Button(icon_name="edit-clear-symbolic",
                                 tooltip_text="Discard this template",
                                 valign=Gtk.Align.CENTER)
            discard.add_css_class("flat")
            discard.connect("clicked", lambda *_: self._finish_template_edit())
            row.add_suffix(discard)
            lst.append(row)
            shown += 1
        for t in self.template_store.all():
            if needle:
                hay = " ".join([t.title, t.description, t.match_summary(),
                                t.summary()]).lower()
                if needle not in hay:
                    continue
            lst.append(self._row_for_template(t))
            shown += 1

        if not shown:
            empty = gtkutil.action_row(
                title=("Nothing matches that filter" if needle
                       else "No templates"),
                subtitle=("Try a different term." if needle
                          else "Press + to create one."))
            empty.add_css_class("dim-label")
            lst.append(empty)

    def _template_state(self, t: templates.Template) -> tuple[str, list]:
        """"none" | "enabled" | "disabled", plus the rules it produced."""
        rules = getattr(self, "_tpl_rules", {}).get(t.id, [])
        if not rules:
            return "none", []
        return ("enabled" if any(r.enabled for r in rules) else "disabled",
                rules)

    def _set_template_rules(self, t: templates.Template, enable: bool):
        state, rules = self._template_state(t)
        problems = []
        for r in rules:
            if r.enabled == enable or not r.editable:
                continue
            try:
                res = self.store.set_enabled(r.managed_id, enable)
                if res.rolled_back:
                    problems.append(res.config_errors)
            except (ValueError, ipc.HyprError, OSError) as exc:
                problems.append(str(exc))
        self._rescan_only()
        self._rebuild_existing()
        self._toast("Reactivated" if enable else "Deactivated" if not problems
                    else "Finished with problems — see the notes")
        if problems:
            self.report.set_label("\n".join(problems))

    def _row_for_template(self, t: templates.Template) -> Adw.ExpanderRow:
        editing = (self._template_edit is not None
                   and self._template_edit.id == t.id)
        state, tpl_rules = self._template_state(t)
        badges = []
        if state == "enabled":
            badges.append("active")
        elif state == "disabled":
            badges.append("deactivated")
        if editing:
            badges.append("editing")
        if t.builtin:
            badges.append("edited" if t.overridden else "shipped")
        else:
            badges.append("yours")

        row = gtkutil.expander_row(
            title=t.title + "  ·  " + " · ".join(badges),
            subtitle=t.description or "no description",
        )
        # The action buttons leave a narrow text column, and these
        # descriptions are a couple of sentences -- unclamped they wrap to six
        # lines each and eleven templates become an enormous list. Truncated
        # here, in full when expanded.
        row.set_subtitle_lines(2)
        if editing:
            row.add_css_class("accent")
        row.add_prefix(Gtk.Image.new_from_icon_name(
            "emblem-ok-symbolic" if editing else "view-list-bullet-symbolic"))
        row.add_suffix(self._template_actions(t))

        row.connect("notify::expanded",
                    lambda r, _p, tt=t: self._on_template_expanded(r, tt))

        if t.description:
            row.add_row(self._detail("about", t.description))
        row.add_row(self._detail("match", t.match_summary()))
        row.add_row(self._detail("sets", t.summary()))
        if not t.is_directly_usable():
            row.add_row(self._detail(
                "note", "No match criteria — Use it and fill them in; it "
                        "cannot be activated on its own."))
        for url in t.sources:
            row.add_row(self._detail("source", url))
        if t.unknown_keys():
            row.add_row(self._detail(
                "not supported by this build", ", ".join(t.unknown_keys())))
        return row

    def _template_actions(self, t: templates.Template) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4,
                      valign=Gtk.Align.CENTER)

        use = Gtk.Button(label="Use", valign=Gtk.Align.CENTER,
                         tooltip_text="Load into the editor as a new draft")
        use.connect("clicked", lambda *_: self._use_template(t))
        box.append(use)

        # What the button offers depends on whether this template already has
        # a rule in the config, and whether that rule is switched on.
        state, rules = self._template_state(t)
        if state == "enabled":
            activate = Gtk.Button(label="Deactivate", valign=Gtk.Align.CENTER)
            activate.set_tooltip_text(
                f"Comment out the {len(rules)} rule(s) this template created")
            activate.connect("clicked",
                             lambda *_: self._set_template_rules(t, False))
        elif state == "disabled":
            activate = Gtk.Button(label="Reactivate", valign=Gtk.Align.CENTER)
            activate.add_css_class("suggested-action")
            activate.set_tooltip_text(
                "Switch its existing rule back on, rather than adding a "
                "second copy")
            activate.connect("clicked",
                             lambda *_: self._set_template_rules(t, True))
        else:
            activate = Gtk.Button(label="Activate", valign=Gtk.Align.CENTER)
            activate.add_css_class("suggested-action")
            activate.set_sensitive(t.is_directly_usable())
            activate.set_tooltip_text(
                "Write it out as a rule using its own match criteria"
                if t.is_directly_usable() else
                "This template has no match criteria, so there is nothing to "
                "match on yet — use it and fill them in")
            activate.connect("clicked", lambda *_: self._activate_template(t))
        box.append(activate)

        def icon(name, tip, handler, dim=False):
            b = Gtk.Button(icon_name=name, tooltip_text=tip,
                           valign=Gtk.Align.CENTER)
            b.add_css_class("flat")
            if dim:
                b.add_css_class("dim-label")
            b.connect("clicked", handler)
            box.append(b)
            return b

        icon("document-edit-symbolic", "Edit",
             lambda *_: self._edit_template(t))
        icon("edit-copy-symbolic", "Duplicate",
             lambda *_: self._duplicate_template(t))
        if t.builtin:
            reset = icon("edit-undo-symbolic", "Restore the shipped version",
                         lambda *_: self._reset_template(t))
            reset.set_sensitive(t.overridden)
        else:
            icon("user-trash-symbolic", "Delete",
                 lambda *_: self._delete_template(t), dim=True)
        return box

    def _new_template(self):
        """Start a template without writing it anywhere.

        It used to be saved the instant + was pressed, so wandering off left an
        empty "New template" behind -- clutter the user never asked to create.
        Rule drafts never did this; templates now match them, and nothing
        reaches templates.json until Save.
        """
        t = templates.Template(
            id=self.template_store.unique_id("new-template"),
            title="New template")
        self._edit_template(t, is_new=True)

    def _duplicate_template(self, t: templates.Template):
        copy = templates.Template(
            id=self.template_store.unique_id(t.id + "-copy"),
            title=t.title + " (copy)", description=t.description,
            match=dict(t.match), effects=dict(t.effects),
            sources=list(t.sources))
        self.template_store.save(copy)
        self._rebuild_existing()
        self._toast(f"Duplicated as “{copy.title}”")

    def _reset_template(self, t: templates.Template):
        self.template_store.delete(t.id)
        if self._template_edit is not None and self._template_edit.id == t.id:
            self._finish_template_edit()
        self._rebuild_existing()
        self._toast("Restored the shipped version")

    def _delete_template(self, t: templates.Template):
        dialog = Adw.AlertDialog(
            heading="Delete this template?",
            body=f"“{t.title}” will be removed. Rules already created from it "
                 "are not affected.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")

        def answered(_d, response):
            if response != "delete":
                return
            self.template_store.delete(t.id)
            if (self._template_edit is not None
                    and self._template_edit.id == t.id):
                self._finish_template_edit()
            self._rebuild_existing()
            self._toast("Deleted")

        dialog.connect("response", answered)
        dialog.present(self)

    # -- the unsaved draft ------------------------------------------------

    def _build_draft_row(self) -> Adw.ActionRow:
        row = gtkutil.action_row()
        row.add_css_class("accent")
        row.add_prefix(Gtk.Image.new_from_icon_name("document-new-symbolic"))
        discard = Gtk.Button(icon_name="edit-clear-symbolic",
                             tooltip_text="Discard this draft",
                             valign=Gtk.Align.CENTER)
        discard.add_css_class("flat")
        discard.connect("clicked", lambda *_: self._discard_draft())
        row.add_suffix(discard)
        return row

    def _update_draft_row(self):
        """Keep the draft row in step with the form as it is filled in."""
        if self._draft_row is None:
            return
        rule = self.build_rule()
        name = (self.name_entry.get_text().strip() or "unnamed")
        self._draft_row.set_title(f"{name}  ·  Not saved")

        match = " · ".join(f"{k} {v}" for k, v in rule.match.items()) or "no match yet"
        effects = ", ".join(sorted(rule.effects)) or "nothing yet"
        self._draft_row.set_subtitle(
            f"{self.store.path.name}  ·  match: {match}  ·  sets: {effects}")

    def _discard_draft(self):
        self._draft = False
        self._rebuild_existing()
        self._refresh()

    def _row_picked_window(self) -> Adw.ExpanderRow:
        win = self.window_info
        row = gtkutil.expander_row(
            title=win.get("class") or "window",
            subtitle="The window you picked — " +
                     ((win.get("title") or "")[:60] or "no title"),
        )
        row.add_prefix(Gtk.Image.new_from_icon_name("focus-windows-symbolic"))
        for label, value in (
            ("class", win.get("class")),
            ("initialClass", win.get("initialClass")),
            ("title", win.get("title")),
            ("initialTitle", win.get("initialTitle")),
            ("workspace", (win.get("workspace") or {}).get("name")),
            ("geometry", f"{win.get('at')} {win.get('size')}"),
            ("xwayland", win.get("xwayland")),
            ("address", win.get("address")),
        ):
            sub = gtkutil.action_row(title=label, subtitle=str(value))
            sub.add_css_class("property")
            row.add_row(sub)
        if model.title_is_volatile(win):
            warn = gtkutil.action_row(
                title="Title changed since this window opened",
                subtitle="Prefer initial_title — static effects are evaluated "
                         "against the title at map time.")
            warn.add_prefix(Gtk.Image.new_from_icon_name("dialog-warning-symbolic"))
            row.add_row(warn)
        return row

    def _row_for_rule(self, found: scan.FoundRule,
                      is_active: bool = False) -> Adw.ExpanderRow:
        is_editing = found.editable and found.managed_id == self._editing_id
        effects = found.effect_names()

        badges = []
        if is_editing:
            badges.append("editing")
        if not found.enabled:
            badges.append("deactivated")
        if not found.editable:
            badges.append(outside_app())
        # In the All scope, say which of these actually touch the window in
        # the editor -- otherwise the two views feel unrelated.
        if self.showing_all and found.matches_window:
            badges.append("matches this window")
        tpl = None
        if found.template_id:
            tpl = self.template_store.get(found.template_id)
            badges.append(f"from template “{tpl.title}”" if tpl
                          else "from a deleted template")

        title = ("Active Rule — " if is_active else "") + found.name
        if badges:
            title += "  ·  " + " · ".join(badges)

        row = gtkutil.expander_row(
            title=title,
            subtitle=f"{found.path.name}  ·  match: {found.match_summary()}"
                     f"  ·  sets: {', '.join(effects) or 'nothing'}",
        )
        if is_active:
            row.add_css_class("accent")
        else:
            # Everything that matches but is not the winner reads as secondary.
            row.add_css_class("dim-label")
        row.add_prefix(Gtk.Image.new_from_icon_name(
            "starred-symbolic" if is_active else
            "view-conceal-symbolic" if not found.enabled else
            # A distinct glyph so template-derived rules are spottable in a
            # long list without reading every badge.
            "view-list-bullet-symbolic" if found.template_id else
            "view-list-symbolic"))

        row.add_suffix(self._rule_actions(found, is_editing, is_active))
        # Expanding a row is "show me this one", so it also puts the rule's
        # real source in the pane -- exactly the text in the file, not a
        # re-rendering of what we parsed out of it.
        row.connect("notify::expanded",
                    lambda r, _p, f=found: self._on_row_expanded(r, f))

        if found.template_id:
            tpl_name = tpl.title if tpl else found.template_id
            row.add_row(self._detail("from template", tpl_name))
        for label, value in (
            ("file", str(found.path)),
            ("position", f"{found.order} in evaluation order"),
            ("active", "yes" if found.enabled else "no — commented out"),
        ):
            row.add_row(self._detail(label, value))
        for key, value in (found.rule.get("match") or {}).items():
            row.add_row(self._detail(f"match · {key}", str(value)))
        for key in effects:
            row.add_row(self._detail(f"sets · {key}", str(found.rule[key])))
        if found.unmatched_props:
            row.add_row(self._detail(
                "could not check", ", ".join(found.unmatched_props)))
        return row

    @staticmethod
    def _detail(label: str, value: str) -> Adw.ActionRow:
        row = gtkutil.action_row(title=label, subtitle=value)
        row.add_css_class("property")
        return row

    def _rule_actions(self, found: scan.FoundRule, is_editing: bool,
                      is_active: bool) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4,
                      valign=Gtk.Align.CENTER)

        def button(icon, tip, handler, danger=False):
            # Deliberately not styled destructive: a row of red icons reads as
            # alarming for what is a list you mostly browse, and the confirm
            # dialog is the actual guard.
            b = Gtk.Button(icon_name=icon, tooltip_text=tip,
                           valign=Gtk.Align.CENTER)
            b.add_css_class("flat")
            if danger:
                b.add_css_class("dim-label")
            b.connect("clicked", handler)
            box.append(b)
            return b

        if found.editable and not is_editing:
            button("document-edit-symbolic", "Load into the form",
                   lambda *_: self._load_rule(found))

        # Reuse, offered where the rules are actually browsed. Adding is only
        # shown when it would do something -- a rule the window is already
        # covered by, or one matching on nothing this window has, would give a
        # button whose only outcome is an explanation of why it did nothing.
        if merge.extendable_fields(found.rule, self.window_info) and not (
                found.editable is False and found.site is None):
            button("list-add-symbolic", "Add this window to this rule",
                   lambda *_: self._extend_rule_with_window(found))
        button("edit-paste-symbolic", "Clone this rule for this window",
               lambda *_: self._clone_rule_for_window(found))
        button(
            "media-playback-start-symbolic" if not found.enabled
            else "media-playback-pause-symbolic",
            "Reactivate" if not found.enabled else "Deactivate (comment out)",
            lambda *_: self._act_toggle(found),
        )
        button("view-list-bullet-symbolic", "Save as a template",
               lambda *_: self._save_rule_as_template(found))
        if found.editable:
            button("edit-copy-symbolic", "Duplicate",
                   lambda *_: self._act_duplicate(found))
            button("user-trash-symbolic", "Delete",
                   lambda *_: self._act_delete(found), danger=True)
        return box

    # -- actions on listed rules ------------------------------------------

    def _rescan(self):
        self._rescan_only()
        if self._editing_id and not any(
                f.managed_id == self._editing_id for f in self.existing):
            self._stop_editing()
        self._rebuild_existing()

    def _after_write(self, result, verb: str):
        if result.rolled_back:
            self._toast(f"{verb} rejected — config rolled back")
            self.report.set_label(result.config_errors)
        else:
            self._toast(verb)
        self._rescan()

    def _rescan_only(self):
        try:
            if self.showing_all:
                self.existing = scan.find_all(
                    self.store.config_dir, self.store.path, self.store.dialect)
                # find_all cannot know about a window; flag the ones that do
                # apply to the one in the editor so the badge means something.
                for f in self.existing:
                    f.matches_window = scan.rule_matches(
                        f.rule, self.window_info)[0]
            else:
                self.existing = scan.find_for_window(
                    self.window_info, self.store.config_dir, self.store.path,
                    self.store.dialect)
        except (ipc.HyprError, OSError):
            self.existing = []

    def _act_duplicate(self, found: scan.FoundRule):
        try:
            result = self.store.duplicate(found.managed_id)
        except (ValueError, ipc.HyprError, OSError) as exc:
            self._toast(f"Could not duplicate: {exc}")
            return
        self._after_write(result, "Duplicated")

    def _act_toggle(self, found: scan.FoundRule):
        want = not found.enabled
        if found.editable:
            try:
                result = self.store.set_enabled(found.managed_id, want)
            except (ValueError, ipc.HyprError, OSError) as exc:
                self._toast(f"Could not change: {exc}")
                return
            self._after_write(result, "Reactivated" if want else "Deactivated")
            return

        # Someone else's file: always confirm, and say exactly what happens.
        verb = "Reactivate" if want else "Deactivate"
        dialog = Adw.AlertDialog(
            heading=f"{verb} a rule in a file {APP_NAME} did not write?",
            body=(f"This edits {found.path} directly, commenting the rule "
                  f"{'back in' if want else 'out'} in place.\n\n"
                  f"Rule: {found.name} — sets {found.summary()}\n\n"
                  "A timestamped backup is made first, and nothing is ever "
                  "deleted from a file we do not own."),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("go", verb)
        dialog.set_response_appearance("go", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")

        def answered(_d, response):
            if response != "go":
                return
            try:
                result = store.RuleStore.toggle_foreign(
                    found.path, found.site, want)
            except (OSError, ipc.HyprError) as exc:
                self._toast(f"Could not change: {exc}")
                return
            self._after_write(result, f"{verb}d {found.path.name}")

        dialog.connect("response", answered)
        dialog.present(self)

    def _act_delete(self, found: scan.FoundRule):
        dialog = Adw.AlertDialog(
            heading="Delete this rule?",
            body=(f"{found.name} — sets {found.summary()}\n\n"
                  f"Removed from {found.path.name}. A backup is written first, "
                  "and Deactivate is the reversible alternative."),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")

        def answered(_d, response):
            if response != "delete":
                return
            try:
                self.store.remove(found.managed_id)
            except (ValueError, ipc.HyprError, OSError) as exc:
                self._toast(f"Could not delete: {exc}")
                return
            self._toast("Deleted")
            self._rescan()

        dialog.connect("response", answered)
        dialog.present(self)

    # -- reusing an existing rule -----------------------------------------

    def _all_rules(self) -> list[scan.FoundRule]:
        """Every rule in the config, whatever it matches.

        Read fresh rather than reusing self.existing: in the This Window scope
        that list holds only the rules that already apply, which is exactly the
        set a user reaching for "reuse an existing rule" is not looking at.
        """
        try:
            return scan.find_all(self.store.config_dir, self.store.path,
                                 self.store.dialect)
        except (ipc.HyprError, OSError) as exc:
            self._toast(f"Could not read your config: {exc}")
            return []

    def _open_reuse(self):
        rules = self._all_rules()
        if not rules:
            self._toast("No existing rules to reuse")
            return
        from .reuse_ui import ReuseRuleDialog
        ReuseRuleDialog(
            rules, self.window_info,
            on_clone=self._clone_rule_for_window,
            on_extend=self._extend_rule_with_window,
        ).present(self)

    def _clone_rule_for_window(self, found: scan.FoundRule):
        """Load a copy of `found` into the form, aimed at the picked window."""
        def proceed():
            self.scope_toggle.set_active_name("window")
            self._reset_form()
            self._template_edit = None
            rule = merge.clone_for_window(found.rule, self.window_info)
            self._apply_rule_to_form(rule)
            self._draft = True
            self._rebuild_existing()
            self._refresh()
            # A clone is a deliberate choice, like loading a template: losing
            # it to an accidental close would be the same annoyance as losing
            # typing, so it counts as unsaved work.
            self._baseline = None
            dropped = model.unknown_keys(found.rule)
            self._toast(
                f"Cloned “{found.name}” — nothing saved yet" +
                (f", dropped unknown: {', '.join(dropped)}" if dropped else ""))

        if self._is_dirty():
            self._confirm_discard(proceed)
        else:
            proceed()

    def _extend_rule_with_window(self, found: scan.FoundRule):
        """Widen an existing rule so it also matches the picked window."""
        fields = merge.extendable_fields(found.rule, self.window_info)
        if not fields:
            self._toast("This rule has no class or title to add a window to")
            return
        if len(fields) == 1:
            self._confirm_extend(found, fields[0])
            return

        # More than one identity field: widening the wrong one changes what the
        # rule means, so it is not a guess worth making on the user's behalf.
        dialog = Adw.AlertDialog(
            heading="Which field should match this window too?",
            body=(f"“{found.name}” matches on more than one. Adding to just "
                  "one of them is usually what you want — every match field "
                  "has to pass, so widening one keeps the others in force."),
        )
        dialog.add_response("cancel", "Cancel")
        for key in fields:
            dialog.add_response(key, f"{key} = {merge.window_value(self.window_info, key)}")
        dialog.set_default_response("cancel")
        dialog.connect(
            "response",
            lambda _d, r: None if r == "cancel" else self._confirm_extend(found, r))
        dialog.present(self)

    def _confirm_extend(self, found: scan.FoundRule, prop: str):
        """Show the exact before/after, then write it."""
        source = (found.site.uncommented() if found.site is not None
                  else self.store.read_block(found.managed_id) or "")
        dialect = "conf" if found.path.suffix == ".conf" else "lua"
        try:
            new_source, new_pattern = merge.plan_extend(
                found.rule, source, self.window_info, prop, dialect)
        except merge.Refused as why:
            self._toast(f"Cannot add this window: {why}")
            return

        old_pattern = str((found.rule.get("match") or {})[prop])
        where = ("in your generated rules file" if found.editable
                 else f"in {found.path.name}, a file {outside_app()}")

        # A value that has already drifted once will drift again, and the rule
        # would quietly stop matching. Worth saying before the write, not after.
        caution = ""
        if prop == "title" and model.title_is_volatile(self.window_info):
            caution = ("\n\nThis window's title has already changed since it "
                       "opened, so matching on it may not keep working. "
                       "initial_title holds the title it opened with.")
        elif prop == "class" and (
                self.window_info.get("class") !=
                self.window_info.get("initialClass")):
            caution = ("\n\nThis window's class differs from the one it opened "
                       "with, so matching on class may not keep working. "
                       "initial_class holds the original.")

        dialog = Adw.AlertDialog(
            heading="Add this window to the rule?",
            body=(f"“{found.name}” {where}.\n\n"
                  f"{prop}\n"
                  f"  before   {old_pattern}\n"
                  f"  after    {new_pattern}\n\n"
                  "Nothing else in the rule changes. A timestamped backup is "
                  "written first, and the config is restored if Hyprland "
                  "rejects the result." + caution),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("go", "Add this window")
        dialog.set_response_appearance("go", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")

        def answered(_d, response):
            if response != "go":
                return
            try:
                if found.editable:
                    result = self.store.update_raw(found.managed_id, new_source)
                else:
                    result = store.RuleStore.amend_foreign(
                        found.path, found.site, new_source)
            except (ValueError, ipc.HyprError, OSError) as exc:
                self._toast(f"Could not change: {exc}")
                return
            self._after_write(result, f"Added this window to “{found.name}”")

        dialog.connect("response", answered)
        dialog.present(self)

    # -- templates --------------------------------------------------------

    def _open_templates(self):
        """Templates are a scope of the same list, not a separate window."""
        self.scope_toggle.set_active_name("templates")

    def _apply_rule_to_form(self, rule: Rule):
        self.name_entry.set_text(rule.name or "")
        for rows, values in ((self._rows_match, rule.match),
                             (self._rows_effect, rule.effects)):
            for key, row in rows.items():
                row.set_enabled(key in values)
                if key in values:
                    row.set_value(values[key])

    def _use_template(self, template: templates.Template):
        """Load a template in as an unsaved draft, to adjust before saving."""
        # Back to the window's own rules: the draft being built belongs there,
        # and leaving the list on templates would hide what was just created.
        self.scope_toggle.set_active_name("window")
        self._reset_form()
        self._template_edit = None
        rule = template.to_rule()
        # Keep the window's own matcher if the template brings none of its own,
        # otherwise "Use" on a starting-point template produces a rule that
        # matches nothing.
        if not rule.match:
            rule.match = model.suggest_match(self.window_info)
        self._apply_rule_to_form(rule)
        self._draft = True
        self._rebuild_existing()
        self._refresh()
        # A template just loaded counts as work: it was chosen deliberately,
        # and losing it silently would be the same annoyance as losing typing.
        self._baseline = None
        missing = template.unknown_keys()
        self._toast(f"Loaded “{template.title}”" +
                    (f" — dropped unknown: {', '.join(missing)}" if missing else ""))

    def _activate_template(self, template: templates.Template):
        """Write the template out as a rule, on its own match criteria."""
        rule = template.to_rule()
        valid, why = rule.is_valid()
        if not valid:
            self._toast(f"Cannot activate: {why}")
            return
        try:
            result = self.store.save(rule, template_id=template.id)
        except (ValueError, ipc.HyprError, OSError) as exc:
            self._toast(f"Could not activate: {exc}")
            return
        if result.rolled_back:
            self._toast("Hyprland rejected it — config rolled back")
            self.report.set_label(result.config_errors)
            return
        self._draft = False
        # Show where it landed. A template usually matches something other
        # than the window in the editor -- polkit prompts, file pickers -- so
        # "this window" would give no sign that anything happened.
        self.scope_toggle.set_active_name("all")
        self._rescan()
        self._toast(f"Activated “{template.title}” — added to your rules")

    def _edit_template(self, template: templates.Template, is_new: bool = False):
        """Edit a template using the same form as a rule."""
        self._reset_form()
        self._draft = False
        self._template_edit = template
        self._template_is_new = is_new
        self._apply_rule_to_form(template.to_rule(name=template.title))
        self.save_btn.set_label("Save template")
        self._rebuild_existing()
        self._refresh()
        self._set_baseline()
        self._toast(f"Editing template “{template.title}” — "
                    "saving updates the template, not your config")

    def _save_template_edit(self):
        template = self._template_edit
        rule = self.build_rule()
        template.title = (self.name_entry.get_text().strip() or template.title)
        template.match = dict(rule.match)
        template.effects = dict(rule.effects)
        try:
            self.template_store.save(template)
        except OSError as exc:
            self._toast(f"Could not save template: {exc}")
            return
        self._template_is_new = False   # it exists on disk now
        self._rebuild_existing()   # title and effects changed in the list
        self._set_baseline()
        self._toast(f"Saved template “{template.title}”")

    def _finish_template_edit(self):
        self._template_edit = None
        self._template_is_new = False
        self.save_btn.set_label("Save & Reload")
        self._reset_form()
        self._rebuild_existing()
        self._refresh()

    def _save_rule_as_template(self, found: scan.FoundRule):
        from .templates_ui import SaveAsTemplateDialog
        rule = model.rule_from_parsed(found.rule)
        rule.name = rule.name or found.name
        SaveAsTemplateDialog(
            self.template_store, rule,
            on_saved=lambda t: self._toast(f"Saved template “{t.title}”"),
        ).present(self)

    def _reset_form(self):
        """Back to a blank rule seeded from the picked window. No draft."""
        self._viewed_source = None
        self._viewed_key = None
        self._viewed_label = "Rules"
        self._editing_id = None
        self._template_edit = None
        self._template_is_new = False
        self.save_btn.set_label("Save & Reload")
        self.name_entry.set_text("")
        self._raw_dirty = False
        self.raw_banner.set_visible(False)
        self.revert_btn.set_visible(False)
        for row in self._rows_effect.values():
            row.set_enabled(False)
        for row in self._rows_match.values():
            row.set_enabled(False)
        self._seed_from_window()

    def _start_new_rule(self):
        self._reset_form()
        self._draft = True
        self._rebuild_existing()
        self._refresh()
        # Baseline last: a fresh draft holds only the matcher seeded from the
        # window, which is not work the user did, so leaving must not prompt.
        self._set_baseline()

    def _load_rule(self, found: scan.FoundRule):
        rule = model.rule_from_parsed(found.rule)
        self.name_entry.set_text(rule.name or "")
        for rows, values in ((self._rows_match, rule.match),
                             (self._rows_effect, rule.effects)):
            for key, row in rows.items():
                row.set_enabled(key in values)
                if key in values:
                    row.set_value(values[key])

        self._editing_id = found.managed_id
        self._draft = False        # editing an existing rule, not drafting
        self._template_edit = None
        self.save_btn.set_label("Update & Reload")
        self._rebuild_existing()   # re-sorts, marks the current rule

        unknown = model.unknown_keys(found.rule)
        self._refresh()
        self._set_baseline()
        if unknown:
            self._toast("Not shown in the form: " + ", ".join(unknown))
        return GLib.SOURCE_REMOVE

    def _stop_editing(self):
        self._editing_id = None
        self.save_btn.set_label("Save & Reload")
        if hasattr(self, "_rules_box"):
            self._rebuild_existing()
            self._refresh()

    def _group_name(self) -> Adw.PreferencesGroup:
        # Not cosmetic: without somewhere to hold it, loading a named rule and
        # saving would quietly drop the name.
        group = Adw.PreferencesGroup()
        row = gtkutil.action_row(title="Name", subtitle="Optional label for the rule")
        self.name_entry = Gtk.Entry(valign=Gtk.Align.CENTER, width_chars=26,
                                    placeholder_text="unnamed")
        self.name_entry.connect("changed", lambda *_: self._schedule_refresh())
        row.add_suffix(self.name_entry)
        group.add(row)
        return group

    def _group_match(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Match",
            description="At least one is required. Different fields are ANDed "
                        "— all must match. Use + on a text field to accept any "
                        "of several values (OR).",
        )
        for f in catalog.PROPS:
            row = FieldRow(f, self._schedule_refresh)
            self._rows_match[f.key] = row
            group.add(row)
        return group

    def _group_effects(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Effects",
            description="At least one is required.",
        )
        common = [f for f in catalog.EFFECTS if f.key in catalog.COMMON_EFFECTS]
        rest = [f for f in catalog.EFFECTS if f.key not in catalog.COMMON_EFFECTS]

        for f in common:
            row = FieldRow(f, self._schedule_refresh)
            self._rows_effect[f.key] = row
            group.add(row)

        expander = gtkutil.expander_row(
            title="All other effects",
            subtitle=f"{len(rest)} more",
        )
        for f in rest:
            row = FieldRow(f, self._schedule_refresh)
            self._rows_effect[f.key] = row
            expander.add_row(row)
        group.add(expander)
        return group

    # -- right: generated text + report -----------------------------------

    def _build_output(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                       margin_top=10, margin_bottom=6,
                       margin_start=12, margin_end=12)
        self.output_title = Gtk.Label(label="Generated rule", xalign=0,
                                      hexpand=True)
        self.output_title.add_css_class("heading")
        head.append(self.output_title)

        self.revert_btn = Gtk.Button(
            label="Revert to form", visible=False,
            tooltip_text="Discard hand edits and go back to generating from "
                         "the fields",
        )
        self.revert_btn.add_css_class("flat")
        self.revert_btn.connect("clicked", self._on_revert_raw)
        head.append(self.revert_btn)

        copy = Gtk.Button(icon_name="edit-copy-symbolic",
                          tooltip_text="Copy to clipboard")
        copy.add_css_class("flat")
        copy.connect("clicked", self._on_copy)
        head.append(copy)
        box.append(head)

        # Editable on purpose: the catalog will always trail Hyprland by some
        # margin, and dropping to text is the escape hatch when it does.
        if HAVE_SOURCEVIEW:
            self.buffer = GtkSource.Buffer()
            self._set_source_language()
            self.buffer.set_highlight_syntax(True)
            self._apply_source_style()
            view = GtkSource.View(buffer=self.buffer, monospace=True,
                                  show_line_numbers=True, editable=True,
                                  auto_indent=True, insert_spaces_instead_of_tabs=True,
                                  tab_width=4)
            # Long class alternations run well past any sane window width;
            # wrapping beats a horizontal scrollbar on text you mostly read.
            view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        else:
            self.buffer = Gtk.TextBuffer()
            view = Gtk.TextView(buffer=self.buffer, monospace=True,
                                editable=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)

        self.buffer.connect("changed", self._on_buffer_changed)
        view.set_left_margin(10)
        view.set_top_margin(8)
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(view)
        box.append(scroller)

        self.raw_banner = Gtk.Label(
            xalign=0, visible=False, wrap=True,
            margin_start=12, margin_end=12, margin_top=4,
            label="Hand-edited — the fields above no longer drive this text.",
        )
        self.raw_banner.add_css_class("caption")
        self.raw_banner.add_css_class("warning")
        box.append(self.raw_banner)

        self.report = Gtk.Label(xalign=0, wrap=True, margin_start=12,
                                margin_end=12, margin_top=8, margin_bottom=10)
        self.report.add_css_class("caption")
        self.report.add_css_class("dim-label")
        box.append(self.report)
        return box

    def _set_source_language(self):
        if not HAVE_SOURCEVIEW:
            return
        lang = GtkSource.LanguageManager.get_default().get_language(
            "lua" if self.store.dialect == "lua" else "ini"
        )
        if lang:
            self.buffer.set_language(lang)

    def _apply_source_style(self):
        if not HAVE_SOURCEVIEW:
            return
        dark = Adw.StyleManager.get_default().get_dark()
        scheme_mgr = GtkSource.StyleSchemeManager.get_default()
        for name in (("Adwaita-dark", "solarized-dark", "oblivion") if dark
                     else ("Adwaita", "solarized-light", "classic")):
            scheme = scheme_mgr.get_scheme(name)
            if scheme:
                self.buffer.set_style_scheme(scheme)
                return

    # -- rule assembly ----------------------------------------------------

    def _seed_from_window(self):
        """Pre-fill the matcher we would have suggested on the CLI."""
        for key, value in model.suggest_match(self.window_info).items():
            row = self._rows_match.get(key)
            if row:
                row.set_value(str(value))
                row.set_enabled(True)

        for key, src in (
            ("class", "class"),
            ("initial_class", "initialClass"),
            ("title", "title"),
            ("initial_title", "initialTitle"),
        ):
            row = self._rows_match.get(key)
            raw = (self.window_info.get(src) or "").strip()
            if row and raw and not row.value:
                row.set_value(model.anchored(raw))

    def build_rule(self) -> Rule:
        rule = Rule(name=(self.name_entry.get_text().strip() or None))
        for key, row in self._rows_match.items():
            if row.enabled and row.has_usable_value():
                rule.match[key] = row.value
        for key, row in self._rows_effect.items():
            if row.enabled and row.has_usable_value():
                rule.effects[key] = row.value
        return rule

    # -- refresh ----------------------------------------------------------

    def _schedule_refresh(self):
        if self._debounce_id:
            GLib.source_remove(self._debounce_id)
        self._debounce_id = GLib.timeout_add(DEBOUNCE_MS, self._debounced)

    def _debounced(self):
        self._debounce_id = 0
        self._refresh()
        return GLib.SOURCE_REMOVE

    def buffer_text(self) -> str:
        return self.buffer.get_text(
            self.buffer.get_start_iter(), self.buffer.get_end_iter(), False
        )

    def _set_buffer_text(self, text: str):
        self._suppress_buffer = True
        self.buffer.set_text(text)
        self._suppress_buffer = False

    def _on_buffer_changed(self, *_a):
        if self._suppress_buffer or self._raw_dirty:
            return
        self._raw_dirty = True
        self.raw_banner.set_visible(True)
        self.revert_btn.set_visible(True)
        self.save_btn.set_sensitive(True)

    def _on_revert_raw(self, *_a):
        self._raw_dirty = False
        self.raw_banner.set_visible(False)
        self.revert_btn.set_visible(False)
        self._refresh()

    def _on_dialect_changed(self, drop, _param):
        dialect = ("lua", "conf")[drop.get_selected()]
        if dialect == self.store.dialect:
            return
        self.store = store.RuleStore(self.store.config_dir, dialect=dialect)
        self.dialect_label.set_label(f"→ {self.store.path.name}")
        self._set_source_language()
        # Hand edits are in the old dialect's syntax; they cannot carry over.
        self._on_revert_raw()

    def _refresh(self):
        self._sync_form_visibility()
        comment = "--" if self.store.dialect == "lua" else "#"

        if hasattr(self, "output_title"):
            self.output_title.set_label(
                "Generated rule" if self.has_selection else
                self._viewed_label if self._viewed_source else "Rules")

        if not self.has_selection:
            # Nothing selected: the code pane would otherwise show a validation
            # complaint about a rule the user never started.
            self._set_buffer_text(
                self._viewed_source if self._viewed_source else
                f"{comment} Expand a rule to see it, or press + to create one.")
            self.save_btn.set_sensitive(False)
            self.report.set_label("")
            return

        if self._raw_dirty:
            self._refresh_notes(self.build_rule(), raw=True)
            return

        rule = self.build_rule()
        valid, why = rule.is_valid()
        # Show the rule as it is being built, not only once it is complete.
        # An incomplete rule still renders; what is missing is a note on top
        # rather than something that replaces the whole pane.
        text = emit.render(rule, self.store.dialect)
        if not valid:
            text = f"{comment} incomplete — {why}\n{text}"
        self._set_buffer_text(text)
        self.save_btn.set_sensitive(valid)
        self._update_draft_row()
        self._refresh_notes(rule, raw=False, valid=valid)

    def _refresh_notes(self, rule: Rule, raw: bool, valid: bool = True):
        notes: list[str] = []
        if raw:
            notes.append("Saving the text as written. Validated before it is "
                         "written to disk.")
        # Live status first: it is what the compact strip shows, and what the
        # user is actually watching for.
        if valid and self._preview_on and not raw:
            notes.extend(self._apply_preview(rule))
        if valid and not raw:
            unpreviewable = rule.unpreviewable()
            if unpreviewable:
                notes.append(
                    "Only visible on a fresh window: " + ", ".join(unpreviewable)
                )
        text = "\n".join(notes)
        self.report.set_label(text)
        if self._compact:
            self.compact_sub.set_label(text.split("\n")[0] if text else "")

    # -- live preview -----------------------------------------------------

    def _on_preview_toggled(self, switch, _param):
        self._preview_on = switch.get_active()
        if not self._preview_on:
            self.preview.revert()
            self._set_compact(False)
        self._refresh()
        # Shrink only after the effects have landed, so the strip does not
        # cover the window the user is trying to look at.
        if self._preview_on and self.shrink_check.get_active():
            self._set_compact(True)

    def _apply_preview(self, rule: Rule) -> list[str]:
        if not self.preview.window_is_alive():
            self.preview_switch.set_active(False)
            return ["The picked window has closed — preview stopped."]
        try:
            report = self.preview.apply(rule)
        except ipc.HyprError as exc:
            return [f"Preview failed: {exc}"]
        notes = []
        if report.applied:
            notes.append("Previewing: " + ", ".join(sorted(report.applied)))
        for key, why in sorted(report.skipped.items()):
            notes.append(f"Not previewed — {key}: {why}")
        return notes

    # -- actions ----------------------------------------------------------

    def _on_cancel(self, *_a):
        """Discard while editing, close the app while browsing."""
        if self.has_selection:
            self._confirm_discard(self._leave_edit)
        else:
            self.close()

    def _force_close(self):
        self._closing = True
        self.close()

    def _on_settings(self, *_a):
        from .settings_ui import SettingsDialog
        SettingsDialog(self.store.prefs, self.store.config_dir,
                       on_saved=self._settings_changed).present(self)

    def _settings_changed(self):
        """Rebuild the store: the target file or dialect may have moved."""
        prefs = self.store.prefs
        self.store = store.RuleStore(self.store.config_dir, prefs=prefs)
        self.dialect_label.set_label(f"→ {self.store.path.name}")
        self.dialect_drop.set_selected(0 if self.store.dialect == "lua" else 1)
        self.shrink_check.set_active(prefs.shrink_while_previewing)
        self._set_source_language()
        self._rescan()
        self._refresh()

    def _on_copy(self, *_a):
        Gdk.Display.get_default().get_clipboard().set(self.buffer_text())
        self._toast("Copied")

    def _on_save(self, *_a):
        self.preview.revert()
        self._set_compact(False)
        # Editing a template writes to templates.json, never to the Hyprland
        # config -- a template is not a rule until it is used or activated.
        if self._template_edit is not None:
            self._save_template_edit()
            return
        try:
            if self._raw_dirty:
                body = self.buffer_text().strip()
                error = self.store.validate(body)
                if error:
                    self._toast("Not saved — that does not compile")
                    self.report.set_label(f"Hyprland's Lua parser said:\n{error}")
                    return
                result = (self.store.update_raw(self._editing_id, body)
                          if self._editing_id else self.store.save_raw(body))
            else:
                rule = self.build_rule()
                result = (self.store.update(self._editing_id, rule)
                          if self._editing_id else self.store.save(rule))
        except (ValueError, ipc.HyprError, OSError) as exc:
            self._toast(f"Save failed: {exc}")
            return

        if result.rolled_back:
            self._toast("Hyprland rejected the rule — config rolled back")
            self.report.set_label(
                "Rolled back. Hyprland reported:\n" + result.config_errors
            )
            return

        verb = "Updated" if self._editing_id else "Saved"
        # Stay open rather than closing on save. The draft row announces
        # itself as "Not saved", and you never see that resolve if the window
        # disappears; staying put also makes repeated tweaking bearable.
        self._draft = False           # it exists on disk now
        self._editing_id = result.rule_id
        self.save_btn.set_label("Update & Reload")
        self._rescan()
        self._set_baseline()
        self._toast(f"{verb} {result.path.name} and reloaded — Esc to close")

    def _toast(self, message: str):
        self.toast_overlay.add_toast(gtkutil.toast(message, timeout=3))

    def _on_close(self, *_a):
        # Esc and the window button both land here, so this is the last place
        # unsaved work can be lost without being asked about.
        if self._is_dirty() and not self._closing:
            self._confirm_discard(self._force_close,
                                  heading="Close without saving?")
            return True   # block the close until answered
        if self._debounce_id:
            GLib.source_remove(self._debounce_id)
            self._debounce_id = 0
        try:
            self.preview.revert()
        except ipc.HyprError:
            pass
        return False


# ---------------------------------------------------------------------------

class Application(Adw.Application):
    def __init__(self, window_info: dict, rule_store: store.RuleStore):
        # NON_UNIQUE because every launch targets a different window. With the
        # default single-instance behaviour a second press of the keybind just
        # re-presents the first editor, silently pointed at the wrong window.
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self._window_info = window_info
        self._store = rule_store

    def do_activate(self):
        win = EditorWindow(self, self._window_info, self._store)
        win.present()


def run(address: str | None = None, at_cursor: bool = False) -> int:
    # Resolve the target before any window of ours exists. This ordering is
    # load-bearing: once our window maps it becomes the active window, and
    # at_cursor in particular would then find itself.
    if address:
        window_info = picker.window_by_address(address)
        if window_info is None:
            raise ipc.HyprError(f"no window with address {address}")
    elif at_cursor:
        window_info = picker.window_at_cursor()
    else:
        window_info = picker.pick_window()

    app = Application(window_info, store.RuleStore())
    return app.run([])

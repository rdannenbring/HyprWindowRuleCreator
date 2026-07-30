"""Row and toast factories with Pango markup switched off.

Adw rows and toasts parse their title and subtitle as markup by default, and
almost every string this app displays is text it did not write: window titles,
rule names, regexes, file paths. A Chrome tab called "Tom & Jerry" is enough to
make a row render as an error, and `<` in a title would be swallowed as a tag.

Escaping at each call site is one forgotten call away from a bug, so rows are
built here instead, with markup disabled before any text is assigned -- the
order matters, since setting it afterwards leaves the bad parse already done.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402


def _plain(row, title=None, subtitle=None, **props):
    row.set_use_markup(False)
    if title is not None:
        row.set_title(str(title))
    if subtitle is not None:
        row.set_subtitle(str(subtitle))
    for key, value in props.items():
        row.set_property(key.replace("_", "-"), value)
    return row


def action_row(title=None, subtitle=None, **props) -> Adw.ActionRow:
    return _plain(Adw.ActionRow(), title, subtitle, **props)


def expander_row(title=None, subtitle=None, **props) -> Adw.ExpanderRow:
    return _plain(Adw.ExpanderRow(), title, subtitle, **props)


def property_row(title=None, subtitle=None, **props) -> Adw.ActionRow:
    row = action_row(title, subtitle, **props)
    row.add_css_class("property")
    return row


def toast(title: str, timeout: int = 3) -> Adw.Toast:
    t = Adw.Toast()
    t.set_use_markup(False)
    t.set_title(str(title))
    t.set_timeout(timeout)
    return t

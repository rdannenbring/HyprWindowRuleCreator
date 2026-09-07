"""Headless entry points -- useful on their own, and the way the core layer
gets tested without a display server in the loop.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import catalog, emit, ipc, model, picker, store
from .branding import CLI_NAME
from .model import Rule


def _fmt_window(win: dict) -> str:
    return (
        f"  address       {win['address']}\n"
        f"  class         {win.get('class')!r}\n"
        f"  initialClass  {win.get('initialClass')!r}\n"
        f"  title         {win.get('title')!r}\n"
        f"  initialTitle  {win.get('initialTitle')!r}\n"
        f"  at / size     {win.get('at')} {win.get('size')}\n"
        f"  workspace     {(win.get('workspace') or {}).get('name')!r}\n"
        f"  floating      {win.get('floating')}   xwayland {win.get('xwayland')}"
    )


def cmd_pick(args) -> int:
    win = picker.pick_window()
    if args.json:
        print(json.dumps(win, indent=2))
    else:
        print(_fmt_window(win))
        suggested = model.suggest_match(win)
        print("\n  suggested match:")
        for k, v in suggested.items():
            print(f"    {k} = {v!r}")
        if model.title_is_volatile(win):
            print("  note: title has drifted since map -- prefer initial_title")
    return 0


def cmd_list(args) -> int:
    for win in ipc.selectable_windows():
        print(_fmt_window(win), "\n")
    return 0


def cmd_catalog(args) -> int:
    print(f"props ({len(catalog.PROPS)}):")
    for f in catalog.PROPS:
        print(f"  {f.key:28} {f.kind:9} {f.doc}")
    print(f"\nstatic effects ({len(catalog.STATIC_EFFECTS)}):")
    for f in catalog.STATIC_EFFECTS:
        mark = f.preview or "-"
        print(f"  {f.key:28} {f.kind:9} [{mark:8}] {f.doc}")
    print(f"\ndynamic effects ({len(catalog.DYNAMIC_EFFECTS)}):")
    for f in catalog.DYNAMIC_EFFECTS:
        mark = f.preview or "-"
        print(f"  {f.key:28} {f.kind:9} [{mark:8}] {f.doc}")
    return 0


def cmd_rules(args) -> int:
    from . import scan
    win = (picker.window_at_cursor() if args.at_cursor
           else picker.window_by_address(args.address) if args.address
           else picker.pick_window())
    if win is None:
        print("no such window", file=sys.stderr)
        return 1
    st = store.RuleStore()
    found = scan.find_for_window(win, st.config_dir, st.path, st.dialect)
    print(f"{win.get('class')} — {len(found)} rule(s) already apply\n")
    for f in found:
        flag = f"editable id={f.managed_id}" if f.editable else "not ours"
        print(f"  [{flag}] {f.path.name}")
        print(f"    name    {f.name}")
        print(f"    match   {f.rule.get('match')}")
        print(f"    effects {f.summary()}")
        if f.unmatched_props:
            print(f"    unchecked {', '.join(f.unmatched_props)}")
        print()
    return 0


def cmd_templates(args) -> int:
    from .templates import TemplateStore
    for t in TemplateStore().all():
        kind = "shipped" + (" (edited)" if t.overridden else "") if t.builtin else "yours"
        print(f"  {t.id:24} [{kind}] {t.title}")
        print(f"    {t.description}")
        print(f"    match : {t.match_summary()}")
        print(f"    sets  : {t.summary()}")
        for url in t.sources:
            print(f"    source: {url}")
        print()
    return 0


def cmd_where(args) -> int:
    from . import reach

    st = store.RuleStore()
    status = st.reachability()

    if args.fix:
        return _fix_loader(st, status, assume_yes=args.yes)

    print(f"  config dir  {st.config_dir}")
    print(f"  dialect     {st.dialect}")
    print(f"  target file {st.path}")
    print(f"  exists      {st.path.exists()}")
    # The one line that "exists: True" does not answer. A rule can be written,
    # valid, and reloaded without complaint while nothing ever reads the file.
    print(f"  loaded      {status.summary()}")
    ids = st.existing_ids()
    print(f"  managed     {len(ids)} rule(s){':' if ids else ''}")
    for rid in ids:
        print(f"    {rid}")

    if status.loaded:
        return 0

    print()
    print(status.explanation())
    if status.entrypoint and status.entrypoint.exists():
        print(f"\nAdd this near the bottom of {status.entrypoint}:\n")
        for line in reach.loader_snippet(st.path, st.dialect).splitlines():
            print(f"    {line}")
        print(f"\nOr let {CLI_NAME} do it: {CLI_NAME} where --fix")
    else:
        print(f"\nThere is no {reach.entrypoint_for(st.config_dir, st.dialect)} "
              "to read, so nothing could be traced.")

    # Useful when the loader is unwanted: files the config demonstrably reads,
    # so pointing the target setting at one of them is the other way out.
    # Only ones inside the config dir -- the rest are the distribution's, and
    # writing rules into those would be undone by the next package update.
    others = [p for p in status.scanned
              if p != status.entrypoint and p.is_relative_to(st.config_dir)]
    if others:
        print(f"\nOr point the target at a file your config already reads "
              f"({CLI_NAME} settings):")
        for path in others[:12]:
            print(f"    {path}")
        if len(others) > 12:
            print(f"    ... and {len(others) - 12} more")
    return 1


def _fix_loader(st, status, assume_yes: bool = False) -> int:
    """Add the loader line for the user, once they have seen what it will do."""
    from . import reach

    if status.loaded:
        print(f"Already loaded — {status.detail()}. Nothing to do.")
        return 0
    if status.kind == "no-entrypoint":
        print(f"error: no {reach.entrypoint_for(st.config_dir, st.dialect)}",
              file=sys.stderr)
        return 1

    snippet = reach.loader_snippet(st.path, st.dialect)
    print(f"Appending to {status.entrypoint}:\n")
    for line in snippet.splitlines():
        print(f"    {line}")
    if not assume_yes and not _confirm(sys.stdin, "\nAdd it? [y/N] "):
        print("cancelled")
        return 130

    try:
        entry, backup = reach.install_loader(st.config_dir, st.path, st.dialect)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"added to {entry}" + (f" (backup {backup.name})" if backup else ""))
    print("Reload Hyprland (hyprctl reload) for it to take effect.")
    return 0


def _confirm(stream, prompt: str) -> bool:
    """Confirm before editing a file the user hand-maintains.

    Not a plain `input()`: a piped or absent stdin should decline rather than
    raise, so this stays safe to call from a script.
    """
    if not stream or not stream.isatty():
        print(prompt + "not a terminal — declining")
        return False
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def cmd_gui(args) -> int:
    from .ui import run
    return run(address=args.address, at_cursor=args.at_cursor)


def cmd_cursor(args) -> int:
    win = picker.window_at_cursor()
    if args.json:
        print(json.dumps(win, indent=2))
    else:
        print(_fmt_window(win))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=CLI_NAME,
        description="Build Hyprland window rules by clicking the window.",
    )
    sub = p.add_subparsers(dest="cmd")

    g = sub.add_parser("gui", help="pick a window and open the editor (default)")
    g.add_argument("--address", help="skip the picker, target this address")
    g.add_argument("--at-cursor", action="store_true",
                   help="skip the picker, target the window under the pointer")
    g.set_defaults(func=cmd_gui)

    cu = sub.add_parser("cursor", help="print the window under the pointer")
    cu.add_argument("--json", action="store_true")
    cu.set_defaults(func=cmd_cursor)

    k = sub.add_parser("pick", help="click a window and print its properties")
    k.add_argument("--json", action="store_true")
    k.set_defaults(func=cmd_pick)

    l = sub.add_parser("list", help="list selectable windows")
    l.set_defaults(func=cmd_list)

    c = sub.add_parser("catalog", help="dump the known props and effects")
    c.set_defaults(func=cmd_catalog)

    r = sub.add_parser("rules", help="list rules that already apply to a window")
    r.add_argument("--at-cursor", action="store_true")
    r.add_argument("--address")
    r.set_defaults(func=cmd_rules)

    tp = sub.add_parser("templates", help="list rule templates")
    tp.set_defaults(func=cmd_templates)

    w = sub.add_parser("where",
                       help="show where rules get written, and whether "
                            "anything reads them")
    w.add_argument("--fix", action="store_true",
                   help="add the missing conf.d loader to your hyprland.lua")
    w.add_argument("--yes", "-y", action="store_true",
                   help="with --fix, do not ask first")
    w.set_defaults(func=cmd_where)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args = parser.parse_args(["gui", *(argv or [])])
    try:
        return args.func(args)
    except picker.Cancelled:
        print("cancelled", file=sys.stderr)
        return 130
    except (ipc.HyprError, picker.PickerError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

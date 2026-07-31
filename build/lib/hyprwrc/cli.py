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
    st = store.RuleStore()
    print(f"  config dir  {st.config_dir}")
    print(f"  dialect     {st.dialect}")
    print(f"  target file {st.path}")
    print(f"  exists      {st.path.exists()}")
    ids = st.existing_ids()
    print(f"  managed     {len(ids)} rule(s){':' if ids else ''}")
    for rid in ids:
        print(f"    {rid}")
    return 0


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

    w = sub.add_parser("where", help="show where rules get written")
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

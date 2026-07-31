"""The window-rule vocabulary, transcribed from the Hyprland wiki.

Source: content/Configuring/Basics/Window-Rules.md @ hyprwm/hyprland-wiki,
matching Hyprland 0.55+ (hl.window_rule Lua syntax). Hyprland exposes no
machine-readable schema for these, so this table is hand-maintained and
version-gated -- see SCHEMA_FOR.

`kind` drives both the UI widget and the emitter's value formatting.
`preview` says how a live preview can be faked on an already-open window:
  - "prop"     : hl.dsp.window.set_prop -- the wiki's dynamic effects
  - "dispatch" : a dedicated dispatcher exists
  - None       : only observable on a freshly-mapped window
"""

from __future__ import annotations

from dataclasses import dataclass, field

SCHEMA_FOR = (0, 55)  # lowest Hyprland version this vocabulary describes


@dataclass(frozen=True)
class Field:
    key: str
    kind: str  # bool | int | number | string | regex | vec2 | enum | gradient
    doc: str
    preview: str | None = None
    choices: tuple[str, ...] = field(default_factory=tuple)
    # value used to undo a preview when set_prop rejects "unset"
    # (float-parsed props error with `stof` on unset)
    revert_to: str | None = None
    # Previewing this would take focus or the pointer away from the editor and
    # not give it back, stranding the user. Held back from live preview with
    # this text as the explanation; it still saves normally.
    traps_input: str | None = None


# --------------------------------------------------------------------------
# Props -- the `match` table. At least one is required for a valid rule.
# --------------------------------------------------------------------------

PROPS: tuple[Field, ...] = (
    Field("class", "regex", "Window class"),
    Field("title", "regex", "Window title"),
    Field("initial_class", "regex", "Class at map time (never changes)"),
    Field("initial_title", "regex", "Title at map time (never changes)"),
    # Deliberately "string", not "regex": tag matching is literal name
    # comparison, so an alternation like "(a|b)" simply never matches.
    # Verified on 0.56 -- a window tagged `xx` matches tag = "xx" but not
    # tag = "(xx|yy)". Typing it as string keeps the multi-value + off this
    # field, rather than offering an OR that would silently fail.
    Field("tag", "string", "Window carries this tag (one exact name)"),
    Field("xwayland", "bool", "Is an XWayland window"),
    Field("float", "bool", "Is floating"),
    Field("fullscreen", "bool", "Is fullscreen"),
    Field("pin", "bool", "Is pinned"),
    Field("focus", "bool", "Is the focused window"),
    Field("group", "bool", "Is in a group"),
    Field("modal", "bool", "Is a modal dialog"),
    Field("fullscreen_state_client", "int",
          "Client fullscreen state: 0 none, 1 max, 2 fs, 3 both"),
    Field("fullscreen_state_internal", "int",
          "Internal fullscreen state: 0 none, 1 max, 2 fs, 3 both"),
    Field("workspace", "string", "Workspace id, name:foo, or a selector"),
    Field("content", "enum", "Content type",
          choices=("none", "photo", "video", "game")),
    Field("xdg_tag", "regex", "xdgTag as shown by hyprctl clients"),
)

# Props whose value is a regex -- these get RE2 escaping and anchoring.
REGEX_PROPS = frozenset(f.key for f in PROPS if f.kind == "regex")


# --------------------------------------------------------------------------
# Static effects -- evaluated once, when the window opens.
# --------------------------------------------------------------------------

STATIC_EFFECTS: tuple[Field, ...] = (
    Field("float", "bool", "Float the window", preview="dispatch"),
    Field("tile", "bool", "Tile the window", preview="dispatch"),
    Field("fullscreen", "bool", "Open fullscreen", preview="dispatch"),
    Field("maximize", "bool", "Open maximized", preview="dispatch"),
    Field("fullscreen_state", "string", 'Fullscreen mode, e.g. "1 2"'),
    Field("move", "vec2", "Position, monitor-local. Accepts expressions",
          preview="dispatch"),
    Field("size", "vec2", "Size. Accepts expressions", preview="dispatch"),
    Field("center", "bool", "Center on the monitor (floating only)",
          preview="dispatch"),
    Field("pseudo", "bool", "Pseudotile", preview="dispatch"),
    Field("monitor", "string", 'Monitor to open on, e.g. "1" or "DP-1"'),
    Field("workspace", "string", 'Workspace to open on; "unset" or " silent"'),
    Field("no_initial_focus", "bool", "Do not take focus when opening"),
    Field("pin", "bool", "Show on all workspaces (floating only)",
          preview="dispatch"),
    Field("group", "string", "Group options: set/new/lock/barred/deny/..."),
    Field("suppress_event", "string",
          "Space-separated: fullscreen maximize activate activatefocus "
          "fullscreenoutput x11configurerequest"),
    Field("content", "enum", "Force content type", preview="prop",
          choices=("none", "photo", "video", "game")),
    Field("no_close_for", "int", "Uncloseable by killactive for N ms"),
    Field("scrolling_width", "number", "Column width on the scrolling layout"),
)


# --------------------------------------------------------------------------
# Dynamic effects -- re-evaluated on change, and all settable via set_prop.
# --------------------------------------------------------------------------

DYNAMIC_EFFECTS: tuple[Field, ...] = (
    Field("opacity", "string",
          'e.g. "0.8", "0.9 0.7", "1.0 0.8 0.9"; append " override" per value',
          preview="prop", revert_to="1.0"),
    Field("rounding", "int", "Corner rounding in px", preview="prop"),
    Field("rounding_power", "number", "Override rounding power",
          preview="prop", revert_to="2.0"),
    Field("border_size", "int", "Border width in px", preview="prop"),
    Field("border_color", "gradient",
          'Color or gradient; two values = active/inactive', preview="prop"),
    Field("animation", "string", 'Animation style, e.g. "popin" or "popin 80%"',
          preview="prop"),
    Field("max_size", "vec2", "Max size for floating windows", preview="prop"),
    Field("min_size", "vec2", "Min size for floating windows", preview="prop"),
    Field("idle_inhibit", "enum", "Idle inhibit mode", preview="prop",
          choices=("none", "always", "focus", "fullscreen")),
    Field("tag", "string", 'Apply a dynamic tag, e.g. "+myTag"', preview="prop"),
    Field("tonemap", "enum", "Tonemapping behaviour", preview="prop",
          choices=("on", "off", "clamp", "limited")),
    Field("scroll_mouse", "number", "Override input.scroll_factor",
          preview="prop", revert_to="1.0"),
    Field("scroll_touchpad", "number",
          "Override input.touchpad.scroll_factor", preview="prop",
          revert_to="1.0"),
    Field("persistent_size", "bool", "Remember floating size per class+title",
          preview="prop"),
    Field("no_max_size", "bool", "Ignore max size limits", preview="prop"),
    Field("stay_focused", "bool", "Force focus while visible", preview="prop",
          traps_input="would hold focus on the target — you could not get back "
                      "to this editor"),
    Field("allows_input", "bool", "Force XWayland window to accept input",
          preview="prop"),
    Field("dim_around", "bool", "Dim everything around the window",
          preview="prop"),
    Field("decorate", "bool", "Draw window decorations", preview="prop"),
    Field("focus_on_activate", "bool", "Honour focus requests", preview="prop"),
    Field("keep_aspect_ratio", "bool", "Lock aspect on mouse resize",
          preview="prop"),
    Field("nearest_neighbor", "bool", "Nearest-neighbour filtering",
          preview="prop"),
    Field("no_anim", "bool", "Disable animations", preview="prop"),
    Field("no_blur", "bool", "Disable blur", preview="prop"),
    Field("no_dim", "bool", "Disable dimming", preview="prop"),
    Field("no_focus", "bool", "Never focus this window", preview="prop"),
    Field("no_follow_mouse", "bool", "Ignore follow_mouse focus",
          preview="prop"),
    Field("no_shadow", "bool", "Disable shadow", preview="prop"),
    Field("no_shortcuts_inhibit", "bool", "Disallow shortcut inhibiting",
          preview="prop"),
    Field("no_screen_share", "bool", "Hide from screen sharing",
          preview="prop"),
    Field("no_vrr", "bool", "Disable VRR (needs misc.vrr 2 or 3)",
          preview="prop"),
    Field("no_auto_hdr", "bool", "Disable AutoHDR", preview="prop"),
    Field("opaque", "bool", "Force opaque", preview="prop"),
    Field("force_rgbx", "bool", "Ignore the alpha channel", preview="prop"),
    Field("sync_fullscreen", "bool", "Match client fullscreen mode",
          preview="prop"),
    Field("immediate", "bool", "Allow tearing", preview="prop"),
    Field("xray", "bool", "Blur xray mode", preview="prop"),
    Field("render_unfocused", "bool", "Keep rendering while hidden",
          preview="prop"),
    Field("confine_pointer", "bool", "Lock the cursor to the window",
          preview="prop",
          traps_input="would lock the pointer inside the target window"),
    Field("no_xdg_drags", "bool", "Disable XDG-driven drags", preview="prop"),
)

EFFECTS: tuple[Field, ...] = STATIC_EFFECTS + DYNAMIC_EFFECTS

PROP_BY_KEY = {f.key: f for f in PROPS}
EFFECT_BY_KEY = {f.key: f for f in EFFECTS}

# set_prop expands a few rule names into several underlying props.
# Documented under "set_prop" in Configuring/Basics/Dispatchers.md.
PROP_EXPANSIONS = {
    "border_color": ("active_border_color", "inactive_border_color"),
    "opacity": (
        "opacity", "opacity_inactive", "opacity_fullscreen",
        "opacity_override", "opacity_inactive_override",
        "opacity_fullscreen_override",
    ),
}

# Effects worth surfacing first -- the long tail stays behind "show all".
COMMON_EFFECTS = (
    "float", "size", "move", "center", "pin", "workspace", "monitor",
    "opacity", "no_initial_focus", "no_focus", "no_anim", "no_blur",
    "rounding", "border_size", "border_color", "idle_inhibit", "tile",
    "no_shadow", "dim_around", "stay_focused", "suppress_event",
)

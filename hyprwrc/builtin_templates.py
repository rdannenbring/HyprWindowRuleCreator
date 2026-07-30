"""Shipped rule templates.

Drawn from the Hyprland wiki, distro-curated defaults (CachyOS), and patterns
that recur across widely-used dotfiles. Each carries its provenance so you can
judge it rather than take it on faith.

Class names are the awkward part: the same dialog is called different things
depending on which toolkit and portal implementation is installed, which is why
several of these match an alternation rather than one name. Check yours with
`hyprwrc cursor` before assuming a template fits.

Matches are RE2 (Hyprland's engine). `initial_*` is preferred over the live
value wherever the rule uses a static effect, because static effects are
evaluated against the values a window had when it mapped.
"""

WIKI = "https://wiki.hypr.land/Configuring/Basics/Window-Rules/"
CACHY = ("https://github.com/CachyOS/cachyos-hyprland-settings/blob/master/"
         "etc/skel/.config/hypr/config/windowrules.conf")

BUILTIN = [
    {
        "id": "auth-polkit",
        "title": "Authentication prompts (polkit)",
        "description":
            "The 'this action requires privileges' password box. Floats and "
            "centres it, holds focus so it cannot be lost behind other "
            "windows, dims the background, and keeps it out of screen shares.",
        "match": {
            "initial_class":
                r"^(polkit-gnome-authentication-agent-1|"
                r"org\.kde\.polkit-kde-authentication-agent-1|hyprpolkitagent|"
                r"lxqt-policykit-agent|xfce-polkit|"
                r"polkit-mate-authentication-agent-1)$",
        },
        "effects": {
            "float": True, "center": True, "stay_focused": True,
            "dim_around": True, "no_screen_share": True,
        },
        "sources": [CACHY, "https://github.com/hyprwm/Hyprland/discussions/1785"],
    },
    {
        "id": "auth-pinentry",
        "title": "Password & GPG prompts (pinentry, gcr)",
        "description":
            "GPG and keyring passphrase prompts. The wiki calls out "
            "stay_focused here specifically to fix pinentry losing focus; "
            "no_screen_share keeps the passphrase off a shared screen.",
        "match": {
            "initial_class": r"^(pinentry-.*|gcr-prompter|org\.gnupg\.pinentry.*)$",
        },
        "effects": {
            "float": True, "center": True, "stay_focused": True,
            "no_screen_share": True,
        },
        "sources": [WIKI],
    },
    {
        "id": "portal-file-picker",
        "title": "File picker portals",
        "description":
            "The Open/Save dialog apps get from xdg-desktop-portal. Tiling "
            "these is almost never what you want.",
        "match": {
            "initial_class":
                r"^(xdg-desktop-portal-gtk|xdg-desktop-portal-kde|"
                r"xdg-desktop-portal-hyprland)$",
        },
        "effects": {"float": True, "center": True, "size": ("900", "600")},
        "sources": [CACHY],
    },
    {
        "id": "dialog-titles",
        "title": "Common dialog titles",
        "description":
            "Catches Open/Save/Choose dialogs by title for apps that draw "
            "their own rather than using a portal. Matches on initial_title, "
            "since these windows rename themselves as you browse.",
        "match": {
            "initial_title":
                r"^(Open|Open File|Open Files|Open Folder|Save|Save As|"
                r"Save File|Choose Files|Select a File|Select Folder|"
                r"File Operation Progress|Confirm to replace files)$",
        },
        "effects": {"float": True, "center": True},
        "sources": [CACHY,
                    "https://deepwiki.com/prasanthrangan/hyprdots/3.3-window-rules"],
    },
    {
        "id": "modal-dialogs",
        "title": "Modal dialogs (any app)",
        "description":
            "Matches on the modal flag rather than a name, so it covers "
            "'are you sure?' boxes from apps you have not thought about yet.",
        "match": {"modal": True},
        "effects": {"float": True, "center": True, "dim_around": True},
        "sources": [WIKI],
    },
    {
        "id": "picture-in-picture",
        "title": "Picture-in-Picture",
        "description":
            "Browser PiP windows: float, pin so they survive workspace "
            "switches, and give them a sane size.",
        "match": {"title": r"^(Picture-in-Picture|Picture in picture)$"},
        "effects": {"float": True, "pin": True, "size": ("960", "540")},
        "sources": [CACHY],
    },
    {
        "id": "settings-utilities",
        "title": "Settings & utility apps",
        "description":
            "Small single-purpose config apps — audio, bluetooth, network, "
            "archives, disks — that are more usable floating.",
        "match": {
            "initial_class":
                r"^(pavucontrol|org\.pulseaudio\.pavucontrol|"
                r"com\.saivert\.pwvucontrol|blueman-manager|"
                r"nm-connection-editor|file-roller|org\.gnome\.FileRoller|"
                r"gnome-disks|baobab|org\.gnome\.baobab|qt5ct|qt6ct|nwg-look|"
                r"qalculate-gtk|zenity|yad)$",
        },
        "effects": {"float": True, "center": True, "size": ("900", "600")},
        "sources": [CACHY,
                    "https://github.com/hyprwm/Hyprland/discussions/13141"],
    },
    {
        "id": "password-managers",
        "title": "Password managers — hide from screen share",
        "description":
            "Blanks these windows in screen captures, so a vault does not end "
            "up in a recording or call. Applies no other layout change.",
        "match": {
            "initial_class":
                r"^(1Password|Bitwarden|org\.keepassxc\.KeePassXC|KeePassXC)$",
        },
        "effects": {"no_screen_share": True},
        "sources": [WIKI],
    },
    {
        "id": "xwayland-video-bridge",
        "title": "XWayland video bridge (screen sharing fix)",
        "description":
            "Makes the helper window that lets X11 apps see your screen "
            "effectively invisible, instead of a stray box in the corner. "
            "Only useful if you actually run xwaylandvideobridge.",
        "match": {"initial_class": r"^xwaylandvideobridge$"},
        "effects": {
            "opacity": "0.0", "no_anim": True, "no_initial_focus": True,
            "no_focus": True, "no_blur": True,
        },
        "sources": ["https://wiki.hypr.land/Useful-Utilities/Screen-Sharing/"],
    },
    {
        "id": "steam-dialogs",
        "title": "Steam sub-windows",
        "description":
            "Steam's friends list and settings open as ordinary windows and "
            "wreck a tiled layout.",
        "match": {"initial_title": r"^(Friends List|Steam Settings)$"},
        "effects": {"float": True, "center": True},
        "sources": ["https://github.com/GamaunG/dotfiles"],
    },
    {
        "id": "float-and-centre",
        "title": "Just float and centre",
        "description":
            "The plain starting point: no match criteria, so fill in your own. "
            "Useful as a base when none of the others fit.",
        "match": {},
        "effects": {"float": True, "center": True},
        "sources": [],
    },
]

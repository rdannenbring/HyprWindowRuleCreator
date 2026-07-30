"""Names, split by whether changing them is safe.

Renaming before release is expected, so the display name lives in one place.
The other two only look like the same string -- they are written into files on
disk, and changing them silently orphans data that is already out there.
"""

# --------------------------------------------------------------------------
# Safe to change. Display only: window titles, dialogs, button text, docs.
# --------------------------------------------------------------------------

APP_NAME = "HyprWindowRuleCreator"

#: Command name, shown in --help and used in documentation examples.
CLI_NAME = "hyprwrc"


# --------------------------------------------------------------------------
# NOT safe to change without a migration. Both are already on users' disks.
# --------------------------------------------------------------------------

#: Marker written around every generated rule, as `>>> {FENCE_TAG} {id}`.
#: Rules already in someone's config carry the old tag; change this and the
#: app stops recognising its own rules -- they become "defined outside", lose
#: their edit/delete actions, and a second copy gets appended instead of the
#: existing one being updated. A rename needs code that also reads the old tag.
FENCE_TAG = "hyprwrc"

#: Directory under XDG_CONFIG_HOME holding settings.json and templates.json.
#: Changing it hides existing settings and user templates rather than moving
#: them.
CONFIG_DIR_NAME = "hyprwrc"

#: GTK application id. Changing it is harmless for data, but it is also what
#: any window rule targeting the editor itself matches on, so a rename means
#: updating that rule.
APP_ID = "dev.hyprwrc.HyprWindowRuleCreator"


def outside_app() -> str:
    """How rules this tool did not write are described in the UI."""
    return f"defined outside {APP_NAME}"

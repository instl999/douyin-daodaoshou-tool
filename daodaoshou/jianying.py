"""Finding the drafts folder Jianying actually reads on this machine."""

from __future__ import annotations

import json
import os
from pathlib import Path

# -------------------------------------------------------- jianying paths ----

# Where an installed editor keeps its user data, and the folder holding drafts
# inside it. The mainland build is checked before the international one: a
# machine carrying both is a 剪映 user who also has CapCut, not the reverse.
JIANYING_APP_DIRS = ("JianyingPro", "CapCut")
JIANYING_DRAFT_LEAF = "com.lveditor.draft"
JIANYING_SETTINGS = ("User Data", "Config", "globalSetting")
JIANYING_DEFAULT_DRAFTS = ("User Data", "Projects", JIANYING_DRAFT_LEAF)


def jianying_app_roots() -> list[Path]:
    """Every directory an installed 剪映 / CapCut keeps its user data in."""
    bases: list[Path] = []
    for variable in ("LOCALAPPDATA", "APPDATA"):
        value = os.getenv(variable, "").strip()
        if value:
            bases.append(Path(value))
    home = Path.home()
    # Windows is the supported platform. The macOS location costs one stat
    # call and turns "nothing was found" into "it just worked" for anyone
    # running this there anyway.
    bases += [home / "AppData" / "Local", home / "Movies"]
    roots: list[Path] = []
    for base in bases:
        for app in JIANYING_APP_DIRS:
            candidate = base / app
            if candidate.is_dir() and candidate not in roots:
                roots.append(candidate)
    return roots


def relocated_draft_dir(app_root: Path) -> Path | None:
    """The drafts folder the user moved to, as Jianying itself recorded it.

    Jianying can keep its draft library on another disk, and a machine that
    has moved it still has the default folder sitting there empty - so a probe
    that knows only the default finds a directory, calls it a hit, and writes
    every draft somewhere the editor no longer reads. Nothing would look
    wrong: the run succeeds and the draft list stays empty.

    The chosen path is in Jianying's own settings file. The key holding it has
    been renamed between versions, so rather than bet on one name, any string
    value naming a directory that exists is accepted, keys mentioning drafts
    first.
    """
    settings = app_root.joinpath(*JIANYING_SETTINGS)
    try:
        data = json.loads(settings.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    named = [(key, value) for key, value in data.items()
             if isinstance(value, str) and value.strip()]
    named.sort(key=lambda pair: "draft" not in pair[0].lower())
    for _key, value in named:
        candidate = Path(value.strip()).expanduser()
        # Settings hold the library root; drafts live in the bundle-id folder
        # under it, and some versions record that folder directly.
        if candidate.name != JIANYING_DRAFT_LEAF:
            candidate = candidate / JIANYING_DRAFT_LEAF
        if candidate.is_dir():
            return candidate
    return None


def detect_draft_dir() -> Path | None:
    """Jianying's drafts folder on this machine, or None if there is none."""
    for root in jianying_app_roots():
        moved = relocated_draft_dir(root)
        if moved is not None:
            return moved
        default = root.joinpath(*JIANYING_DEFAULT_DRAFTS)
        if default.is_dir():
            return default
    return None


def resolve_draft_dir() -> tuple[Path, str]:
    """Where finished drafts are written, and how that was decided.

    JIAN_YING_DRAFT_DIR used to be required, and it was the setting every new
    user got wrong first: the path is four directories deep and ends in a
    bundle id. The editor installs itself in one of two known places, so the
    usual answer can simply be looked up, and the drafts land where Jianying
    already reads them instead of in a folder to be copied by hand.

    An explicit setting still wins, and is still checked. A typo there is a
    mistake to report rather than a reason to quietly use somewhere else: the
    whole point of setting it is that this machine is the unusual one.
    """
    configured = os.getenv("JIAN_YING_DRAFT_DIR", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_dir():
            raise RuntimeError(f"JIAN_YING_DRAFT_DIR does not exist: {path}")
        return path, "JIAN_YING_DRAFT_DIR"
    found = detect_draft_dir()
    if found is not None:
        return found, "detected"
    raise RuntimeError(
        "Jianying's drafts folder was not found on this machine. In 剪映专业版, "
        "全局设置 -> 草稿位置 shows where it keeps drafts; put that path in "
        "JIAN_YING_DRAFT_DIR in .env."
    )



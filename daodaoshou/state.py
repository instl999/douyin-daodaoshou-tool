"""The run's manifest and log - what --resume reads back."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .config import BASELINE_VIDEO_SPEED
from .models import SCENE_FIELDS, Character, Scene

# 6 added `speed`: a manifest without one was written before the global speed
# existed, so its narration was read at the baseline.
# 7 added `moods`: the director's reading of the copy, which the music is
# chosen from. A manifest without one falls back to reading the copy itself,
# so an older run still gets a bed rather than an error.
# 8 added each scene's `subject`: the one thing its frame is about. A scene
# without one still composes - the subject is named generically - but the
# picture is only as focused as the description it was given, so a resume
# from v7 keeps the flatter images it already paid for.
# 9 names every clip and frame after the inputs that made it (see "assets"),
# records the voice and the style beside the speed, and gives each scene a
# `take` for --redo. Files from an older run are trusted once when it is
# resumed, and renamed to the key they would have had.
MANIFEST_VERSION = 9

# ----------------------------------------------------------------- state ----

def save_run_state(asset_root: Path, draft_name: str, title: str, copy: str, scenes: list[Scene],
                   characters: list[Character], status: str, failures: list[dict[str, Any]],
                   speed: float = BASELINE_VIDEO_SPEED,
                   moods: list[str] | None = None,
                   render: dict[str, str] | None = None) -> None:
    state = {
        "version": MANIFEST_VERSION,
        "draft_name": draft_name,
        "title": title,
        "copy": copy,
        "status": status,
        # What the narration on disk was actually read at. Without it a resume
        # at another speed would keep the old clips, cut the new timeline to
        # them, and produce a video that is neither speed while reporting a
        # clean run - which is exactly the silent kind of wrong this tool is
        # full of guards against. The file names now carry the rate as well;
        # this stays so a resume can say that the speed is why.
        "speed": speed,
        # The voice and the look, for the same reason: a resume that re-reads
        # or redraws everything should be able to say which setting moved.
        **(render or {}),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        # How the director read the copy, which is what the music is chosen
        # from. Stored because a --resume never calls the director again, and
        # without it a resumed run would fall back to reading the copy itself
        # and could quietly land on a different track than the first run did.
        "moods": list(moods or []),
        "characters": [asdict(character) for character in characters],
        "scenes": [asdict(scene) for scene in scenes],
        "failures": failures,
    }
    (asset_root / "manifest.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    (asset_root / "failures.json").write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")


def append_run_log(asset_root: Path, event: str, **details: Any) -> None:
    record = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event, **details}
    with (asset_root / "run.log").open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(record, ensure_ascii=False) + "\n")


def scenes_from_manifest(state: dict[str, Any]) -> list[Scene]:
    scenes: list[Scene] = []
    for item in state.get("scenes", []):
        values = {key: item.get(key) for key in SCENE_FIELDS}
        values["cast"] = values.get("cast") or []
        # Absent before manifest v9: every frame was a first take.
        values["take"] = int(values.get("take") or 0)
        scenes.append(Scene(**values))
    return scenes



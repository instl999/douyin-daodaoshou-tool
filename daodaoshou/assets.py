"""Files on disk, each named after the inputs that made it."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .models import Character, Scene

if TYPE_CHECKING:
    from .config import Config



def frames_to_draw(cfg: Config, scenes: list[Scene], characters: list[Character],
                   image_dir: Path, legacy: bool) -> int:
    """How many frames a run of this storyboard would draw, found without touching a file."""
    def drawn(index: int, scene: Scene) -> bool:
        if _usable(keyed_path(image_dir, index, picture_key(cfg, scene, characters), ".png")):
            return True
        recorded = Path(scene.image_path) if scene.image_path else None
        return legacy and _usable(recorded) and bool(_LEGACY_ASSET_NAME.fullmatch(recorded.stem))
    return sum(not drawn(index, scene) for index, scene in enumerate(scenes, 1))


# ---------------------------------------------------------------- assets ----
#
# Every clip and frame on disk is named after what made it: the scene number,
# for people, then a short digest of every input that decides its content.
#
#     audio/07_3fa9c2e1d0.mp3      text, voice, speech rate, loudness, model
#     images/07_b41f09aa2c.png     prompt, subject, framing, cast, style,
#                                  model, size, seed, take
#
# The name is the proof. Files used to be called 07.mp3 and 07.png and were
# adopted back by that name alone, which let a run pick up a file made for
# other inputs and still report success:
#
#   - a fresh run under an existing --draft-name took the previous run's clip
#     for scene 7, whatever the new scene 7 said. Re-running after a failure
#     re-splits the copy, so every subtitle sat over narration reading a
#     different sentence, and no API call was made to say otherwise;
#   - a resume after changing IMAGE_STYLE_PRESET kept every old frame and laid
#     the new style's grade and title over them, and one after changing the
#     voice read the missing lines in the new voice beside the old ones.
#
# Now a file is used only if it was made from exactly the inputs its scene has
# now. Reuse is safe by construction, anything stale is simply not found, and
# a crashed run's finished files are still picked up.
#
# What the user controls goes into a key; this code's own wording does not.
# The composition brief gets rewritten, and a resume after an upgrade keeps
# the frames it already paid for rather than quietly redrawing every one -
# the same call manifest v8 made.
ASSET_KEY_LENGTH = 10


def fingerprint(*parts: Any) -> str:
    """A short digest of `parts`, stable across runs and machines."""
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:ASSET_KEY_LENGTH]


def narration_key(cfg: Config, text: str) -> str:
    """Everything that decides what a spoken clip sounds like.

    The speech rate is the combined one - VIDEO_SPEED with the voice's own
    trim - so a clip read at another speed is a different clip, which is what
    the manifest's `speed` used to be kept for.
    """
    return fingerprint("narration", text.strip(), cfg.ark_tts_model, cfg.ark_tts_voice_type,
                       cfg.ark_tts_speech_rate, cfg.ark_tts_loudness_rate)


def image_seed(cfg: Config, scene: Scene) -> int | None:
    """The seed a scene's frame is drawn with, or None for a random one.

    A redrawn frame has to come back different, and with ARK_IMAGE_SEED set
    the same prompt and seed return the same picture - so each take moves the
    seed on by one. Take 0 uses the configured seed exactly.
    """
    if cfg.ark_image_seed is None:
        return None
    return cfg.ark_image_seed + scene.take


def picture_key(cfg: Config, scene: Scene, characters: list[Character]) -> str:
    """Everything the user controls that decides what a frame shows.

    Not the subtitle: the frame is drawn from image_prompt and subject, so
    correcting a typo in a line re-reads that line and keeps its picture.
    """
    lookup = {character.id: character.desc for character in characters}
    cast = [lookup[cid] for cid in scene.cast if cid in lookup]
    return fingerprint("picture", scene.image_prompt.strip(), (scene.subject or "").strip(),
                       scene.shot_size, cast, cfg.image_style_prompt.strip(), cfg.style.medium,
                       cfg.ark_image_model, cfg.ark_image_size, cfg.ark_image_output_format,
                       image_seed(cfg, scene), scene.take)


def style_key(cfg: Config) -> str:
    """The look as the image model receives it, to tell an edited preset apart."""
    return fingerprint(cfg.image_style_prompt.strip(), cfg.style.medium)


def keyed_path(directory: Path, index: int, key: str, suffix: str) -> Path:
    return directory / f"{index:02d}_{key}{suffix}"


def write_atomically(target: Path, data: bytes) -> None:
    """Write a file whole or not at all.

    A file's name is taken as proof of what it holds, so a crash halfway
    through a write must not leave half a clip under a good name for the next
    run to adopt.
    """
    partial = target.with_name(target.name + ".part")
    partial.write_bytes(data)
    partial.replace(target)


def _usable(path: Path | None) -> bool:
    return path is not None and path.is_file() and path.stat().st_size > 0


# A pre-v9 file: the bare scene number, 07.mp3.
_LEGACY_ASSET_NAME = re.compile(r"\d{2,}")


@dataclass
class Reconciled:
    """What a run found on disk before paying for anything."""

    narration_reused: int = 0
    # Had a file, but one made for other inputs: re-read / redrawn.
    narration_stale: int = 0
    pictures_reused: int = 0
    pictures_stale: int = 0
    # Files from a pre-v9 run, renamed to the key they would have had.
    migrated: int = 0


def _reconcile_one(scene: Scene, attribute: str, expected: Path, legacy: Path | None) -> str:
    """Point one of a scene's files at `expected` if that file exists.

    Returns 'reused', 'migrated', 'stale' (the scene had a file, made from
    other inputs) or 'missing'.
    """
    recorded = getattr(scene, attribute)
    if _usable(expected):
        setattr(scene, attribute, str(expected.resolve()))
        return "reused"
    if legacy is not None:
        # Written before files carried their inputs in their names. Those
        # runs trusted their files and so does resuming one: each is renamed,
        # once, to the key it would have had, and is an ordinary file after.
        for candidate in (Path(recorded) if recorded else None, legacy):
            if _usable(candidate) and _LEGACY_ASSET_NAME.fullmatch(candidate.stem):
                candidate.replace(expected)
                setattr(scene, attribute, str(expected.resolve()))
                return "migrated"
    setattr(scene, attribute, None)
    return "stale" if recorded else "missing"


def reconcile_assets(cfg: Config, scenes: list[Scene], characters: list[Character],
                     audio_dir: Path, image_dir: Path, *,
                     legacy_audio: bool = False, legacy_images: bool = False) -> Reconciled:
    """Point every scene at the files made from its current inputs, and only those.

    `legacy_audio` / `legacy_images` trust a pre-v9 run's files - only ever
    when that run is being resumed, and for the narration only when it was
    read at this speed.
    """
    found = Reconciled()
    for index, scene in enumerate(scenes, 1):
        audio = _reconcile_one(
            scene, "audio_path",
            keyed_path(audio_dir, index, narration_key(cfg, scene.text), ".mp3"),
            audio_dir / f"{index:02d}.mp3" if legacy_audio else None)
        picture = _reconcile_one(
            scene, "image_path",
            keyed_path(image_dir, index, picture_key(cfg, scene, characters), ".png"),
            image_dir / f"{index:02d}.png" if legacy_images else None)
        found.narration_reused += audio in {"reused", "migrated"}
        found.narration_stale += audio == "stale"
        found.pictures_reused += picture in {"reused", "migrated"}
        found.pictures_stale += picture == "stale"
        found.migrated += (audio == "migrated") + (picture == "migrated")
    return found


def render_settings(cfg: Config) -> dict[str, str]:
    """The run-wide settings a manifest records, so a resume can say what changed."""
    return {"voice": cfg.ark_tts_voice_type, "style": cfg.style_preset, "style_key": style_key(cfg)}


def explain_reconciled(previous: dict[str, Any] | None, cfg: Config, found: Reconciled) -> list[str]:
    """One line for each reason something already made is being made again.

    The reason is named when the manifest can name it - a different voice, a
    different style - because "12 frames will be redrawn" with no reason
    attached reads as a bug, and it is a bill.
    """
    lines: list[str] = []
    previous = previous or {}
    was_voice, was_style = previous.get("voice"), previous.get("style")
    if found.narration_stale:
        reason = (f"the voice changed ({was_voice} -> {cfg.ark_tts_voice_type})"
                  if was_voice and was_voice != cfg.ark_tts_voice_type
                  else "their text, voice or speed changed")
        lines.append(f"{found.narration_stale} narration clip(s) will be read again: {reason}.")
    if found.pictures_stale:
        if was_style and was_style != cfg.style_preset:
            reason = f"the style changed ({was_style} -> {cfg.style_preset})"
        elif previous.get("style_key") and previous["style_key"] != style_key(cfg):
            reason = f"the {cfg.style_preset} style's prompt changed"
        else:
            reason = "their prompt, framing, cast, seed or take changed"
        lines.append(f"{found.pictures_stale} frame(s) will be drawn again: {reason}.")
    if found.migrated:
        lines.append(f"{found.migrated} file(s) from an older run were kept and renamed after their inputs.")
    return lines



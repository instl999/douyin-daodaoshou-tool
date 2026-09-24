"""The command itself: its arguments, and one run from copy to draft."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import env, images, storyboard, tts
from .assets import (
    explain_reconciled,
    frames_to_draw,
    image_seed,
    keyed_path,
    narration_key,
    picture_key,
    reconcile_assets,
    render_settings,
)
from .bgm import BGM_MOODS, resolve_bgm
from .config import BASELINE_VIDEO_SPEED, DEFAULT_VIDEO_SPEED, Config, describe_configuration, drop_stale_narration, validate_speed
from .draft import build_draft, validate_draft_target, validate_plan_target
from .env import load_env
from .images import anchor_scene, compose_image_prompt, reference_image
from .models import Character, Scene
from .report import describe_image_budget, print_image_cost_estimate, report_progress, run_summary
from .state import MANIFEST_VERSION, append_run_log, save_run_state, scenes_from_manifest
from .storyboard import MAX_COPY_CHARACTERS, scene_limits
from .title import title_already_narrated, title_language_differs
from .tts import populate_audio_durations


def _use_utf8_stdout() -> None:
    """Make stdout survive a console that is not UTF-8.

    Windows encodes stdout with the *console* code page, not UTF-8: cp1252
    under Git Bash, cp1252 again under many CI shells. Almost everything this
    program prints is Chinese - style labels, scene text, the progress lines -
    so on such a console the `print` itself raises UnicodeEncodeError.
    `--check-config` died on its second line, and a real run dies partway
    through, after the narration and the images have been paid for. The
    traceback names charmap.py, so it reads as a Python bug rather than a
    terminal setting.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:                  # a pipe, or a captured buffer
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass                                 # printing is not worth aborting over


_use_utf8_stdout()

# ------------------------------------------------------------------ main ----

def parse_scene_numbers(text: str, count: int) -> list[int]:
    """'3,7' or '3-5,9' as scene numbers, each checked against the storyboard."""
    numbers: set[int] = set()
    for part in text.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", part)
        if not match:
            raise RuntimeError(f"--redo takes scene numbers such as 3,7 or 3-5, not {part!r}.")
        first, last = sorted((int(match[1]), int(match[2] or match[1])))
        if first < 1 or last > count:
            raise RuntimeError(f"--redo {part}: this storyboard has scenes 1 to {count}.")
        numbers.update(range(first, last + 1))
    if not numbers:
        raise RuntimeError("--redo needs at least one scene number.")
    return sorted(numbers)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a Jianying draft from copy.")
    source = parser.add_mutually_exclusive_group(required=False)
    source.add_argument("--text")
    source.add_argument("--input", type=Path)
    parser.add_argument("--title", help="Title overlay; defaults to the first line of the copy.")
    parser.add_argument("--draft-name")
    parser.add_argument("--resume", metavar="DRAFT_NAME", help="Resume a failed run stored under output/DRAFT_NAME.")
    parser.add_argument("--replace", action="store_true",
                        help="Overwrite an existing Jianying draft. This deletes the whole draft folder.")
    parser.add_argument("--redo", metavar="SCENES",
                        help=("With --resume: draw these scenes' pictures again, e.g. 3,7 or 3-5. Each "
                              "redraw is a new take, with a new seed when ARK_IMAGE_SEED is set; the "
                              "earlier take stays on disk."))
    parser.add_argument("--check-config", action="store_true",
                        help="Validate local configuration and assets without API calls.")
    parser.add_argument("--plan-only", dest="plan_only", action="store_true",
                        help="Generate and print a storyboard only; this still calls the storyboard API.")
    parser.add_argument("--speed", type=float, default=None, metavar="X",
                        help=("How fast the whole video runs: narration, "
                              "picture, camera moves, pauses and subtitles "
                              "together. 1.0 is natural pace. Overrides "
                              f"VIDEO_SPEED (default {DEFAULT_VIDEO_SPEED})."))
    parser.add_argument("--verbose", action="store_true", help="Print a full traceback on failure.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.redo and (not args.resume or args.plan_only):
        parser.error("--redo redraws scenes of an existing run: use it with --resume, without --plan-only")
    load_env()
    cfg = Config.load(speed=args.speed,
                      purpose="check" if args.check_config else "plan" if args.plan_only else "build")
    if args.check_config:
        print("Configuration OK")
        describe_configuration(cfg)
        return 0

    failures: list[dict[str, Any]] = []
    characters: list[Character] = []
    # What the narration already on disk was read at. A fresh run has none, so
    # it is whatever this run is about to use.
    previous_speed = cfg.speed
    # The manifest being resumed, if any: what a resume compares itself with
    # when it says why something already made is being made again.
    previous: dict[str, Any] | None = None
    render = render_settings(cfg)

    def save(status: str) -> None:
        save_run_state(asset_root, draft_name, title, copy, scenes, characters,
                       status, failures, cfg.speed, moods, render)

    if args.resume:
        draft_name = args.resume
        asset_root = env.ROOT / "output" / draft_name
        manifest_path = asset_root / "manifest.json"
        if not manifest_path.is_file():
            raise RuntimeError(f"Resume manifest does not exist: {manifest_path}")
        state = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        previous = state
        if int(state.get("version", 0)) < MANIFEST_VERSION:
            print(
                f"Manifest was written by an older version (v{state.get('version')}); "
                "resuming, but fields it no longer uses are ignored.",
                flush=True,
            )
        copy = state.get("copy", "")
        if not copy:
            raise RuntimeError("Resume manifest has no source copy; start a new run instead.")
        title = args.title or state.get("title") or next(
            (line.strip() for line in copy.splitlines() if line.strip()), copy.strip()
        )
        scenes = scenes_from_manifest(state)
        if not scenes:
            raise RuntimeError("Resume manifest has no scenes.")
        characters = [
            Character(str(item["id"]), str(item["desc"]))
            for item in state.get("characters", [])
            if isinstance(item, dict) and item.get("id") and item.get("desc")
        ]
        failures = state.get("failures", [])
        # Absent before manifest v7; copy_moods() reads the copy instead, so
        # an older run still gets music rather than an error.
        moods = [str(mood) for mood in state.get("moods", []) if str(mood) in BGM_MOODS]
        # Absent before manifest v6, which means the clips were made before
        # this setting existed and were read at the baseline.
        previous_speed = validate_speed(state.get("speed", BASELINE_VIDEO_SPEED))
        if args.redo:
            # A new take is a new key, so the frame is drawn again rather than
            # found again, and the earlier take stays on disk beside it.
            redo = parse_scene_numbers(args.redo, len(scenes))
            for number in redo:
                scenes[number - 1].take += 1
            print(f"Drawing scene(s) {', '.join(map(str, redo))} again.")
            append_run_log(asset_root, "redo_requested", scenes=redo)
        # A resume used to overwrite the draft unconditionally, which deletes
        # any edits already made in Jianying. It now needs --replace like any
        # other run. Printing the plan writes no draft, so it needs neither.
        if not args.plan_only:
            validate_draft_target(cfg, draft_name, args.replace)
        append_run_log(asset_root, "resume_started", scene_count=len(scenes))
    else:
        if not args.text and not args.input:
            parser.error("one of --text or --input is required unless --resume is used")
        copy = args.text or args.input.read_text(encoding="utf-8")
        copy_character_count = len(re.sub(r"\s+", "", copy))
        if copy_character_count < 10:
            raise RuntimeError("Copy is too short.")
        if copy_character_count > MAX_COPY_CHARACTERS:
            raise RuntimeError(f"Copy is {copy_character_count} characters; the current maximum is {MAX_COPY_CHARACTERS}.")
        title = args.title or next((line.strip() for line in copy.splitlines() if line.strip()), copy.strip())
        draft_name = args.draft_name or time.strftime("auto_video_%Y%m%d_%H%M%S")
        asset_root = env.ROOT / "output" / draft_name
        if args.plan_only:
            validate_plan_target(asset_root, draft_name, args.replace)
        else:
            validate_draft_target(cfg, draft_name, args.replace)
        asset_root.mkdir(parents=True, exist_ok=True)
        target_scenes, maximum_scenes = scene_limits(cfg.scene_length_mode, cfg.scene_characters, copy)
        print_image_cost_estimate(cfg, target_scenes, maximum_scenes)
        append_run_log(asset_root, "storyboard_started")
        scenes, characters, moods = storyboard.plan_scenes(cfg, copy)
        append_run_log(asset_root, "storyboard_completed", scene_count=len(scenes),
                       character_count=len(characters))
        save("planned")

    if args.plan_only:
        print(json.dumps(
            {"draft_name": draft_name,
             "characters": [asdict(character) for character in characters],
             "scenes": [asdict(scene) for scene in scenes]},
            ensure_ascii=False, indent=2,
        ))
        # The number the review is for: what making this storyboard would cost.
        legacy = bool(args.resume) and int((previous or {}).get("version", 0)) < MANIFEST_VERSION
        to_draw = frames_to_draw(cfg, scenes, characters, asset_root / "images", legacy)
        print(f"Plan: {len(scenes)} scene(s). " + describe_image_budget(cfg, to_draw, len(scenes) - to_draw)
              + (f" A run would stop before drawing: that is over MAX_IMAGES={cfg.max_images}."
                 if cfg.max_images is not None and to_draw > cfg.max_images else ""))
        return 0

    audio_dir, image_dir = asset_root / "audio", asset_root / "images"
    audio_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)
    stale_speed = drop_stale_narration(scenes, bool(args.resume),
                                       previous_speed, cfg.speed)
    if stale_speed:
        print(f"Speed changed since this run was made "
              f"({previous_speed:.2f}x -> {cfg.speed:.2f}x); the narration is "
              f"being re-read at the new speed. Images are kept.")
        append_run_log(asset_root, "revoice_for_speed", was=previous_speed,
                       now=cfg.speed)
    # Only a file made from a scene's current inputs is picked up - never one
    # that merely has the right number. A fresh run has no older files to
    # trust; a resumed pre-v9 run trusts its own, as it always did.
    legacy = bool(args.resume) and int((previous or {}).get("version", 0)) < MANIFEST_VERSION
    found = reconcile_assets(cfg, scenes, characters, audio_dir, image_dir,
                             legacy_audio=legacy and not stale_speed, legacy_images=legacy)
    for line in explain_reconciled(previous, cfg, found):
        print(line)
    save("reconciled")
    append_run_log(asset_root, "assets_reconciled", **asdict(found))

    # The exact bill, now that the storyboard exists, and before any of it is
    # run up. The estimate printed before planning is a range; this is not.
    to_draw = sum(1 for scene in scenes if not scene.image_path)
    print(describe_image_budget(cfg, to_draw, len(scenes) - to_draw), flush=True)
    append_run_log(asset_root, "images_planned", to_draw=to_draw, reused=len(scenes) - to_draw)
    if cfg.max_images is not None and to_draw > cfg.max_images:
        raise RuntimeError(
            f"Stopped before any narration or picture was paid for: this run would draw {to_draw} "
            f"images and MAX_IMAGES is {cfg.max_images}. The storyboard is saved in "
            f"output/{draft_name}/manifest.json - raise MAX_IMAGES, or shorten the storyboard there, "
            f"and --resume {draft_name}."
        )

    def make_tts(index: int, scene: Scene) -> tuple[int, Path]:
        audio_path = keyed_path(audio_dir, index, narration_key(cfg, scene.text), ".mp3")
        tts.synthesize_tts(cfg, scene.text, audio_path)
        return index, audio_path

    pending_tts = [(index, scene) for index, scene in enumerate(scenes, 1)
                   if not scene.audio_path or not Path(scene.audio_path).is_file()]
    completed_tts = 0
    if pending_tts:
        append_run_log(asset_root, "tts_started", count=len(pending_tts), workers=cfg.tts_concurrency)
        report_progress("Voice-over", 0, len(pending_tts))
        with ThreadPoolExecutor(max_workers=min(cfg.tts_concurrency, len(pending_tts))) as executor:
            futures = {executor.submit(make_tts, index, scene): index for index, scene in pending_tts}
            for future in as_completed(futures):
                index = futures[future]
                try:
                    _, audio_path = future.result()
                except Exception as exc:
                    failure = {"stage": "tts", "scene": index, "error": str(exc)}
                    failures.append(failure)
                    save("failed")
                    append_run_log(asset_root, "tts_failed", **failure)
                    raise
                scenes[index - 1].audio_path = str(audio_path.resolve())
                save("tts_in_progress")
                append_run_log(asset_root, "tts_completed", scene=index)
                completed_tts += 1
                report_progress("Voice-over", completed_tts, len(pending_tts))

    populate_audio_durations(scenes)
    save("audio_durations_ready")

    # The title gets its own voice clip. It is not one of the scenes: the copy
    # is what the scenes narrate, and when --title is given the overlay says
    # something the copy never does - so it was drawn on screen and never read.
    # Cached like the scene clips, so --resume does not pay for it twice.
    if cfg.speak_title and title_language_differs(title, scenes):
        print("Note: the title is not in the same language as the narration, "
              "so it will be read by the narration's voice. Pass a --title in "
              "the same language if that is not what you want.")

    title_audio: Path | None = None
    title_voice = "off"
    if cfg.speak_title and title.strip() and title_already_narrated(title, scenes):
        append_run_log(asset_root, "title_tts_skipped", reason="already said in the opening")
        print("Title voice-over skipped: the copy already opens by saying it.")
        title_voice = "not needed: the copy opens by saying it"
    elif cfg.speak_title and title.strip():
        title_voice = "reused"
        # Keyed like every other clip, not a fixed name. On the title's own
        # text: a fixed name meant resuming with a different --title spoke the
        # OLD title over the new one on screen, and the run looked entirely
        # successful. On the rate: a re-run at 1.2x kept a title read at 1.5x
        # over a card sized for 1.2x. And on the voice, which the old key
        # left out, so a new voice still opened on the old one.
        candidate = audio_dir / f"title_{narration_key(cfg, title)}.mp3"
        if not candidate.is_file() or candidate.stat().st_size == 0:
            append_run_log(asset_root, "title_tts_started")
            report_progress("Title voice", 0, 1)
            try:
                tts.synthesize_tts(cfg, title.strip(), candidate)
            except Exception as exc:
                # A silent title is a worse video, not a broken one. The rest of
                # the run is already paid for; do not throw it away over the
                # opening line.
                append_run_log(asset_root, "title_tts_failed", error=str(exc))
                print(f"Title voice-over failed, continuing without it: {exc}")
                candidate = None
                title_voice = "failed; the video opens without it"
            else:
                append_run_log(asset_root, "title_tts_completed")
                report_progress("Title voice", 1, 1)
                title_voice = "read this run"
        title_audio = candidate if candidate and candidate.is_file() else None

    pending_images = [(index, scene) for index, scene in enumerate(scenes, 1)
                      if not scene.image_path or not Path(scene.image_path).is_file()]

    def make_image(index: int, scene: Scene, reference: str | None) -> tuple[int, Path]:
        image_path = keyed_path(image_dir, index, picture_key(cfg, scene, characters), ".png")
        images.generate_image(cfg, compose_image_prompt(cfg, scene, characters, referenced=reference is not None),
                       image_path, seed=image_seed(cfg, scene), reference=reference)
        return index, image_path

    image_failures: list[dict[str, Any]] = []
    drawn_images = 0

    def draw(batch: list[tuple[int, Scene]], reference: str | None) -> None:
        """Draw a batch in parallel, recording each frame as it lands."""
        nonlocal drawn_images
        if not batch:
            return
        with ThreadPoolExecutor(max_workers=min(cfg.image_concurrency, len(batch)),
                                thread_name_prefix="ark-image") as executor:
            futures = {}
            for index, scene in batch:
                append_run_log(asset_root, "image_started", scene=index)
                futures[executor.submit(make_image, index, scene, reference)] = index

            for future in as_completed(futures):
                index = futures[future]
                try:
                    _, image_path = future.result()
                except Exception as exc:
                    failure = {"stage": "image", "scene": index, "error": str(exc)}
                    failures.append(failure)
                    image_failures.append(failure)
                    save("failed")
                    append_run_log(asset_root, "image_failed", **failure)
                else:
                    scenes[index - 1].image_path = str(image_path.resolve())
                    save("image_in_progress")
                    append_run_log(asset_root, "image_completed", scene=index)
                finally:
                    drawn_images += 1
                    report_progress("Images", drawn_images, len(pending_images))

    if pending_images:
        append_run_log(asset_root, "images_started", count=len(pending_images),
                       workers=min(cfg.image_concurrency, len(pending_images)))
        report_progress("Images", 0, len(pending_images))
        if cfg.image_reference == "anchor":
            # The anchor goes first and alone: every other frame is matched to it.
            anchor = anchor_scene(scenes) + 1
            draw([(index, scene) for index, scene in pending_images if index == anchor], None)
            if image_failures:
                save("failed")
                raise RuntimeError(
                    f"The reference frame (scene {anchor}) failed, so the frames matched to it were not "
                    f"drawn; --resume draws it again. {image_failures[0]['error']}"
                )
            append_run_log(asset_root, "reference_anchor", scene=anchor)
            matched = [(index, scene) for index, scene in pending_images if index != anchor]
            if matched:
                draw(matched, reference_image(Path(scenes[anchor - 1].image_path or "")))
        else:
            draw(pending_images, None)

        if image_failures:
            save("failed")
            first_failure = image_failures[0]
            raise RuntimeError(
                f"{len(image_failures)} image(s) failed; successful images were kept for --resume. "
                f"First failure: scene {first_failure['scene']}: {first_failure['error']}"
            )

    # Chosen here, where both the storyboard and the copy are in hand, and
    # logged: the bed is the one thing in the video nobody reviews, so a run
    # that picked the wrong track should at least say which one it picked.
    bgm_path, bgm_reason = resolve_bgm(cfg, copy, moods)
    append_run_log(asset_root, "bgm_selected",
                   path=str(bgm_path) if bgm_path else None, reason=bgm_reason)
    print(f"BGM: {bgm_reason}")

    report_progress("Draft", 0, 1)
    built: dict[str, Any] = {}
    try:
        draft_path = build_draft(cfg, scenes, draft_name, args.replace, title,
                                 title_audio, cfg.opening_sound_path, bgm_path, report=built)
    except Exception as exc:
        failure = {"stage": "draft", "error": str(exc)}
        failures.append(failure)
        save("failed")
        append_run_log(asset_root, "draft_failed", **failure)
        raise
    report_progress("Draft", 1, 1)
    failures = []
    save("completed")
    append_run_log(asset_root, "completed", draft_path=str(draft_path))
    if title_audio is not None and not built.get("title_voice", True):
        title_voice = "dropped: too long to read before the copy starts"
    summary = run_summary(cfg, scenes, duration_us=built.get("duration_us", 0),
                          narration_read=completed_tts, pictures_drawn=len(pending_images),
                          title_voice=title_voice, music=bgm_reason)
    print(f"Done: {draft_path}")
    print("\n".join(summary))
    append_run_log(asset_root, "summary", lines=[line.strip() for line in summary])
    return 0


def run() -> None:
    """The command's entry point: main(), with failures reported as one line."""
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted; finished assets were kept. Re-run with --resume to continue.", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        if "--verbose" in sys.argv:
            traceback.print_exc()
        print(f"Failed: {exc}", file=sys.stderr)
        sys.exit(1)

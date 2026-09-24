"""Writing the Jianying draft: timeline, captions, title, camera, music and grade."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .bgm import bgm_loop_plan, bgm_volume_envelope, sample_envelope
from .camera import apply_ken_burns, plan_camera
from .config import paced_us
from .layout import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    TITLE_PRESETS,
    characters_per_line,
    fit_title_size,
    paint_characters,
    split_title_lines,
    subtitle_baseline_y,
    subtitle_line_count,
    title_fills,
    title_line_offsets,
)
from .models import Scene, SceneTiming
from .title import MAX_OPENING_LEAD_US, TITLE_TAIL_US, opening_lead, title_voice_fits

if TYPE_CHECKING:
    from .config import Config



NARRATION_SUBTITLE_TRACK = "narration_subtitles"
TITLE_OVERLAY_TRACK = "title_overlay"


def title_track_name(index: int) -> str:
    """One track per title line: title_overlay, title_overlay_2, ..."""
    return TITLE_OVERLAY_TRACK if index == 0 else f"{TITLE_OVERLAY_TRACK}_{index + 1}"


COLOR_GRADE_TRACK = "grade"

# ----------------------------------------------------------------- draft ----

def resolve_enum(enum_cls: Any, name: str, setting: str) -> Any:
    """Look up an animation/transition by its Chinese name with a usable error."""
    members = getattr(enum_cls, "__members__", {})
    if name not in members:
        sample = ", ".join(list(members)[:12])
        raise RuntimeError(
            f"{setting}={name!r} is not a valid {enum_cls.__name__}. "
            f"Set it to 'none' to disable, or pick one of (first 12 of {len(members)}): {sample}"
        )
    return members[name]


def validate_draft_target(cfg: Config, draft_name: str, replace: bool) -> None:
    if (cfg.draft_dir / draft_name).exists() and not replace:
        raise RuntimeError(
            f"Draft already exists: {cfg.draft_dir / draft_name}. "
            "Use a new --draft-name, or add --replace to overwrite it. "
            "Overwriting deletes the whole draft folder, including edits made in Jianying."
        )


def validate_plan_target(asset_root: Path, draft_name: str, replace: bool) -> None:
    """Planning writes no draft, but it does replace the run's storyboard."""
    if (asset_root / "manifest.json").is_file() and not replace:
        raise RuntimeError(
            f"output/{draft_name} already holds a storyboard. Use a new --draft-name, "
            f"--resume {draft_name} --plan-only to print the one it has, "
            "or add --replace to plan it afresh."
        )


def build_draft(cfg: Config, scenes: list[Scene], draft_name: str, replace: bool,
                title: str, title_audio: Path | None = None,
                opening_sound: Path | None = None,
                bgm: Path | None = None,
                report: dict[str, Any] | None = None) -> Path:
    """Write the draft. `bgm` is the track resolve_bgm settled on.

    `report`, when given, is filled with what only the build knows: the
    video's length, and whether the title's voice made it in.

    Falling back to cfg.bgm_path when it is None is not a second decision:
    resolve_bgm returns exactly that whenever BGM_PATH is set, so the two
    cannot disagree, and a caller that has not resolved anything still gets
    the explicitly configured track.
    """
    from pyJianYingDraft import (
        AudioMaterial,
        AudioSegment,
        ClipSettings,
        DraftFolder,
        FilterType,
        FontType,
        KeyframeProperty,
        TextBackground,
        TextBorder,
        TextIntro,
        TextOutro,
        TextSegment,
        TextShadow,
        TextStyle,
        Timerange,
        TrackSpec,
        TrackType,
        VideoSegment,
    )

    subtitle_intro = (
        None if cfg.subtitle_animation.lower() in {"", "none", "off"} or not cfg.subtitle_animation_us
        else resolve_enum(TextIntro, cfg.subtitle_animation, "SUBTITLE_ANIMATION")
    )
    title_intro = (
        None if cfg.title_animation.lower() in {"", "none", "off"}
        else resolve_enum(TextIntro, cfg.title_animation, "TITLE_ANIMATION")
    )
    title_outro = (
        None if cfg.title_outro.lower() in {"", "none", "off"}
        else resolve_enum(TextOutro, cfg.title_outro, "TITLE_OUTRO")
    )
    grade = (
        None if cfg.color_grade.lower() in {"", "none", "off"} or not cfg.color_grade_intensity
        else resolve_enum(FilterType, cfg.color_grade, "COLOR_GRADE")
    )

    draft = DraftFolder(str(cfg.draft_dir)).create_draft(
        draft_name, CANVAS_WIDTH, CANVAS_HEIGHT, fps=30, allow_replace=replace
    )
    video_track = draft.append_track(TrackSpec(TrackType.video, "visuals"))
    audio_track = draft.append_track(TrackSpec(TrackType.audio, "voiceover"))
    opening_sfx_track = draft.append_track(TrackSpec(TrackType.audio, "opening_sfx"))
    watermark_track = (
        draft.append_track(TrackSpec(TrackType.video, "watermark")) if cfg.watermark_path else None
    )
    narration_subtitle_track = draft.append_track(TrackSpec(TrackType.text, NARRATION_SUBTITLE_TRACK))
    # The title is one text segment per line, and the lines are on screen at
    # the same time, so each needs its own track: a Jianying text track holds
    # one segment at a time.
    title_lines = split_title_lines(
        title, characters_per_line(cfg.title_size, cfg.title_max_line_width, cfg.subtitle_em_px)
    )
    title_tracks = [
        draft.append_track(TrackSpec(TrackType.text, title_track_name(index)))
        for index in range(len(title_lines))
    ]
    bgm_path = bgm or cfg.bgm_path
    bgm_material = AudioMaterial(str(bgm_path)) if bgm_path else None
    bgm_track = draft.append_track(TrackSpec(TrackType.audio, "BGM")) if bgm_material else None
    grade_track = draft.append_track(TrackSpec(TrackType.filter, COLOR_GRADE_TRACK)) if grade else None

    subtitle_font = resolve_enum(FontType, cfg.subtitle_font, "SUBTITLE_FONT")
    title_font = resolve_enum(FontType, cfg.title_font, "TITLE_FONT")
    if cfg.subtitle_style == "box":
        subtitle_colour, subtitle_border, subtitle_background = (
            (0.0, 0.0, 0.0),
            TextBorder(color=(1.0, 1.0, 1.0), width=cfg.subtitle_border_width),
            TextBackground(color="#000000", alpha=0.3, round_radius=0.2, height=0.14,
                           width=0.14, horizontal_offset=0.5, vertical_offset=0.5),
        )
    elif cfg.subtitle_style == "outline":
        # White fill on a black stroke and a soft shadow, no plate. Cleanest
        # over pale art, where a dark plate reads as a bar stuck on the frame.
        subtitle_colour, subtitle_border, subtitle_background = (
            (1.0, 1.0, 1.0),
            TextBorder(color=(0.0, 0.0, 0.0), width=cfg.subtitle_border_width),
            None,
        )
    else:
        # The reference treatment: white text on a thin dark stroke, sitting on
        # a dark translucent plate roughly one and a half line-heights tall.
        # The plate is what lets the caption stay this small and this light and
        # still hold against a busy panel; without it the size would have to go
        # back up and the caption would start competing with the picture.
        subtitle_colour, subtitle_border, subtitle_background = (
            (1.0, 1.0, 1.0),
            TextBorder(color=(0.04, 0.06, 0.10), width=cfg.subtitle_border_width),
            TextBackground(color="#0B1826", alpha=0.55, round_radius=0.12, height=0.30,
                           width=0.10, horizontal_offset=0.5, vertical_offset=0.5),
        )

    materials = [AudioMaterial(scene.audio_path or "") for scene in scenes]
    for scene, material in zip(scenes, materials, strict=True):
        scene.duration_us = material.duration

    # The title is read aloud over the stinger's decay, and the copy waits for
    # it. Without the wait the first line talks over the title's own voice: the
    # configured 0.8 s lead is enough for the stinger alone and about half of
    # what a spoken title needs.
    title_material = AudioMaterial(str(title_audio)) if title_audio else None
    # The breath after the title and the cap on the whole head are baseline
    # numbers like everything else in this package; cfg already holds its own
    # durations on the video's clock, and these two live at module scope, so
    # they are the two that have to be put on it here.
    tail_us = paced_us(TITLE_TAIL_US, cfg.speed)
    cap_us = paced_us(MAX_OPENING_LEAD_US, cfg.speed)
    if title_material is not None and not title_voice_fits(
            title_material.duration, cfg.title_lead_us, tail_us, cap_us):
        # Too long to read before the copy has to start. The type stays; only
        # the voice-over goes. Silently clamping instead would talk the copy
        # over the tail of its own title.
        print(f"Title voice-over skipped: reading it takes "
              f"{title_material.duration / 1e6:.1f}s and the opening may not "
              f"run past {cap_us / 1e6:.1f}s. Use a shorter --title.")
        title_material = None
    lead_us = opening_lead(cfg.opening_lead_us,
                           title_material.duration if title_material else 0,
                           cfg.title_lead_us, tail_us, cap_us)

    timings, total_duration = plan_timeline(
        [material.duration for material in materials],
        [scene.pause_after for scene in scenes],
        lead_us, cfg.paragraph_pause_us, cfg.ending_hold_us,
    )

    camera = plan_camera(scenes)
    for index, (scene, audio, timing) in enumerate(zip(scenes, materials, timings, strict=True), 1):
        narration_range = Timerange(timing.narration_start, timing.narration_duration)

        # One image, one uninterrupted segment. Scenes are never split, and the
        # cut into the next scene is hard: no transition, no intro animation.
        # All of the motion comes from the keyframed camera move. The picture
        # also covers the pause after its scene and the hold at the end, so a
        # breath never shows as a black frame.
        video = VideoSegment(scene.image_path or "",
                             Timerange(timing.visual_start, timing.visual_duration))
        if cfg.ken_burns_rate > 0:
            apply_ken_burns(video, KeyframeProperty, timing.visual_duration, camera[index - 1],
                            cfg.ken_burns_rate)
        draft.add_segment(video, video_track)

        subtitle = TextSegment(
            scene.text, narration_range,
            font=subtitle_font,
            style=TextStyle(size=cfg.subtitle_size, color=subtitle_colour, align=1,
                            letter_spacing=cfg.subtitle_letter_spacing,
                            auto_wrapping=True, max_line_width=cfg.subtitle_max_line_width),
            clip_settings=ClipSettings(transform_x=0.0, transform_y=subtitle_baseline_y(
                cfg.subtitle_y,
                subtitle_line_count(scene.text, cfg.subtitle_size,
                                    cfg.subtitle_max_line_width, cfg.subtitle_em_px),
                cfg.subtitle_size, cfg.subtitle_em_px,
            )),
            border=subtitle_border,
            background=subtitle_background,
            shadow=TextShadow(alpha=0.7, diffuse=25.0, distance=8.0, angle=-90.0),
        )
        if subtitle_intro is not None:
            subtitle.add_animation(subtitle_intro, duration=cfg.subtitle_animation_us)
        draft.add_segment(subtitle, narration_subtitle_track)

        draft.add_segment(AudioSegment(audio, narration_range), audio_track)

    if grade_track is not None and grade is not None:
        # One grade across the whole video. Independently generated panels drift
        # in temperature and brightness; a single light pass pulls them together.
        draft.add_filter(grade, Timerange(0, total_duration), COLOR_GRADE_TRACK,
                         cfg.color_grade_intensity)

    title_span: tuple[int, int] | None = None
    if title_material is not None:
        title_span = (cfg.title_lead_us, cfg.title_lead_us + title_material.duration)
        draft.add_segment(
            AudioSegment(title_material,
                         Timerange(cfg.title_lead_us, title_material.duration)),
            audio_track)

    # Defaulting to the configured cue rather than to nothing. The cue is
    # required and always resolvable from cfg, so there is no "no cue" case to
    # express - and a caller that omits the argument previously got
    # AudioMaterial("None") and a broken draft.
    add_opening_sound(cfg, draft, opening_sfx_track, total_duration,
                      opening_sound or cfg.opening_sound_path)
    if watermark_track is not None and cfg.watermark_path:
        watermark = VideoSegment(
            str(cfg.watermark_path), Timerange(0, total_duration),
            clip_settings=ClipSettings(scale_x=1.0, scale_y=1.0, transform_x=0.0, transform_y=0.0),
        )
        draft.add_segment(watermark, watermark_track)

    # The overlay holds at least as long as the lead: a title that vanishes
    # while its own voice is still reading it reads as a timing bug.
    title_hold = max(cfg.title_us, lead_us)
    add_title(cfg, draft, title_tracks, title_lines, total_duration, title_intro,
              title_outro, title_font, title_hold)
    if bgm_material is not None and bgm_track is not None:
        speech = [(timing.narration_start, timing.narration_end) for timing in timings]
        if title_span is not None:
            speech.insert(0, title_span)
        add_bgm(cfg, draft, bgm_track, bgm_material, total_duration, speech)

    draft.save()
    write_draft_meta(cfg.draft_dir / draft_name, draft_name)
    if report is not None:
        report.update(duration_us=total_duration, title_voice=title_material is not None)
    return cfg.draft_dir / draft_name


def write_draft_meta(draft_path: Path, draft_name: str) -> None:
    """Name the draft in its own metadata.

    pyJianYingDraft's create_draft copies its meta template verbatim, so
    draft_name, draft_fold_path and draft_root_path are left empty strings.
    Jianying lists drafts by folder, which is why this has never stopped one
    appearing - but a draft that does not know its own name or where it lives
    is wrong in a way that costs nothing to fix, and the sibling tool that
    writes the same format fills these in deliberately.
    """
    meta_path = draft_path / "draft_meta_info.json"
    if not meta_path.is_file():
        return
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
        meta["draft_name"] = draft_name
        meta["draft_fold_path"] = str(draft_path)
        meta["draft_root_path"] = str(draft_path.parent)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    except (ValueError, OSError) as exc:
        # The draft itself is written and openable; its label is not worth
        # failing a finished run over.
        print(f"Could not label the draft metadata: {exc}")


def add_opening_sound(cfg: Config, draft: Any, track: Any, total_duration: int,
                      sound_path: Path) -> None:
    from pyJianYingDraft import AudioMaterial, AudioSegment, Timerange

    material = AudioMaterial(str(sound_path))
    duration = min(material.duration, total_duration)
    if duration <= 0:
        return
    segment = AudioSegment(material, Timerange(0, duration),
                           source_timerange=Timerange(0, duration), volume=cfg.opening_sound_volume)
    segment.add_fade(0, min(200_000, duration // 2))
    draft.add_segment(segment, track)


def add_title(cfg: Config, draft: Any, tracks: list[Any], lines: list[str], total_duration: int,
              title_intro: Any, title_outro: Any, font: Any,
              hold_us: int | None = None) -> None:
    """Stack the title lines and colour them, ramped or one colour per line.

    The reference title runs warm white into crimson across a two-line block.
    Each line is still its own segment, because that is what puts the line
    pitch under our control - the reference sets it tighter than any text
    default would - and the ramp runs through the lines in reading order.
    """
    from pyJianYingDraft import ClipSettings, TextBorder, TextSegment, TextShadow, TextStyle, Timerange

    duration = min(total_duration, cfg.title_us if hold_us is None else hold_us)
    if duration <= 0 or not lines:
        return
    colours = TITLE_PRESETS[cfg.title_style]
    fills = title_fills(lines, colours, cfg.title_ramp)
    size = fit_title_size(lines, cfg.title_size, cfg.title_max_line_width, cfg.subtitle_em_px)
    offsets = title_line_offsets(len(lines), cfg.title_y, size, cfg.subtitle_em_px)
    for line, track, offset, line_fills in zip(lines, tracks, offsets, fills, strict=True):
        segment = TextSegment(
            line, Timerange(0, duration),
            font=font,
            style=TextStyle(size=size, bold=True, align=1, color=line_fills[0],
                            letter_spacing=cfg.subtitle_letter_spacing, auto_wrapping=False),
            border=TextBorder(color=colours.border, width=cfg.title_border_width),
            # Hard and offset down-right rather than soft and centred: the
            # reference shadow reads as a second layer of type behind the first.
            shadow=TextShadow(alpha=0.85, diffuse=8.0, distance=14.0, angle=-55.0),
            clip_settings=ClipSettings(transform_x=0.0, transform_y=offset),
        )
        paint_characters(segment, line_fills)
        if title_intro is not None:
            segment.add_animation(title_intro, duration=min(500_000, duration))
        if title_outro is not None:
            segment.add_animation(title_outro, duration=min(500_000, duration))
        draft.add_segment(segment, track)


def plan_timeline(durations: list[int], pauses: list[bool], lead_us: int,
                  pause_us: int, hold_us: int) -> tuple[list[SceneTiming], int]:
    """Lay out narration and picture, with room to breathe.

    A scene marked `pause_after` is followed by a beat of BGM only, so the
    paragraph breaks in the copy are audible instead of every sentence running
    into the next. The outgoing picture holds through that beat rather than
    cutting to black, and the last picture holds again at the end so the video
    does not stop dead on the final syllable.
    """
    timings: list[SceneTiming] = []
    cursor = lead_us
    last = len(durations) - 1
    for index, duration in enumerate(durations):
        gap = pause_us if (pauses[index] and index != last) else 0
        hold = hold_us if index == last else 0
        visual_start = 0 if index == 0 else cursor
        visual_duration = duration + gap + hold + (lead_us if index == 0 else 0)
        timings.append(SceneTiming(cursor, duration, visual_start, visual_duration))
        cursor += duration + gap
    return timings, cursor + (hold_us if durations else 0)


def add_bgm(cfg: Config, draft: Any, track: Any, material: Any, total_duration: int,
            speech_spans: list[tuple[int, int]]) -> None:
    """Loop the BGM under the narration, ducked under speech and lifted in the gaps.

    Volume keyframes carry the level and the segment volume is left at 1.0, so
    the result is the same whether Jianying treats keyframes as replacing the
    static volume or as multiplying it. The loop plan's fades multiply on top
    and handle the head, the tail and the seams.
    """
    from pyJianYingDraft import AudioSegment, Timerange

    if cfg.bgm_volume <= 0:
        return
    lift = max(cfg.bgm_volume, cfg.bgm_lift_volume)
    envelope = bgm_volume_envelope(speech_spans, total_duration, cfg.bgm_volume, lift, cfg.bgm_ramp_us)
    for start, duration, fade_in, fade_out in bgm_loop_plan(total_duration, material.duration):
        segment = AudioSegment(material, Timerange(start, duration),
                               source_timerange=Timerange(0, duration), volume=1.0)
        segment.add_keyframe(0, sample_envelope(envelope, start))
        for moment, volume in envelope:
            if start < moment < start + duration:
                segment.add_keyframe(moment - start, volume)
        segment.add_keyframe(duration, sample_envelope(envelope, start + duration))
        segment.add_fade(fade_in, fade_out)
        draft.add_segment(segment, track)



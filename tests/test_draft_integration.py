"""Build a real Jianying draft from synthetic media and inspect the result.

This is the test that would have caught the timeline bugs: an off-screen
subtitle, a BGM that never fades, and a dynamic clip a few milliseconds shorter
than its narration aborting the whole build. It needs pyJianYingDraft's media
backend (pymediainfo) and Pillow, and skips cleanly when either is missing.
"""

from __future__ import annotations

import json
import struct
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import animated_caption_draft as acd  # noqa: E402

pytest.importorskip("PIL", reason="Pillow is required to synthesise test images")
pytest.importorskip("pymediainfo", reason="pyJianYingDraft needs pymediainfo to probe media")

SECOND = 1_000_000
SCENE_SECONDS = (2.1, 5.4, 3.9, 1.6, 6.2)  # three of these exceed MAX_SHOT_SECONDS


def _wav(path: Path, seconds: float) -> Path:
    frames = int(24000 * seconds)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(struct.pack(f"<{frames}h", *([0] * frames)))
    return path


def _png(path: Path, width: int, height: int) -> Path:
    from PIL import Image

    Image.new("RGB", (width, height), (30, 50, 90)).save(path)
    return path


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    assets, drafts = tmp_path / "assets", tmp_path / "drafts"
    assets.mkdir()
    drafts.mkdir()
    _wav(assets / "opening.wav", 1.2)
    _wav(assets / "bgm.wav", 4.0)  # deliberately shorter than the video, so it loops
    _png(assets / "watermark.png", 200, 80)
    for index, seconds in enumerate(SCENE_SECONDS, 1):
        _wav(assets / f"{index:02d}.wav", seconds)
        _png(assets / f"{index:02d}.png", 2560, 1440)

    for name, value in {
        "ARK_API_KEY": "test-key",
        "ARK_TTS_VOICE_TYPE": "test-voice",
        "JIAN_YING_DRAFT_DIR": str(drafts),
        "OPENING_SOUND_PATH": str(assets / "opening.wav"),
        "BGM_PATH": str(assets / "bgm.wav"),
        "WATERMARK_PATH": str(assets / "watermark.png"),
        "NARRATION_SUBTITLE_Y": "-700",
    }.items():
        monkeypatch.setenv(name, value)
    return assets, drafts


def _scenes(assets: Path) -> list[acd.Scene]:
    return [
        acd.Scene(
            text=f"这是第{index}句旁白文案内容",
            image_prompt="a person at a desk",
            keyword=f"第{index}句" if index % 2 else None,
            cast=["A"],
            audio_path=str(assets / f"{index:02d}.wav"),
            image_path=str(assets / f"{index:02d}.png"),
        )
        for index in range(1, len(SCENE_SECONDS) + 1)
    ]


@pytest.fixture
def draft(workspace):
    assets, drafts = workspace
    cfg = acd.Config.load(skip_i2v=True)
    path = acd.build_draft(cfg, _scenes(assets), "pytest_draft", replace=False, title="测试标题")
    content = json.loads((path / "draft_content.json").read_text(encoding="utf-8"))
    return cfg, content, {track["name"]: track for track in content["tracks"]}


def _sorted_segments(track):
    return sorted(track["segments"], key=lambda segment: segment["target_timerange"]["start"])


def test_canvas_stays_landscape(draft):
    _, content, _ = draft
    assert (content["canvas_config"]["width"], content["canvas_config"]["height"]) == (1920, 1080)


def test_subtitle_lands_on_screen(draft):
    """NARRATION_SUBTITLE_Y=-700 must render inside the frame, not below it."""
    _, _, tracks = draft
    for segment in tracks[acd.NARRATION_SUBTITLE_TRACK]["segments"]:
        y = segment["clip"]["transform"]["y"]
        assert -1.0 < y < 0.0, f"subtitle at transform_y={y} is outside the visible frame"


def test_narration_starts_after_the_opening_lead(draft):
    cfg, _, tracks = draft
    first = _sorted_segments(tracks[acd.NARRATION_SUBTITLE_TRACK])[0]
    assert first["target_timerange"]["start"] == cfg.opening_lead_us


def test_visual_track_is_gapless_and_matches_the_narration(draft):
    cfg, _, tracks = draft
    previous_end = 0
    for segment in _sorted_segments(tracks["visuals"]):
        start = segment["target_timerange"]["start"]
        assert start == previous_end, f"gap or overlap at {start}us"
        previous_end = start + segment["target_timerange"]["duration"]
    expected = cfg.opening_lead_us + sum(round(seconds * SECOND) for seconds in SCENE_SECONDS)
    assert abs(previous_end - expected) < SECOND // 10


def test_long_scenes_are_split_into_two_shots(draft):
    _, _, tracks = draft
    shots = len(tracks["visuals"]["segments"])
    long_scenes = sum(1 for seconds in SCENE_SECONDS if seconds * SECOND > 3 * SECOND)
    assert shots == len(SCENE_SECONDS) + long_scenes


def test_every_shot_has_camera_movement(draft):
    _, _, tracks = draft
    for segment in tracks["visuals"]["segments"]:
        properties = {kf["property_type"] for kf in segment["common_keyframes"]}
        assert properties, "a shot with no keyframes would be a frozen still"


def test_pans_never_start_at_full_frame(draft):
    """A pan at scale 1.0 exposes the edge of the image."""
    _, _, tracks = draft
    for segment in tracks["visuals"]["segments"]:
        for keyframe_list in segment["common_keyframes"]:
            if keyframe_list["property_type"] != "KFTypeScaleX":
                continue
            for keyframe in keyframe_list["keyframe_list"]:
                assert keyframe["values"][0] > 1.0


def test_bgm_loops_and_fades_at_both_ends(draft):
    _, content, tracks = draft
    fades = {fade["id"]: fade for fade in content["materials"]["audio_fades"]}
    segments = _sorted_segments(tracks["BGM"])
    assert len(segments) > 1, "a 4s track under a 20s video must loop"

    def fade_of(segment):
        return next((fades[ref] for ref in segment["extra_material_refs"] if ref in fades), None)

    assert fade_of(segments[0])["fade_in_duration"] > 0, "the video must not start on a blast of music"
    assert fade_of(segments[-1])["fade_out_duration"] > 0, "the video must not end on a hard cut"


def test_bgm_is_quiet_enough_to_sit_under_narration(draft):
    cfg, _, tracks = draft
    assert cfg.bgm_volume <= 0.2
    for segment in tracks["BGM"]["segments"]:
        assert segment["volume"] <= 0.2


def test_keyword_track_only_carries_scenes_that_have_one(draft):
    _, _, tracks = draft
    expected = sum(1 for index in range(1, len(SCENE_SECONDS) + 1) if index % 2)
    assert len(tracks[acd.KEYWORD_OVERLAY_TRACK]["segments"]) == expected


def test_keyword_sits_above_the_subtitle(draft):
    _, _, tracks = draft
    subtitle_y = tracks[acd.NARRATION_SUBTITLE_TRACK]["segments"][0]["clip"]["transform"]["y"]
    keyword_y = tracks[acd.KEYWORD_OVERLAY_TRACK]["segments"][0]["clip"]["transform"]["y"]
    assert keyword_y > subtitle_y


def test_title_sits_in_the_upper_half(draft):
    _, _, tracks = draft
    assert tracks[acd.TITLE_OVERLAY_TRACK]["segments"][0]["clip"]["transform"]["y"] > 0


def test_text_layers_are_animated(draft):
    _, content, tracks = draft
    animations = {item["id"] for item in content["materials"].get("material_animations", [])}
    for name in (acd.NARRATION_SUBTITLE_TRACK, acd.KEYWORD_OVERLAY_TRACK, acd.TITLE_OVERLAY_TRACK):
        for segment in tracks[name]["segments"]:
            assert animations & set(segment["extra_material_refs"]), f"{name} segment has no intro animation"


def test_existing_draft_is_not_overwritten_without_replace(workspace):
    assets, _ = workspace
    cfg = acd.Config.load(skip_i2v=True)
    acd.build_draft(cfg, _scenes(assets), "guarded", replace=False, title="标题")
    with pytest.raises(RuntimeError, match="Draft already exists"):
        acd.validate_draft_target(cfg, "guarded", replace=False)
    acd.validate_draft_target(cfg, "guarded", replace=True)


def _clip(path: Path, seconds: float) -> Path:
    """An animated GIF is the cheapest thing pyJianYingDraft accepts as video."""
    from PIL import Image

    frames = [Image.new("RGB", (320, 180), (30, 50, 90)) for _ in range(int(seconds * 10))]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=100, loop=0)
    return path


def test_short_dynamic_clip_does_not_abort_the_build(workspace):
    """A clip shorter than its narration used to raise, which made every later
    --resume fail forever on assets that had already been paid for."""
    assets, _ = workspace
    cfg = acd.Config.load(skip_i2v=True)
    scenes = _scenes(assets)
    # 2.0s of video under a 2.1s narration, the frame-rounding case.
    scenes[0].video_path = str(_clip(assets / "short.gif", 2.0))

    path = acd.build_draft(cfg, scenes, "short_clip", replace=False, title="标题")

    content = json.loads((path / "draft_content.json").read_text(encoding="utf-8"))
    tracks = {track["name"]: track for track in content["tracks"]}
    visuals = _sorted_segments(tracks["visuals"])
    # The clip plays for what it has, then the still covers the remainder, so
    # the track stays gapless and nothing drifts out of sync.
    assert visuals[0]["target_timerange"]["duration"] == 2 * SECOND
    assert visuals[1]["target_timerange"]["start"] == 2 * SECOND
    previous_end = 0
    for segment in visuals:
        assert segment["target_timerange"]["start"] == previous_end
        previous_end = segment["target_timerange"]["start"] + segment["target_timerange"]["duration"]

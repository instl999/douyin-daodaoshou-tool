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
            cast=["A"],
            audio_path=str(assets / f"{index:02d}.wav"),
            image_path=str(assets / f"{index:02d}.png"),
        )
        for index in range(1, len(SCENE_SECONDS) + 1)
    ]


@pytest.fixture
def draft(workspace):
    assets, drafts = workspace
    cfg = acd.Config.load()
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


def test_title_sits_in_the_upper_half(draft):
    _, _, tracks = draft
    assert tracks[acd.TITLE_OVERLAY_TRACK]["segments"][0]["clip"]["transform"]["y"] > 0


def test_text_layers_are_animated(draft):
    _, content, tracks = draft
    animations = {item["id"] for item in content["materials"].get("material_animations", [])}
    for name in (acd.NARRATION_SUBTITLE_TRACK, acd.TITLE_OVERLAY_TRACK):
        for segment in tracks[name]["segments"]:
            assert animations & set(segment["extra_material_refs"]), f"{name} segment has no intro animation"


def test_existing_draft_is_not_overwritten_without_replace(workspace):
    assets, _ = workspace
    cfg = acd.Config.load()
    acd.build_draft(cfg, _scenes(assets), "guarded", replace=False, title="标题")
    with pytest.raises(RuntimeError, match="Draft already exists"):
        acd.validate_draft_target(cfg, "guarded", replace=False)
    acd.validate_draft_target(cfg, "guarded", replace=True)


def test_shots_have_no_transition_or_intro_animation(draft):
    """Cuts are hard: only the keyframed camera move carries motion."""
    _, content, tracks = draft
    animations = {item["id"] for item in content["materials"].get("material_animations", [])}
    transitions = {item["id"] for item in content["materials"].get("transitions", [])}
    for segment in tracks["visuals"]["segments"]:
        refs = set(segment["extra_material_refs"])
        assert not (refs & animations), "shots must not carry an intro/outro animation"
        assert not (refs & transitions), "shots must not carry a transition"


def test_only_the_expected_tracks_exist(draft):
    """Guards against a keyword or image-to-video track creeping back in."""
    _, _, tracks = draft
    assert set(tracks) == {
        "visuals", "voiceover", "opening_sfx", "watermark",
        acd.NARRATION_SUBTITLE_TRACK, acd.TITLE_OVERLAY_TRACK, "BGM",
    }


def test_style_presets_are_selectable(monkeypatch, workspace):
    monkeypatch.setenv("IMAGE_STYLE_PRESET", "webtoon")
    assert acd.Config.load().image_style_prompt == acd.STYLE_PRESETS["webtoon"]
    monkeypatch.setenv("IMAGE_STYLE_PROMPT", "my own style")
    assert acd.Config.load().image_style_prompt == "my own style"


def test_unknown_style_preset_is_rejected(monkeypatch, workspace):
    monkeypatch.setenv("IMAGE_STYLE_PRESET", "nonexistent")
    with pytest.raises(RuntimeError, match="IMAGE_STYLE_PRESET"):
        acd.Config.load()

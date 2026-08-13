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
SCENE_SECONDS = (2.1, 5.4, 3.9, 1.6, 6.2)


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
            shot_size=("wide", "medium", "close")[index % 3],
            pause_after=(index == 2),
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


def test_visual_track_is_gapless_and_covers_the_whole_video(draft):
    cfg, content, tracks = draft
    previous_end = 0
    for segment in _sorted_segments(tracks["visuals"]):
        start = segment["target_timerange"]["start"]
        assert start == previous_end, f"gap or overlap at {start}us -- a gap would show as black"
        previous_end = start + segment["target_timerange"]["duration"]
    expected = (cfg.opening_lead_us
                + sum(round(seconds * SECOND) for seconds in SCENE_SECONDS)
                + cfg.paragraph_pause_us      # one scene is marked pause_after
                + cfg.ending_hold_us)
    assert abs(previous_end - expected) < SECOND // 10
    assert abs(content["duration"] - expected) < SECOND // 10


def test_the_picture_holds_after_the_last_word(draft):
    """The video must not stop dead on the final syllable."""
    cfg, _, tracks = draft
    last_visual = _sorted_segments(tracks["visuals"])[-1]
    last_caption = _sorted_segments(tracks[acd.NARRATION_SUBTITLE_TRACK])[-1]
    visual_end = last_visual["target_timerange"]["start"] + last_visual["target_timerange"]["duration"]
    caption_end = last_caption["target_timerange"]["start"] + last_caption["target_timerange"]["duration"]
    assert visual_end - caption_end == pytest.approx(cfg.ending_hold_us, abs=SECOND // 20)


def test_a_paragraph_pause_appears_between_two_captions(draft):
    cfg, _, tracks = draft
    captions = _sorted_segments(tracks[acd.NARRATION_SUBTITLE_TRACK])
    gaps = [later["target_timerange"]["start"]
            - (earlier["target_timerange"]["start"] + earlier["target_timerange"]["duration"])
            for earlier, later in zip(captions, captions[1:], strict=False)]
    assert max(gaps) == pytest.approx(cfg.paragraph_pause_us, abs=SECOND // 20)
    assert gaps.count(0) == len(gaps) - 1, "only the marked scene should be followed by a pause"


def test_one_image_is_one_uninterrupted_segment(draft):
    """No scene may be split, however long its narration runs."""
    cfg, _, tracks = draft
    visuals = _sorted_segments(tracks["visuals"])
    assert len(visuals) == len(SCENE_SECONDS)

    expected = [round(seconds * SECOND) for seconds in SCENE_SECONDS]
    expected[0] += cfg.opening_lead_us      # the first still covers the opening lead
    expected[1] += cfg.paragraph_pause_us   # and each still covers the pause after it
    expected[-1] += cfg.ending_hold_us      # and the last one holds at the end
    for segment, want in zip(visuals, expected, strict=True):
        got = segment["target_timerange"]["duration"]
        assert abs(got - want) < SECOND // 20, "a scene's image was cut into more than one shot"


def test_each_scene_uses_a_different_image(draft):
    """01.png must be followed by 02.png, not by a second slice of 01.png."""
    _, content, tracks = draft
    videos = {material["id"]: material["path"] for material in content["materials"]["videos"]}
    used = [videos[segment["material_id"]] for segment in _sorted_segments(tracks["visuals"])]
    assert len(set(used)) == len(used), f"the same image appears in consecutive segments: {used}"


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


def test_bgm_ducks_under_speech_and_lifts_in_the_gaps(draft):
    """Levels live in volume keyframes; the static volume stays at 1.0 so the
    result is right whether Jianying replaces or multiplies it."""
    cfg, _, tracks = draft
    assert cfg.bgm_volume <= 0.2
    levels = []
    for segment in tracks["BGM"]["segments"]:
        assert segment["volume"] == 1.0
        keyframes = [kf for kf in segment["common_keyframes"] if kf["property_type"] == "KFTypeVolume"]
        assert keyframes, "every BGM segment needs keyframes, or it plays at full volume"
        levels += [point["values"][0] for point in keyframes[0]["keyframe_list"]]
    assert min(levels) == pytest.approx(cfg.bgm_volume)
    assert max(levels) == pytest.approx(max(cfg.bgm_volume, cfg.bgm_lift_volume))


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
        "visuals", "voiceover", "opening_sfx", "watermark", "BGM",
        acd.NARRATION_SUBTITLE_TRACK, acd.TITLE_OVERLAY_TRACK, acd.COLOR_GRADE_TRACK,
    }


def test_one_colour_grade_spans_the_whole_video(draft):
    """Independently generated panels drift; a single pass pulls them together."""
    cfg, content, tracks = draft
    segments = tracks[acd.COLOR_GRADE_TRACK]["segments"]
    assert len(segments) == 1
    assert segments[0]["target_timerange"]["start"] == 0
    assert segments[0]["target_timerange"]["duration"] == content["duration"]
    intensity = content["materials"]["effects"][0]["value"]
    assert intensity == pytest.approx(cfg.color_grade_intensity / 100.0)


def test_style_presets_are_selectable(monkeypatch, workspace):
    monkeypatch.setenv("IMAGE_STYLE_PRESET", "webtoon")
    assert acd.Config.load().image_style_prompt == acd.STYLE_PRESETS["webtoon"]
    monkeypatch.setenv("IMAGE_STYLE_PROMPT", "my own style")
    assert acd.Config.load().image_style_prompt == "my own style"


def test_unknown_style_preset_is_rejected(monkeypatch, workspace):
    monkeypatch.setenv("IMAGE_STYLE_PRESET", "nonexistent")
    with pytest.raises(RuntimeError, match="IMAGE_STYLE_PRESET"):
        acd.Config.load()

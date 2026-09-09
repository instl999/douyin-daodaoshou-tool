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
    # The reference video's own title: long enough to break, so the fixture
    # exercises the stacked two-line layout rather than the trivial one.
    path = acd.build_draft(cfg, _scenes(assets), "pytest_draft", replace=False,
                           title="男人不能为女人做的3件事",
                           opening_sound=acd.resolve_opening_sound(cfg, drafts))
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


def _title_segments(content):
    """The title lines, top line first, across their one-track-each layout."""
    tracks = {track["name"]: track for track in content["tracks"]}
    named = [tracks[name] for name in (acd.title_track_name(i) for i in range(acd.MAX_TITLE_LINES))
             if name in tracks]
    segments = [track["segments"][0] for track in named if track["segments"]]
    return sorted(segments, key=lambda s: -s["clip"]["transform"]["y"])


def _fill(text_content):
    return text_content["styles"][0]["fill"]["content"]["solid"]["color"]


def test_the_title_is_one_segment_per_line(draft):
    """Two lines, because Jianying colours a segment as a whole."""
    _, content, _ = draft
    segments = _title_segments(content)
    assert len(segments) == 2
    assert all(segment["target_timerange"]["start"] == 0 for segment in segments)


def test_the_title_block_is_centred_and_the_lines_do_not_overlap(draft):
    cfg, content, _ = draft
    ys = [segment["clip"]["transform"]["y"] for segment in _title_segments(content)]
    assert sum(ys) == pytest.approx(cfg.title_y, abs=1e-9), "the block is centred on TITLE_Y"
    pitch = (ys[0] - ys[1]) * acd.CANVAS_HALF_HEIGHT
    assert pitch == pytest.approx(182, abs=3)


def test_the_title_lines_carry_the_two_colours_of_the_style(draft):
    cfg, content, _ = draft
    colours = acd.TITLE_PRESETS[cfg.title_style]
    materials = {item["id"]: item for item in content["materials"]["texts"]}
    first, second = (json.loads(materials[s["material_id"]]["content"])
                     for s in _title_segments(content))
    assert _fill(first) == pytest.approx(list(colours.primary), abs=1e-3)
    assert _fill(second) == pytest.approx(list(colours.accent), abs=1e-3)
    assert _fill(first) != _fill(second), "the reference title is not one flat colour"


def test_text_layers_are_animated(draft):
    _, content, tracks = draft
    animations = {item["id"] for item in content["materials"].get("material_animations", [])}
    named = [acd.NARRATION_SUBTITLE_TRACK] + [acd.title_track_name(i) for i in range(acd.MAX_TITLE_LINES)]
    for name in named:
        for segment in tracks.get(name, {"segments": []})["segments"]:
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
        acd.NARRATION_SUBTITLE_TRACK, acd.COLOR_GRADE_TRACK,
        # One text track per title line, created only for the lines that exist.
        *(acd.title_track_name(index) for index in range(2)),
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
    monkeypatch.setenv("IMAGE_STYLE_PRESET", "manhua")
    assert acd.Config.load().image_style_prompt == acd.STYLE_PRESETS["manhua"].prompt
    monkeypatch.setenv("IMAGE_STYLE_PROMPT", "my own style")
    assert acd.Config.load().image_style_prompt == "my own style"


def test_the_style_supplies_the_grade_and_the_title_colourway(monkeypatch, workspace):
    """Picking a look has to move the filter and the title with it."""
    monkeypatch.setenv("IMAGE_STYLE_PRESET", "noir")
    cfg = acd.Config.load()
    assert (cfg.color_grade, cfg.title_style) == (acd.STYLE_PRESETS["noir"].grade,
                                                  acd.STYLE_PRESETS["noir"].title)


def test_env_still_wins_over_the_style(monkeypatch, workspace):
    monkeypatch.setenv("IMAGE_STYLE_PRESET", "noir")
    monkeypatch.setenv("COLOR_GRADE", "自然")
    monkeypatch.setenv("TITLE_STYLE", "gold")
    cfg = acd.Config.load()
    assert (cfg.color_grade, cfg.title_style) == ("自然", "gold")


def test_unknown_style_preset_is_rejected(monkeypatch, workspace):
    monkeypatch.setenv("IMAGE_STYLE_PRESET", "nonexistent")
    with pytest.raises(RuntimeError, match="IMAGE_STYLE_PRESET"):
        acd.Config.load()


# --------------------------------------------------------- the spoken title --
# The title was drawn on screen and never read. That only shows when --title
# says something the copy does not, which is the documented usage - so the
# fixture below gives the title its own clip, exactly as a real run now does.

# Longer than TITLE_SECONDS' 2.4s default once the 0.45s lead is added, so the
# overlay genuinely has to be held. At 1.6s the voice finished inside the
# default hold and the test passed whether the hold was extended or not - which
# is a test that reports on nothing.
TITLE_SECONDS = 3.0


@pytest.fixture
def spoken(workspace):
    assets, _ = workspace
    cfg = acd.Config.load()
    voice = _wav(assets / "title.wav", TITLE_SECONDS)
    path = acd.build_draft(cfg, _scenes(assets), "pytest_spoken", replace=False,
                           title="男人不能为女人做的3件事", title_audio=voice,
                           opening_sound=acd.resolve_opening_sound(cfg, assets))
    content = json.loads((path / "draft_content.json").read_text(encoding="utf-8"))
    return cfg, content, {track["name"]: track for track in content["tracks"]}


def test_the_lead_is_a_floor_not_the_answer():
    """0.8s fits the stinger and about half a spoken title."""
    lead, tail = round(0.45 * SECOND), acd.TITLE_TAIL_US
    configured = round(0.8 * SECOND)
    # A title short enough to fit keeps the configured lead...
    assert acd.opening_lead(configured, round(0.1 * SECOND), lead, tail) == configured
    # ...and one that does not, extends it by exactly what it needs.
    spoken_us = round(1.6 * SECOND)
    assert acd.opening_lead(configured, spoken_us, lead, tail) == lead + spoken_us + tail
    # No title audio at all leaves the timeline exactly as it was.
    assert acd.opening_lead(configured, 0, lead, tail) == configured


def test_the_title_is_spoken_before_the_copy(spoken):
    """The whole point: the copy must not talk over the title's own voice."""
    cfg, _, tracks = spoken
    voice = _sorted_segments(tracks["voiceover"])
    title, first_line = voice[0], voice[1]
    assert title["target_timerange"]["start"] == cfg.title_lead_us
    title_end = title["target_timerange"]["start"] + title["target_timerange"]["duration"]
    assert first_line["target_timerange"]["start"] >= title_end, (
        "the first line of the copy starts before the title has finished")


def test_the_title_waits_for_the_stinger(spoken):
    """It speaks into the cue's decay, not over its impact."""
    cfg, _, tracks = spoken
    assert cfg.title_lead_us > 0
    title = _sorted_segments(tracks["voiceover"])[0]
    assert title["target_timerange"]["start"] == cfg.title_lead_us


def test_the_title_stays_on_screen_while_it_is_read(spoken):
    """A title that vanishes mid-sentence reads as a timing bug."""
    _, _, tracks = spoken
    overlay = _sorted_segments(tracks[acd.title_track_name(0)])[0]
    voice = _sorted_segments(tracks["voiceover"])[0]
    voice_end = voice["target_timerange"]["start"] + voice["target_timerange"]["duration"]
    overlay_end = overlay["target_timerange"]["start"] + overlay["target_timerange"]["duration"]
    assert overlay_end >= voice_end


def test_the_bed_ducks_under_the_title_too(spoken):
    """The title is speech; the music has to get out of its way like any other."""
    cfg, _, tracks = spoken
    keyframes = tracks["BGM"]["segments"][0]["common_keyframes"]
    points = [(k["time_offset"], k["values"][0])
              for group in keyframes for k in group["keyframe_list"]]
    assert points, "the bed has no volume automation at all"
    middle = cfg.title_lead_us + round(0.8 * SECOND)
    at_title = min(points, key=lambda point: abs(point[0] - middle))[1]
    assert at_title <= cfg.bgm_volume + 1e-6, (
        f"bed sits at {at_title} during the title, above the ducked {cfg.bgm_volume}")


def test_an_unspoken_title_leaves_the_timeline_alone(draft, spoken):
    """Without title audio nothing moves - the old behaviour is intact."""
    cfg, _, plain_tracks = draft
    _, _, spoken_tracks = spoken
    plain_first = _sorted_segments(plain_tracks[acd.NARRATION_SUBTITLE_TRACK])[0]
    spoken_first = _sorted_segments(spoken_tracks[acd.NARRATION_SUBTITLE_TRACK])[0]
    assert plain_first["target_timerange"]["start"] == cfg.opening_lead_us
    assert spoken_first["target_timerange"]["start"] > cfg.opening_lead_us


# --------------------------------------------- a clone with no audio at all --

@pytest.fixture
def bare(workspace, monkeypatch, tmp_path):
    """No opening sound configured anywhere - the fresh-clone case."""
    assets, _ = workspace
    monkeypatch.delenv("OPENING_SOUND_PATH", raising=False)
    # The DEFAULT is pointed at nothing, not just the env var unset. A developer
    # who keeps their own assets/opening_dong.mp3 would otherwise resolve it and
    # this fixture would quietly test the opposite of what it claims - passing
    # on a fresh clone, failing on the machine of whoever wrote it.
    monkeypatch.setattr(acd, "DEFAULT_OPENING_SOUND_PATH", "assets/__absent__.mp3")
    cfg = acd.Config.load()
    assert cfg.opening_sound_path is None, "the fixture is not testing what it claims"
    cue = acd.resolve_opening_sound(cfg, tmp_path / "run")
    path = acd.build_draft(cfg, _scenes(assets), "pytest_bare", replace=False,
                           title="男人不能为女人做的3件事", opening_sound=cue)
    content = json.loads((path / "draft_content.json").read_text(encoding="utf-8"))
    return cfg, content, {track["name"]: track for track in content["tracks"]}


def test_a_clone_with_no_sound_effect_still_builds(bare):
    """The one asset the repo may not ship was the one it could not start without."""
    _, _, tracks = bare
    segments = tracks["opening_sfx"]["segments"]
    assert len(segments) == 1
    assert segments[0]["target_timerange"]["start"] == 0
    assert segments[0]["target_timerange"]["duration"] > 500_000


def test_the_generated_cue_is_written_outside_the_repo(tmp_path, monkeypatch):
    """It goes in the run's own folder, never back into the user's assets/."""
    monkeypatch.delenv("OPENING_SOUND_PATH", raising=False)
    monkeypatch.setattr(acd, "DEFAULT_OPENING_SOUND_PATH", "assets/__absent__.mp3")
    monkeypatch.setenv("ARK_API_KEY", "k")
    monkeypatch.setenv("ARK_TTS_VOICE_TYPE", "v")
    monkeypatch.setenv("JIAN_YING_DRAFT_DIR", str(tmp_path))
    cfg = acd.Config.load()
    run = tmp_path / "output" / "a_run"
    cue = acd.resolve_opening_sound(cfg, run)
    assert cue.parent == run
    assert acd.ROOT not in cue.parents, "the generated cue must not land in the repo"


def test_the_draft_knows_its_own_name_and_place(draft, workspace):
    """pyJianYingDraft copies its meta template verbatim, leaving these empty."""
    _, drafts = workspace
    meta = json.loads((drafts / "pytest_draft" / "draft_meta_info.json")
                      .read_text(encoding="utf-8-sig"))
    assert meta["draft_name"] == "pytest_draft"
    assert Path(meta["draft_fold_path"]) == drafts / "pytest_draft"
    assert Path(meta["draft_root_path"]) == drafts

"""Unit tests for the parts of the pipeline that never touch a paid API."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import animated_caption_draft as acd  # noqa: E402

SECOND = 1_000_000


# ------------------------------------------------------------------ layout --

def test_default_subtitle_y_is_inside_the_frame():
    """-700 must land in the lower third, not below the bottom edge.

    The bug this guards: dividing by half the canvas height (540) mapped the
    default to -1.296, which is off-screen.
    """
    y = acd.layout_y(acd.DEFAULT_NARRATION_SUBTITLE_Y)
    assert y == pytest.approx(-0.7292, abs=1e-4)
    assert -1.0 < y < 0.0
    pixels_from_bottom = acd.CANVAS_HALF_HEIGHT + y * acd.CANVAS_HALF_HEIGHT
    assert 100 < pixels_from_bottom < 250


def test_layout_y_clamps_to_the_safe_area():
    assert acd.layout_y(-100_000) == -acd.SAFE_NORMALIZED_Y
    assert acd.layout_y(100_000) == acd.SAFE_NORMALIZED_Y


def test_title_sits_in_the_upper_half():
    assert acd.layout_y(acd.DEFAULT_TITLE_Y) > 0


# --------------------------------------------------------- caption wrapping --

def test_short_caption_is_one_line():
    assert acd.subtitle_line_count("短句", 8.0, 0.88, acd.DEFAULT_SUBTITLE_EM_PX) == 1


def test_a_typical_scene_still_fits_one_line():
    """SCENE_CHARACTERS_PER_IMAGE defaults to 22, so that length must not wrap."""
    text = "字" * 21
    assert acd.subtitle_line_count(text, 8.0, 0.88, acd.DEFAULT_SUBTITLE_EM_PX) == 1


def test_a_long_caption_wraps():
    assert acd.subtitle_line_count("字" * 45, 8.0, 0.88, acd.DEFAULT_SUBTITLE_EM_PX) >= 2


def test_a_bigger_font_wraps_sooner():
    text = "字" * 21
    big = acd.subtitle_line_count(text, 14.0, 0.88, acd.DEFAULT_SUBTITLE_EM_PX)
    small = acd.subtitle_line_count(text, 8.0, 0.88, acd.DEFAULT_SUBTITLE_EM_PX)
    assert big > small


def test_one_line_captions_keep_the_configured_position():
    base = acd.layout_y(acd.DEFAULT_NARRATION_SUBTITLE_Y)
    assert acd.subtitle_baseline_y(base, 1, 8.0, acd.DEFAULT_SUBTITLE_EM_PX) == base


def test_wrapped_captions_are_raised_so_the_bottom_line_holds():
    """The block is centre-anchored, so extra lines must push it up, not down."""
    base = acd.layout_y(acd.DEFAULT_NARRATION_SUBTITLE_Y)
    two = acd.subtitle_baseline_y(base, 2, 8.0, acd.DEFAULT_SUBTITLE_EM_PX)
    three = acd.subtitle_baseline_y(base, 3, 8.0, acd.DEFAULT_SUBTITLE_EM_PX)
    assert two > base and three > two
    # Each extra line raises the centre by exactly half a line.
    assert (three - two) == pytest.approx(two - base)


def test_the_compensated_position_stays_on_screen():
    base = acd.layout_y(acd.DEFAULT_NARRATION_SUBTITLE_Y)
    for lines in range(1, 8):
        y = acd.subtitle_baseline_y(base, lines, 14.0, acd.DEFAULT_SUBTITLE_EM_PX)
        assert -1.0 < y < 1.0


def test_empty_caption_does_not_divide_by_zero():
    assert acd.subtitle_line_count("   ", 8.0, 0.88, acd.DEFAULT_SUBTITLE_EM_PX) == 1


# ------------------------------------------------------------------- shots --

DURATIONS = (1.6, 2.5, 3.5, 5.4, 6.2, 12.0)


def _travel(duration_s, move):
    s0, s1, x0, x1, y0, y1 = acd.ken_burns_keyframes(round(duration_s * SECOND), move)
    return abs(s1 - s0), abs(x1 - x0), abs(y1 - y0)


def test_camera_speed_is_the_same_on_short_and_long_shots():
    """The bug this guards: fixed endpoints made a 1.6s shot zoom fast and a
    6.2s shot barely move at all."""
    push = (+1, 0, 0)
    for duration in (1.6, 2.5, 3.5, 5.4):  # all below the travel ceiling
        zoom, _, _ = _travel(duration, push)
        assert zoom / duration == pytest.approx(acd.DEFAULT_KEN_BURNS_RATE, rel=1e-6)


def test_travel_is_capped_so_long_shots_do_not_zoom_through_the_frame():
    zoom, _, _ = _travel(60.0, (+1, 0, 0))
    assert zoom == pytest.approx(acd.KEN_BURNS_MAX_TRAVEL)


def test_upscale_stays_within_the_source_resolution():
    """2560-wide source into a 1920 canvas tolerates about 1.33x before it softens."""
    for duration in DURATIONS:
        for move in acd.KEN_BURNS_MOVES:
            s0, s1, *_ = acd.ken_burns_keyframes(round(duration * SECOND), move)
            assert max(s0, s1) <= 1.33


def test_a_pan_never_exposes_the_edge_of_the_image():
    """At scale S the image overhangs the canvas by (S - 1) half-canvas units."""
    for duration in DURATIONS:
        for move in acd.KEN_BURNS_MOVES:
            s0, s1, x0, x1, y0, y1 = acd.ken_burns_keyframes(round(duration * SECOND), move)
            headroom = min(s0, s1) - 1.0
            assert max(abs(x0), abs(x1), abs(y0), abs(y1)) <= headroom


def test_every_move_actually_moves():
    for move in acd.KEN_BURNS_MOVES:
        zoom, pan_x, pan_y = _travel(3.5, move)
        assert zoom + pan_x + pan_y > 0.01, "a static move is just a frozen still"


def test_push_and_pull_are_mirror_images():
    push = acd.ken_burns_keyframes(3_500_000, (+1, 0, 0))
    pull = acd.ken_burns_keyframes(3_500_000, (-1, 0, 0))
    assert (push[0], push[1]) == (pull[1], pull[0])


def test_zero_rate_holds_the_frame_still():
    s0, s1, x0, x1, y0, y1 = acd.ken_burns_keyframes(3_500_000, (+1, -1, 0), rate=0.0)
    assert (s0, s1, x0, x1, y0, y1) == (acd.KEN_BURNS_BASE_SCALE, acd.KEN_BURNS_BASE_SCALE, 0, 0, 0, 0)


# ---------------------------------------------------------------- timeline --

def test_scenes_are_contiguous_and_cover_the_whole_video():
    durations = [2 * SECOND, 3 * SECOND, 4 * SECOND]
    timings, total = acd.plan_timeline(durations, [False, True, False],
                                       lead_us=800_000, pause_us=500_000, hold_us=1_800_000)
    assert timings[0].visual_start == 0, "the first picture must cover the opening lead"
    previous_end = 0
    for timing in timings:
        assert timing.visual_start == previous_end, "a gap here would show as black"
        previous_end = timing.visual_start + timing.visual_duration
    assert previous_end == total


def test_narration_is_never_overlapped_and_keeps_its_own_length():
    durations = [2 * SECOND, 3 * SECOND, 4 * SECOND]
    timings, _ = acd.plan_timeline(durations, [False, True, False],
                                   lead_us=800_000, pause_us=500_000, hold_us=1_800_000)
    assert [t.narration_duration for t in timings] == durations
    for earlier, later in zip(timings, timings[1:], strict=False):
        assert later.narration_start >= earlier.narration_end


def test_a_paragraph_pause_pushes_the_next_line_back():
    durations = [2 * SECOND, 2 * SECOND]
    with_pause, _ = acd.plan_timeline(durations, [True, False], 0, 500_000, 0)
    without, _ = acd.plan_timeline(durations, [False, False], 0, 500_000, 0)
    assert with_pause[1].narration_start - without[1].narration_start == 500_000


def test_the_last_scene_holds_after_its_narration_ends():
    timings, total = acd.plan_timeline([2 * SECOND], [False], 0, 500_000, 1_800_000)
    assert timings[0].visual_duration == 2 * SECOND + 1_800_000
    assert total - timings[0].narration_end == 1_800_000


def test_a_pause_on_the_last_scene_is_ignored():
    """It would only add dead air before the ending hold."""
    _, total = acd.plan_timeline([2 * SECOND], [True], 0, 500_000, 0)
    assert total == 2 * SECOND


def test_timeline_without_pauses_or_hold_is_just_the_narration():
    durations = [2 * SECOND, 3 * SECOND]
    timings, total = acd.plan_timeline(durations, [False, False], 0, 0, 0)
    assert total == sum(durations)
    assert [t.narration_start for t in timings] == [0, 2 * SECOND]


# ---------------------------------------------------------------- ducking --

RAMP = 250_000


def _levels(points):
    return [volume for _, volume in points]


def test_bgm_is_quiet_under_speech_and_lifted_in_the_gaps():
    speech = [(SECOND, 3 * SECOND), (4 * SECOND, 6 * SECOND)]   # a 1s gap between
    points = acd.bgm_volume_envelope(speech, 8 * SECOND, 0.10, 0.20, RAMP)
    assert acd.sample_envelope(points, 2 * SECOND) == pytest.approx(0.10)
    assert acd.sample_envelope(points, 5 * SECOND) == pytest.approx(0.10)
    assert acd.sample_envelope(points, 3 * SECOND + RAMP) == pytest.approx(0.20), "the gap must lift"
    assert acd.sample_envelope(points, 7 * SECOND) == pytest.approx(0.20), "the ending hold must lift"


def test_no_lift_when_there_is_no_room_for_the_ramp():
    """Back-to-back lines must not make the music pump between every sentence."""
    speech = [(0, 2 * SECOND), (2 * SECOND, 4 * SECOND), (4 * SECOND, 6 * SECOND)]
    points = acd.bgm_volume_envelope(speech, 6 * SECOND, 0.10, 0.20, RAMP)
    assert max(_levels(points)) == pytest.approx(0.10)


def test_envelope_points_are_ordered_and_in_range():
    speech = [(800_000, 3 * SECOND), (3 * SECOND, 5 * SECOND), (6 * SECOND, 9 * SECOND)]
    points = acd.bgm_volume_envelope(speech, 11 * SECOND, 0.10, 0.20, RAMP)
    times = [moment for moment, _ in points]
    assert times == sorted(times)
    assert len(times) == len(set(times))
    assert 0 <= min(times) and max(times) <= 11 * SECOND
    assert all(0.10 <= volume <= 0.20 for volume in _levels(points))


def test_envelope_covers_the_whole_video():
    points = acd.bgm_volume_envelope([(SECOND, 3 * SECOND)], 5 * SECOND, 0.10, 0.20, RAMP)
    assert points[0][0] == 0
    assert points[-1][0] == 5 * SECOND


def test_envelope_without_speech_is_flat():
    points = acd.bgm_volume_envelope([], 5 * SECOND, 0.10, 0.20, RAMP)
    assert _levels(points) == [0.20, 0.20]


def test_sample_interpolates_between_points():
    points = [(0, 0.10), (SECOND, 0.20)]
    assert acd.sample_envelope(points, SECOND // 2) == pytest.approx(0.15)
    assert acd.sample_envelope(points, -SECOND) == pytest.approx(0.10)
    assert acd.sample_envelope(points, 9 * SECOND) == pytest.approx(0.20)


# ------------------------------------------------------------- shot sizes --

def test_every_shot_size_injects_distinct_framing():
    prompts = {
        size: acd.compose_image_prompt(_StubConfig(), _scene(shot_size=size), [])
        for size in acd.SHOT_SIZES
    }
    assert len(set(prompts.values())) == len(acd.SHOT_SIZES)
    assert "Close-up" in prompts["close"]
    assert "Wide establishing" in prompts["wide"]


def test_unknown_shot_size_falls_back_to_medium():
    odd = acd.compose_image_prompt(_StubConfig(), _scene(shot_size="extreme-close"), [])
    assert odd == acd.compose_image_prompt(_StubConfig(), _scene(shot_size="medium"), [])


def test_framing_is_not_baked_into_the_art_direction():
    """Thirty identical medium shots was the bug; framing belongs per scene."""
    for prompt in acd.STYLE_PRESETS.values():
        assert "two-shot framing" not in prompt


def test_shot_size_and_pause_are_read_from_the_storyboard():
    scenes = acd.scenes_from_payload({"scenes": [
        {"text": "一句话", "image_prompt": "x", "shot_size": "CLOSE", "pause_after": True},
        {"text": "另一句", "image_prompt": "x", "shot_size": "nonsense"},
        {"text": "第三句", "image_prompt": "x"},
    ]})
    assert [s.shot_size for s in scenes] == ["close", "medium", "medium"]
    assert [s.pause_after for s in scenes] == [True, False, False]


# --------------------------------------------------------------------- bgm --

def test_bgm_shorter_than_the_video_loops_and_fades_at_both_ends():
    plan = acd.bgm_loop_plan(10 * SECOND, 4 * SECOND)
    assert [start for start, *_ in plan] == [0, 4 * SECOND, 8 * SECOND]
    assert sum(duration for _, duration, _, _ in plan) == 10 * SECOND
    assert plan[0][2] > 0, "the first loop must fade in"
    assert plan[-1][3] > 0, "the last loop must fade out"


def test_bgm_longer_than_the_video_still_fades():
    """The old code skipped fades entirely when the track needed no looping."""
    plan = acd.bgm_loop_plan(10 * SECOND, 60 * SECOND)
    assert len(plan) == 1
    start, duration, fade_in, fade_out = plan[0]
    assert (start, duration) == (0, 10 * SECOND)
    assert fade_in > 0 and fade_out > 0


def test_bgm_fades_never_exceed_half_the_segment():
    for total, material in ((300_000, 200_000), (10 * SECOND, 4 * SECOND), (SECOND, SECOND)):
        for _, duration, fade_in, fade_out in acd.bgm_loop_plan(total, material):
            assert fade_in <= duration // 2
            assert fade_out <= duration // 2


def test_bgm_plan_is_empty_without_material():
    assert acd.bgm_loop_plan(10 * SECOND, 0) == []
    assert acd.bgm_loop_plan(0, 10 * SECOND) == []


# -------------------------------------------------------------- storyboard --

def test_batches_split_on_sentence_boundaries():
    copy = "。".join(f"第{index}句话内容比较长需要凑够字数才会触发切分" for index in range(1, 40))
    batches = acd.storyboard_batches(copy, batch_size=100)
    assert len(batches) > 1
    assert "".join(batches).replace("\n", "") == copy.replace("\n", "")


def test_short_copy_is_a_single_batch():
    assert acd.storyboard_batches("只有一句话。") == ["只有一句话。"]


def test_scene_limits_scale_with_copy_length():
    target, maximum = acd.scene_limits("density", 22, "字" * 220)
    assert target == 10
    assert maximum >= target


def test_faster_cutting_is_now_configurable():
    """SCENE_CHARACTERS_PER_IMAGE used to be locked at a minimum of 20."""
    slow, _ = acd.scene_limits("density", 22, "字" * 220)
    fast, _ = acd.scene_limits("density", 11, "字" * 220)
    assert fast > slow


def test_scene_length_bounds_respect_the_floor():
    minimum, maximum = acd.scene_text_length_bounds("density", acd.MIN_SCENE_CHARACTERS)
    assert minimum >= acd.MIN_SCENE_CHARACTERS
    assert maximum > minimum


# --------------------------------------------------- storyboard validation --

def test_fenced_json_is_accepted():
    data = acd.parse_storyboard_payload('```json\n{"scenes": []}\n```')
    assert data == {"scenes": []}


def test_malformed_scene_items_are_skipped_not_crashed():
    """A non-string field used to raise AttributeError deep in a list comprehension."""
    scenes = acd.scenes_from_payload({"scenes": [
        {"text": 42, "image_prompt": "ok"},
        {"text": "ok", "image_prompt": None},
        "not a dict",
        {"text": "  ", "image_prompt": "ok"},
        {"text": "真正的一句话", "image_prompt": "a person at a desk"},
    ]})
    assert len(scenes) == 1
    assert scenes[0].text == "真正的一句话"


def test_cast_is_normalised_to_a_list_of_strings():
    scenes = acd.scenes_from_payload({"scenes": [
        {"text": "一句话", "image_prompt": "x", "cast": ["A", " ", "B"]},
        {"text": "另一句", "image_prompt": "x", "cast": "A"},
    ]})
    assert scenes[0].cast == ["A", "B"]
    assert scenes[1].cast == []


def test_characters_are_parsed_and_bad_entries_dropped():
    characters = acd.characters_from_payload({"characters": [
        {"id": "A", "desc": "a 30-year-old man"},
        {"id": "", "desc": "nameless"},
        {"id": "B"},
        "junk",
    ]})
    assert [c.id for c in characters] == ["A"]


# --------------------------------------------------------------- env parse --

@pytest.mark.parametrize("raw, expected", [
    ("plain value", "plain value"),
    ('"quoted value"', "quoted value"),
    ("'single quoted'", "single quoted"),
    ("value  # trailing comment", "value"),
    ('"value # inside quotes"', "value # inside quotes"),
    ("a,b,c, 16:9", "a,b,c, 16:9"),
    ("", ""),
])
def test_env_values_parse_as_expected(raw, expected):
    assert acd._parse_env_value(raw) == expected


@pytest.mark.parametrize("name", sorted(acd.STYLE_PRESETS))
def test_style_prompts_survive_parsing(name):
    """Style prompts are long and comma-heavy; they must come back whole."""
    prompt = acd.STYLE_PRESETS[name]
    assert acd._parse_env_value(prompt) == prompt


def test_default_style_preset_exists():
    assert acd.DEFAULT_STYLE_PRESET in acd.STYLE_PRESETS


# ------------------------------------------------------------------ prompt --

def _scene(**overrides):
    base = {"text": "一句话", "image_prompt": "a man tidying a desk"}
    base.update(overrides)
    return acd.Scene(**base)


class _StubConfig:
    image_style_prompt = "test style"


def test_cast_description_is_injected_verbatim():
    """Character consistency depends on the exact same words every time."""
    characters = [acd.Character("A", "32-year-old man, navy sweater")]
    prompt = acd.compose_image_prompt(_StubConfig(), _scene(cast=["A"]), characters)
    assert "32-year-old man, navy sweater" in prompt
    assert "identical face, hair, build and clothing" in prompt


def test_prompt_without_cast_has_no_cast_block():
    prompt = acd.compose_image_prompt(_StubConfig(), _scene(), [])
    assert "Recurring cast" not in prompt


def test_unknown_cast_ids_are_ignored():
    prompt = acd.compose_image_prompt(_StubConfig(), _scene(cast=["Z"]), [acd.Character("A", "x")])
    assert "Recurring cast" not in prompt

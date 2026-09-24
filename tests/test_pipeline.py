"""Unit tests for the parts of the pipeline that never touch a paid API."""

from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict
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


def test_title_block_is_centred_like_the_reference():
    """The reference centres its title on the frame; it is the frame, briefly."""
    assert acd.layout_y(acd.DEFAULT_TITLE_Y) == 0.0


def test_default_title_and_caption_sizes_match_the_measured_reference():
    """Both are derived from pixel measurements, so guard the arithmetic.

    Reference frame: caption em ~66 px, title em ~191 px on a 1080-tall frame.
    """
    assert round(acd.DEFAULT_SUBTITLE_SIZE * acd.DEFAULT_SUBTITLE_EM_PX) == 67
    assert round(acd.DEFAULT_TITLE_SIZE * acd.DEFAULT_SUBTITLE_EM_PX) == 191


# ----------------------------------------------------------- title breaking --

def _title_limit(size=None):
    return acd.characters_per_line(
        size or acd.DEFAULT_TITLE_SIZE, acd.DEFAULT_TITLE_MAX_LINE_WIDTH, acd.DEFAULT_SUBTITLE_EM_PX
    )


def test_the_reference_title_breaks_where_a_person_broke_it():
    """The actual title from the reference video, and its actual break."""
    assert acd.split_title_lines("男人不能为女人做的3件事", _title_limit()) == ["男人不能为女人", "做的3件事"]


def test_short_titles_stay_on_one_line():
    assert acd.split_title_lines("认知觉醒", _title_limit()) == ["认知觉醒"]


def test_a_line_never_ends_on_a_particle_that_binds_forward():
    """A width-only break lands on 绝 / 对 here; both leave a word hanging."""
    lines = acd.split_title_lines("男人这一生绝对不能为女人做这三件事", _title_limit())
    assert lines == ["男人这一生", "绝对不能为女人", "做这三件事"]
    for line in lines[:-1]:
        assert line[-1] not in acd.TITLE_NEVER_ENDS_A_LINE
    for line in lines[1:]:
        assert line[0] not in acd.TITLE_NEVER_STARTS_A_LINE


def test_punctuation_is_a_break_point_and_is_dropped():
    lines = acd.split_title_lines("情绪稳定，是一个成年人最大的底气", _title_limit())
    assert lines[0] == "情绪稳定"
    assert not any("，" in line for line in lines)


def test_a_run_of_digits_is_never_split():
    assert acd.split_title_lines("30岁之后别再做这三件蠢事", _title_limit())[0].startswith("30")


def test_an_explicit_newline_is_obeyed_verbatim():
    assert acd.split_title_lines("第一行\n第二行", _title_limit()) == ["第一行", "第二行"]


def test_a_title_never_exceeds_the_line_limit():
    limit = _title_limit()
    for length in range(1, 60):
        for line in acd.split_title_lines("字" * length, limit)[:-1]:
            assert len(line) <= limit


def test_a_title_too_long_for_three_lines_shrinks_instead_of_wrapping():
    """Auto-wrap is off, so an overlong line has to be handled by the size."""
    lines = acd.split_title_lines("字" * 40, _title_limit())
    assert len(lines) == acd.MAX_TITLE_LINES
    fitted = acd.fit_title_size(
        lines, acd.DEFAULT_TITLE_SIZE, acd.DEFAULT_TITLE_MAX_LINE_WIDTH, acd.DEFAULT_SUBTITLE_EM_PX
    )
    assert fitted < acd.DEFAULT_TITLE_SIZE
    widest = max(len(line) for line in lines)
    assert widest * fitted * acd.DEFAULT_SUBTITLE_EM_PX <= acd.DEFAULT_TITLE_MAX_LINE_WIDTH * acd.CANVAS_WIDTH + 1e-6


def test_a_title_that_already_fits_is_never_enlarged():
    size = acd.fit_title_size(
        ["四个字"], acd.DEFAULT_TITLE_SIZE, acd.DEFAULT_TITLE_MAX_LINE_WIDTH, acd.DEFAULT_SUBTITLE_EM_PX
    )
    assert size == acd.DEFAULT_TITLE_SIZE


def test_title_lines_are_centred_on_the_block_at_the_reference_pitch():
    offsets = acd.title_line_offsets(2, 0.0, acd.DEFAULT_TITLE_SIZE, acd.DEFAULT_SUBTITLE_EM_PX)
    assert offsets[0] > 0 > offsets[1]
    assert offsets[0] == pytest.approx(-offsets[1])
    pitch_px = (offsets[0] - offsets[1]) * acd.CANVAS_HALF_HEIGHT
    assert pitch_px == pytest.approx(182, abs=2), "reference line pitch is 182 px"


def test_a_single_title_line_sits_exactly_on_the_block_centre():
    assert acd.title_line_offsets(1, 0.25, acd.DEFAULT_TITLE_SIZE, acd.DEFAULT_SUBTITLE_EM_PX) == [0.25]


@pytest.mark.parametrize("base", [0.9, -0.9, 0.0])
@pytest.mark.parametrize("count", [1, 2, 3])
def test_a_title_block_pushed_off_frame_moves_whole_and_keeps_its_pitch(count, base):
    """Clamping line by line used to pile the outer lines against the edge."""
    offsets = acd.title_line_offsets(count, base, acd.DEFAULT_TITLE_SIZE, acd.DEFAULT_SUBTITLE_EM_PX)
    assert all(abs(y) <= acd.SAFE_NORMALIZED_Y for y in offsets)
    assert len(set(offsets)) == count, "two title lines landed on the same height"
    gaps = {round(a - b, 9) for a, b in zip(offsets, offsets[1:], strict=False)}
    assert len(gaps) <= 1, "the line pitch changed inside one title"


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
    for preset in acd.STYLE_PRESETS.values():
        assert "two-shot framing" not in preset.prompt


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
    prompt = acd.STYLE_PRESETS[name].prompt
    assert acd._parse_env_value(prompt) == prompt


def test_default_style_preset_exists():
    assert acd.DEFAULT_STYLE_PRESET in acd.STYLE_PRESETS


# ------------------------------------------------------------------ styles --

def test_there_are_nine_styles_and_they_are_distinct():
    assert len(acd.STYLE_PRESETS) == 9
    assert len({preset.prompt for preset in acd.STYLE_PRESETS.values()}) == 9
    assert len({preset.medium for preset in acd.STYLE_PRESETS.values()}) == 9


@pytest.mark.parametrize("name", sorted(acd.STYLE_PRESETS))
def test_every_style_carries_the_things_that_must_match_it(name):
    """A style is not just a prompt: the director, grade and title move with it."""
    preset = acd.STYLE_PRESETS[name]
    assert preset.prompt.endswith("16:9")
    assert preset.medium and preset.avoid and preset.label
    assert preset.title in acd.TITLE_PRESETS
    assert preset.grade and preset.grade.lower() not in {"none", "off"}


def test_the_default_style_is_the_reference_look():
    preset = acd.STYLE_PRESETS[acd.DEFAULT_STYLE_PRESET]
    assert acd.DEFAULT_STYLE_PRESET == "midnight"
    assert "midnight-blue" in preset.medium
    assert preset.title == acd.DEFAULT_TITLE_STYLE


def test_a_bare_prompt_string_keeps_the_rest_of_the_built_in():
    """Old styles.json files hold plain strings; they must not lose the grade."""
    parsed = acd.StylePreset.parse("midnight", "my own words", "test")
    built_in = acd.STYLE_PRESETS["midnight"]
    assert parsed.prompt == "my own words"
    assert (parsed.medium, parsed.grade, parsed.title) == (built_in.medium, built_in.grade, built_in.title)


def test_an_unknown_style_from_a_string_still_gets_usable_defaults():
    parsed = acd.StylePreset.parse("brand_new", "some prompt", "test")
    assert parsed.prompt == "some prompt"
    assert parsed.medium == acd.DEFAULT_STYLE_MEDIUM
    assert parsed.title in acd.TITLE_PRESETS


def test_an_object_style_overrides_only_what_it_names():
    parsed = acd.StylePreset.parse("noir", {"prompt": "p", "grade": "自然"}, "test")
    assert (parsed.grade, parsed.medium) == ("自然", acd.STYLE_PRESETS["noir"].medium)


@pytest.mark.parametrize("value", ["", "   ", 42, None, {"medium": "x"}])
def test_a_style_without_a_prompt_is_an_error(value):
    with pytest.raises(RuntimeError):
        acd.StylePreset.parse("x", value, "test")


def test_the_generated_styles_file_round_trips():
    document = acd.builtin_styles_document()
    assert document["default"] == acd.DEFAULT_STYLE_PRESET
    for name, payload in document["presets"].items():
        assert acd.StylePreset.parse(name, payload, "test") == acd.STYLE_PRESETS[name]


# ------------------------------------------------------------------ prompt --

def _scene(**overrides):
    base = {"text": "一句话", "image_prompt": "a man tidying a desk"}
    base.update(overrides)
    return acd.Scene(**base)


class _StubConfig:
    image_style_prompt = "test style"
    style = acd.STYLE_PRESETS[acd.DEFAULT_STYLE_PRESET]


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


def test_the_image_prompt_names_the_selected_medium():
    prompt = acd.compose_image_prompt(_StubConfig(), _scene(), [])
    assert acd.STYLE_PRESETS[acd.DEFAULT_STYLE_PRESET].medium in prompt
    assert "manhua panel" not in prompt


@pytest.mark.parametrize("name", sorted(acd.STYLE_PRESETS))
def test_the_storyboard_director_is_told_the_selected_medium(name):
    """A photographic style used to be handed a brief for flat manhua panels."""
    preset = acd.STYLE_PRESETS[name]

    class _Cfg(_StubConfig):
        style = preset
        scene_length_mode = "density"
        scene_characters = 22

    prompt = acd.storyboard_prompt(_Cfg(), 1, 1, 8, 10, [])
    assert preset.medium in prompt
    assert preset.avoid in prompt
    assert "social-realism manhua" not in prompt


# ------------------------------------------- the title that says itself twice --

def _title_scene(text: str) -> acd.Scene:
    return acd.Scene(text=text, image_prompt="a desk", shot_size="wide", cast=["A"])


def test_a_default_title_is_not_spoken_twice():
    """--title defaults to the copy's first line, which scene 1 also narrates.

    Speaking both says the same sentence twice in a row. This is the common
    case - every run that does not pass --title.
    """
    copy_opens = "男人不能为女人做的3件事，第一件是替她做决定。"
    assert acd.title_already_narrated("男人不能为女人做的3件事", [_title_scene(copy_opens)])


def test_a_real_title_is_still_spoken():
    """A --title the copy never says is exactly what needed a voice."""
    assert not acd.title_already_narrated(
        "省钱的三个误区", [_title_scene("很多人以为记账就能存下钱。")])


def test_the_comparison_ignores_reflowed_whitespace():
    """The splitter may reflow spacing; a stutter is still a stutter."""
    assert acd.title_already_narrated(
        "男人 不能　为女人做的3件事", [_title_scene("男人不能为女人做的3件事，第一件……")])


def test_no_scenes_means_nothing_to_collide_with():
    assert not acd.title_already_narrated("任何标题", [])


def test_a_title_too_long_to_read_loses_its_voice_not_the_pacing():
    """The lead grows to fit the title, and that growth needs a ceiling.

    A forty-character --title reads for eight seconds, which opened the video
    on nearly nine seconds of title card before the copy started. This is
    short-form video. The type stays; the voice-over goes.
    """
    lead = round(0.45 * 1_000_000)
    configured = round(0.8 * 1_000_000)

    assert acd.title_voice_fits(round(2.4 * 1_000_000), lead)
    assert not acd.title_voice_fits(round(8.0 * 1_000_000), lead)

    # Callers drop the voice, so the head returns to its configured length...
    assert acd.opening_lead(configured, 0, lead) == configured
    # ...and even if one did not, the arithmetic cannot exceed the cap.
    assert acd.opening_lead(configured, round(30.0 * 1_000_000), lead) \
        <= acd.MAX_OPENING_LEAD_US


# ------------------------------------ a title in the wrong language ----------

def test_a_title_in_another_language_is_flagged():
    """The director translates; a --title passed alongside it does not.

    English copy comes back as Chinese scenes, so an English --title ends up
    read aloud by the Chinese narration voice. The draft is not wrong, it
    just sounds wrong, and nothing else would say so.
    """
    chinese = [_title_scene("你能相信吗？老挝有一座特别的小城。"),
               _title_scene("这座小城的整个经济，几乎全靠一个产业。")]
    english = [_title_scene("Can you believe it? A small city in Laos.")]

    assert acd.title_language_differs("One City, One Industry", chinese)
    assert not acd.title_language_differs("一城一业", chinese)
    assert not acd.title_language_differs("One City", english)


def test_a_title_with_no_letters_has_no_language_to_disagree_with():
    """"2026" is not English just because it is not Chinese."""
    chinese = [_title_scene("这座小城的整个经济，几乎全靠一个产业。")]
    assert not acd.title_language_differs("2026", chinese)
    assert not acd.title_language_differs("#3", chinese)


# ------------------------------------------------- the global video speed ----
#
# Every test here asserts a *ratio* between two speeds rather than an absolute
# number. The failure this setting exists to prevent is one part of the video
# keeping its old length while the rest speeds up, and a ratio is the only
# thing that catches that wherever it happens.


def test_the_default_speed_is_1_2_and_the_baseline_is_1_0():
    """An unset speed is the default, not the baseline.

    The two are different numbers on purpose: 1.0 is what every duration in
    .env is written at, and the default is what a video is built at when
    nobody says otherwise.
    """
    assert acd.DEFAULT_VIDEO_SPEED == 1.2
    assert acd.BASELINE_VIDEO_SPEED == 1.0
    assert acd.validate_speed(None) == 1.2
    assert acd.validate_speed("") == 1.2


def test_a_speed_outside_what_the_voice_can_read_is_refused():
    """Not clamped silently: past this the picture and the voice separate.

    speech_rate is a percentage offset in [-50, 100], so a video cut to 3x
    would be cut to a pace its own narration could not be spoken at - which is
    the mismatch the whole setting exists to remove.
    """
    for bad in (0.2, 3.0, -1):
        with pytest.raises(RuntimeError, match="VIDEO_SPEED"):
            acd.validate_speed(bad)
    with pytest.raises(RuntimeError, match="VIDEO_SPEED"):
        acd.validate_speed("quickly")
    assert acd.validate_speed(acd.MIN_VIDEO_SPEED) == acd.MIN_VIDEO_SPEED
    assert acd.validate_speed(acd.MAX_VIDEO_SPEED) == acd.MAX_VIDEO_SPEED


def test_the_copy_is_read_faster_rather_than_resampled():
    """The service takes a rate, so there is no pitch shift to undo later."""
    assert acd.speech_rate_for(1.0) == 0
    assert acd.speech_rate_for(1.5) == 50
    assert acd.speech_rate_for(0.5) == -50


def test_a_voice_trim_keeps_its_meaning_at_every_speed():
    """ARK_TTS_SPEECH_RATE stays a per-voice adjustment, not a second speed.

    It multiplies rather than adds, so "this voice reads 10% fast" is still
    10% fast at 1.5x instead of becoming 6.7% fast.
    """
    assert acd.speech_rate_for(1.0, 10) == 10
    assert acd.speech_rate_for(1.5, 10) == 65      # 1.5 * 1.10 = 1.65
    assert acd.speech_rate_for(2.0, 50) == 100     # clamped to the API's ceiling


def test_every_configured_duration_arrives_already_scaled(monkeypatch, tmp_path):
    """Config is where settings become timeline values, so it is where speed lands.

    Scaling at each use instead would make the next duration added to this file
    correct only if whoever added it remembered.
    """
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    for name, value in {"ARK_API_KEY": "k", "ARK_TTS_VOICE_TYPE": "v",
                        "JIAN_YING_DRAFT_DIR": str(drafts),
                        # Set, because its default is now 0 and 0/1.5 == 0
                        # would pass whether or not the hold is scaled at all.
                        "ENDING_HOLD_SECONDS": "1.8"}.items():
        monkeypatch.setenv(name, value)

    slow = acd.Config.load(speed=1.0)
    fast = acd.Config.load(speed=1.5)
    for field in ("opening_lead_us", "title_lead_us", "title_us", "bgm_ramp_us",
                  "paragraph_pause_us", "ending_hold_us", "subtitle_animation_us"):
        assert getattr(fast, field) == pytest.approx(
            getattr(slow, field) / 1.5, abs=1), field
    # A rate per second, not a duration: it multiplies.
    assert fast.ken_burns_rate == pytest.approx(slow.ken_burns_rate * 1.5)
    # And the copy is read at the speed the pictures are cut to.
    assert fast.ark_tts_speech_rate == 50


def test_the_env_file_still_wins_when_no_flag_is_given(monkeypatch, tmp_path):
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    for name, value in {"ARK_API_KEY": "k", "ARK_TTS_VOICE_TYPE": "v",
                        "JIAN_YING_DRAFT_DIR": str(drafts),
                        "VIDEO_SPEED": "1.25"}.items():
        monkeypatch.setenv(name, value)
    assert acd.Config.load().speed == 1.25
    assert acd.Config.load(speed=1.75).speed == 1.75      # --speed overrides it


def test_the_camera_crosses_the_same_ground_in_a_shorter_shot():
    """A push at 1.5x is the same push, played faster - not one that crawls.

    Travel is rate x seconds. The scene is 1.5x shorter and the rate is 1.5x
    higher, so the endpoints are identical and the move simply happens quicker.
    Leaving the rate alone would have kept the old travel-per-second under
    narration that had moved on, which is a camera visibly lagging its video.
    """
    move = acd.KEN_BURNS_MOVES[0]
    slow = acd.ken_burns_keyframes(6 * SECOND, move, acd.DEFAULT_KEN_BURNS_RATE)
    fast = acd.ken_burns_keyframes(round(6 * SECOND / 1.5), move,
                                   acd.DEFAULT_KEN_BURNS_RATE * 1.5)
    assert fast == pytest.approx(slow)


def test_the_opening_cap_shortens_with_everything_else():
    """4s of title card in a 1.5x video is 4s of the viewer waiting.

    The cap and the breath after the title are the two durations that live at
    module scope rather than on Config, so they are the two a caller has to
    put on the clock itself.
    """
    cap = acd.paced_us(acd.MAX_OPENING_LEAD_US, 1.5)
    tail = acd.paced_us(acd.TITLE_TAIL_US, 1.5)
    assert cap == pytest.approx(acd.MAX_OPENING_LEAD_US / 1.5, abs=1)
    # A title that fitted at 1.0x need not fit at 1.5x - but the title read at
    # 1.5x is shorter too, so the one that actually occurs still does.
    spoken_at_1x = round(2.6 * SECOND)
    lead = acd.paced_us(round(0.45 * SECOND), 1.5)
    assert not acd.title_voice_fits(spoken_at_1x, lead, tail, cap)
    assert acd.title_voice_fits(round(spoken_at_1x / 1.5), lead, tail, cap)


def test_a_manifest_from_another_speed_is_not_reused():
    """Resuming keeps the pictures and re-reads the voice.

    Cut to the old clips the video would be at neither speed, and every stage
    would report success.
    """
    assert acd.speeds_match(1.5, 1.5)
    assert acd.speeds_match(1.5, 1.5004)      # same integer speech_rate
    assert not acd.speeds_match(1.5, 1.0)


def test_resuming_at_a_new_speed_forgets_the_voice_and_keeps_the_pictures():
    """Clearing the paths is the point: assets are adopted back by filename.

    Forgetting the clips only in the manifest would let the next run re-adopt
    the same files off disk, cut the new timeline to them, and report a clean
    resume - a video at neither speed with nothing anywhere saying so.
    """
    def made():
        return [acd.Scene(text="一句话", image_prompt="p",
                          audio_path="01.mp3", image_path="01.png",
                          duration_us=2 * SECOND)]

    scenes = made()
    assert acd.drop_stale_narration(scenes, True, 1.0, 1.5)
    assert scenes[0].audio_path is None and scenes[0].duration_us is None
    assert scenes[0].image_path == "01.png"      # the expensive half is kept

    for resuming, was, now in ((True, 1.5, 1.5), (False, 1.0, 1.5)):
        scenes = made()
        assert not acd.drop_stale_narration(scenes, resuming, was, now)
        assert scenes[0].audio_path == "01.mp3"


# --------------------------------------- the title the copy already says ----


def test_a_restated_title_is_caught_even_when_it_is_reworded():
    """The director rewrites as it splits, so a repeat is rarely a prefix.

    This is the case an exact comparison missed: the headline comes back with
    its clauses swapped and a particle changed, matches no prefix of anything,
    and is still the same sentence said twice at the top of the video.
    """
    assert acd.title_already_narrated(
        "男人不能为女人做的3件事", [_title_scene("有3件事，男人不要为女人做。")])


def test_a_restatement_in_the_second_sentence_counts_too():
    """Sentence one is a hook; the thesis the title came from is sentence two."""
    assert acd.title_already_narrated(
        "男人不能为女人做的3件事",
        [_title_scene("今天聊个扎心的话题。"),
         _title_scene("有3件事，男人千万不要为女人做。")])
    # And not past the opening pair: by the third sentence the viewer has
    # heard the title, moved on, and a later echo is not a stutter.
    assert not acd.title_already_narrated(
        "男人不能为女人做的3件事",
        [_title_scene("先说点别的。"), _title_scene("再说点别的。"),
         _title_scene("有3件事，男人千万不要为女人做。")])


def test_sharing_a_subject_is_not_saying_the_same_thing():
    """A near miss has to stay spoken.

    Dropping the voice from a title the copy never says loses the opening line
    outright, which is a worse video than the stutter this check exists to
    prevent - so the ratios are set to let a near miss through.
    """
    assert not acd.title_already_narrated(
        "为什么你存不下钱",
        [_title_scene("今天聊聊钱的事。"),
         _title_scene("你有没有发现，工资一到手就没了？")])


def test_a_short_title_still_matches_when_it_is_quoted_outright():
    """Too short for the ratios, but containment needs no length to be sure."""
    assert acd.title_already_narrated("记账", [_title_scene("我建议你从记账开始。")])
    assert not acd.title_already_narrated("三十岁", [_title_scene("二十岁的时候你不会懂。")])


# ----------------------------------------- finding Jianying's drafts --------


def _fake_jianying(tmp_path, monkeypatch, relocated=None):
    """A LOCALAPPDATA holding an install, optionally with a moved library."""
    local = tmp_path / "Local"
    root = local / "JianyingPro"
    (root / "User Data" / "Projects" / acd.JIANYING_DRAFT_LEAF).mkdir(parents=True)
    if relocated is not None:
        relocated.mkdir(parents=True, exist_ok=True)
        config = root / "User Data" / "Config"
        config.mkdir(parents=True, exist_ok=True)
        (config / "globalSetting").write_text(
            json.dumps({"someOtherPath": str(tmp_path / "nope"),
                        "currentDraftUserPath": str(relocated.parent)}),
            encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setenv("JIAN_YING_DRAFT_DIR", "")
    return root


def test_the_drafts_folder_is_found_without_being_configured(tmp_path, monkeypatch):
    """The setting every new user got wrong first is now usually unnecessary."""
    root = _fake_jianying(tmp_path, monkeypatch)
    found, source = acd.resolve_draft_dir()
    assert found == root / "User Data" / "Projects" / acd.JIANYING_DRAFT_LEAF
    assert source == "detected"


def test_a_relocated_library_beats_the_empty_default(tmp_path, monkeypatch):
    """Moving the library leaves the default folder in place but empty.

    A probe that knows only the default finds that folder, calls it a hit, and
    writes every draft where the editor no longer looks - a run that reports
    success and a draft list that stays empty.
    """
    moved = tmp_path / "D" / "JianyingDrafts" / acd.JIANYING_DRAFT_LEAF
    _fake_jianying(tmp_path, monkeypatch, relocated=moved)
    assert acd.resolve_draft_dir() == (moved, "detected")


def test_an_explicit_setting_still_wins_and_is_still_checked(tmp_path, monkeypatch):
    """Setting it means this machine is the unusual one; a typo is a mistake."""
    _fake_jianying(tmp_path, monkeypatch)
    mine = tmp_path / "mine"
    mine.mkdir()
    monkeypatch.setenv("JIAN_YING_DRAFT_DIR", str(mine))
    assert acd.resolve_draft_dir() == (mine, "JIAN_YING_DRAFT_DIR")

    monkeypatch.setenv("JIAN_YING_DRAFT_DIR", str(tmp_path / "typo"))
    with pytest.raises(RuntimeError, match="JIAN_YING_DRAFT_DIR"):
        acd.resolve_draft_dir()


def test_no_editor_installed_says_which_setting_to_fill(monkeypatch):
    monkeypatch.setenv("JIAN_YING_DRAFT_DIR", "")
    monkeypatch.setattr(acd, "jianying_app_roots", list)
    with pytest.raises(RuntimeError, match="JIAN_YING_DRAFT_DIR"):
        acd.resolve_draft_dir()


# ------------------------------------------------- picking the music --------


def _library(directory, *names):
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / name).write_bytes(b"")
    return directory


def test_the_mood_is_read_off_the_front_of_the_filename():
    """The tag is where the tracks already carry it: before the title."""
    assert acd.bgm_tag(Path("紧张Kill Drill - Robert Ruth.mp3")) == "紧张"
    assert acd.bgm_tag(Path("紧张危机Dismantle - Peter Sandberg.mp3")) == "紧张危机"
    # A Chinese artist name at the END of the filename is not the tag.
    assert acd.bgm_tag(Path("舒缓Keep on the Sunny Side - 岩崎太整.mp3")) == "舒缓"
    assert acd.bgm_tag(Path("Untagged Track.mp3")) == ""


def test_the_closest_tag_wins(tmp_path):
    """A track tagged with both moods beats one tagged with either."""
    entries = acd.bgm_library(_library(
        tmp_path / "bgm", "紧张Kill Drill.mp3", "紧张危机Dismantle.mp3",
        "舒缓Sunny Side.mp3", "notes.txt"))
    assert len(entries) == 3, "only playable files are library entries"
    assert acd.pick_bgm(entries, ["紧张", "危机"]).name == "紧张危机Dismantle.mp3"
    assert acd.pick_bgm(entries, ["紧张"]).name == "紧张Kill Drill.mp3"
    assert acd.pick_bgm(entries, ["舒缓"]).name == "舒缓Sunny Side.mp3"


def test_a_track_written_for_an_opening_is_a_worse_bed_than_a_plain_one(tmp_path):
    """One track is laid under the whole video, so a positional cue ranks down.

    Ranked down, not excluded: a library holding nothing else still has music.
    """
    entries = acd.bgm_library(_library(
        tmp_path / "both", "开头失落IV.mp3", "失落Rain.mp3"))
    assert acd.pick_bgm(entries, ["失落"]).name == "失落Rain.mp3"

    only_positional = acd.bgm_library(_library(tmp_path / "solo", "开头失落IV.mp3"))
    assert acd.pick_bgm(only_positional, ["失落"]).name == "开头失落IV.mp3"


def test_nothing_matching_means_no_music_rather_than_any_music(tmp_path):
    """The wrong bed is more distracting than none, and nobody reviews it."""
    entries = acd.bgm_library(_library(tmp_path / "bgm", "舒缓Sunny Side.mp3"))
    assert acd.pick_bgm(entries, ["紧张"]) is None
    assert acd.bgm_library(tmp_path / "missing") == []


def test_the_same_copy_picks_the_same_track_every_time(tmp_path):
    """Two tracks tagged alike must not be chosen between by directory order."""
    directory = _library(tmp_path / "bgm", "紧张B.mp3", "紧张A.mp3", "紧张C.mp3")
    assert {acd.pick_bgm(acd.bgm_library(directory), ["紧张"]).name
            for _ in range(5)} == {"紧张A.mp3"}


def test_the_copy_itself_answers_when_the_director_does_not():
    """The fallback that keeps an older manifest, or a quiet model, in music."""
    assert "焦虑" in acd.copy_moods("他很焦虑，晚上睡不着，总是担心明天。")
    assert acd.copy_moods("。。。") == []


def test_only_labels_the_library_can_match_are_kept():
    """A word the model invented matches no filename and displaces a real one."""
    assert acd.moods_from_payload({"mood": ["紧张", "波澜壮阔", "危机"]}) == ["紧张", "危机"]
    assert acd.moods_from_payload({"mood": "舒缓"}) == ["舒缓"]
    assert acd.moods_from_payload({}) == []


def test_an_explicit_track_is_not_second_guessed(tmp_path, monkeypatch):
    """BGM_PATH is somebody naming a track; the library is not consulted."""
    chosen = tmp_path / "mine.mp3"
    chosen.write_bytes(b"")
    library = _library(tmp_path / "bgm", "紧张Kill Drill.mp3")
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    for name, value in {"ARK_API_KEY": "k", "ARK_TTS_VOICE_TYPE": "v",
                        "JIAN_YING_DRAFT_DIR": str(drafts),
                        "BGM_PATH": str(chosen),
                        "BGM_LIBRARY": str(library)}.items():
        monkeypatch.setenv(name, value)
    path, reason = acd.resolve_bgm(acd.Config.load(), "随便什么文案", ["紧张"])
    assert path == chosen
    assert reason == "BGM_PATH"


def test_the_bed_sits_between_20_and_25_dB_under_the_voice(tmp_path, monkeypatch):
    """Both levels, not only the ducked one.

    The lift used to be 0.20 - 14 dB down, which is music rather than
    atmosphere, and the one thing in the mix loud enough to compete with the
    line that follows the gap it fills.
    """
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    for name, value in {"ARK_API_KEY": "k", "ARK_TTS_VOICE_TYPE": "v",
                        "JIAN_YING_DRAFT_DIR": str(drafts)}.items():
        monkeypatch.setenv(name, value)
    cfg = acd.Config.load()
    for level in (cfg.bgm_volume, cfg.bgm_lift_volume):
        assert -25.5 <= 20 * math.log10(level) <= -19.5
    assert cfg.bgm_volume < cfg.bgm_lift_volume, "the gaps still lift"


# ------------------------------------------ one subject, drawn largest -----


def test_the_frame_is_told_what_its_subject_is():
    """The whole hierarchy hangs off a subject, so it has to reach the prompt."""
    scene = _scene(shot_size="medium")
    scene.subject = "a woman at a kitchen table"
    prompt = acd.compose_image_prompt(_StubConfig(), scene, [])
    assert "exactly one subject and it is a woman at a kitchen table" in prompt
    assert "the largest thing in the picture" in prompt


def test_a_scene_with_no_subject_still_composes():
    """A v7 manifest, or a model that skipped the field.

    The scene is still usable and the hierarchy still applies - it is only the
    naming that falls back. Refusing here would throw away a storyboard that
    was otherwise fine, and paid for.
    """
    prompt = acd.compose_image_prompt(_StubConfig(), _scene(), [])
    assert acd.DEFAULT_SUBJECT in prompt
    assert "exactly one subject" in prompt


def test_the_prompt_no_longer_argues_against_a_focal_point():
    """The bug, in one line.

    "do not visually overemphasize one incidental detail" reads as "do not
    emphasise anything", and that is what came back: every element at one
    size, one weight and one level of detail, with nothing for the eye to land
    on. The worry it came from now lives in the director's brief, where it can
    be about choosing the right subject instead of about flattening the frame.
    """
    prompt = acd.compose_image_prompt(_StubConfig(), _scene(), [])
    assert "overemphasize" not in prompt
    assert "do not visually magnify" not in prompt


def test_the_element_budget_tightens_as_the_shot_closes():
    """A close-up is a face; a wide shot is a place. They cannot hold the same.

    The budget is one number per framing, read by both the image prompt and
    the director's brief, so a brief cannot ask for more than the picture is
    allowed to draw.
    """
    budgets = [acd.SHOT_SIZES[size].elements for size in ("wide", "medium", "close")]
    assert budgets == sorted(budgets, reverse=True), budgets
    assert acd.SHOT_SIZES["close"].elements == 1

    wide = acd.compose_image_prompt(_StubConfig(), _scene(shot_size="wide"), [])
    close = acd.compose_image_prompt(_StubConfig(), _scene(shot_size="close"), [])
    assert "At most 4 supporting elements" in wide
    assert "At most one supporting element" in close


def test_the_director_is_told_the_same_budget_the_picture_is_drawn_to():
    class _Cfg(_StubConfig):
        style = acd.STYLE_PRESETS[acd.DEFAULT_STYLE_PRESET]
        scene_length_mode = "density"
        scene_characters = 22

    brief = acd.storyboard_prompt(_Cfg(), 1, 1, 8, 10, [])
    for name, size in acd.SHOT_SIZES.items():
        assert f"{name} allows at most {size.elements} besides the subject" in brief


def test_the_subject_scales_with_the_framing():
    """"Large" and "small" are what drew a subject the size of a chair."""
    heights = {size: acd.compose_image_prompt(
        _StubConfig(), _scene(shot_size=size), []) for size in acd.SHOT_SIZES}
    assert "about a third of the frame height" in heights["wide"]
    assert "about two thirds of the frame height" in heights["medium"]
    assert "at least three quarters of the frame height" in heights["close"]


def test_one_picture_of_one_moment_not_a_layout():
    """A collage has no subject; it has a list. Both ends are told so."""
    prompt = acd.compose_image_prompt(_StubConfig(), _scene(), [])

    class _Cfg(_StubConfig):
        style = acd.STYLE_PRESETS[acd.DEFAULT_STYLE_PRESET]
        scene_length_mode = "density"
        scene_characters = 22

    brief = acd.storyboard_prompt(_Cfg(), 1, 1, 8, 10, [])
    for text in (prompt, brief):
        assert "split screen" in text
        assert "grid of panels" in text


def _brief():
    class _Cfg(_StubConfig):
        style = acd.STYLE_PRESETS[acd.DEFAULT_STYLE_PRESET]
        scene_length_mode = "density"
        scene_characters = 22

    return acd.storyboard_prompt(_Cfg(), 1, 1, 8, 10, [])


def test_the_director_must_name_a_thing_rather_than_an_idea():
    """A named abstraction is where the pile of symbols comes from."""
    brief = _brief()
    assert 'named in "subject"' in brief
    assert "Not an abstraction" in brief
    assert 'never two things joined by "and"' in brief


def test_a_place_or_an_object_is_as_valid_a_subject_as_a_person():
    """The bug the reference frames showed: a person in nearly every shot.

    A person was being chosen three separate times over - as the fallback for
    an abstract line, as an explicit preference over objects, and again
    whenever the sentence was about feeling. On copy that is entirely about
    feeling, that is every frame.
    """
    brief = _brief()
    assert "Do not default to a person" in brief
    for kind in ("PLACE", "OBJECT", "PERSON"):
        assert kind in brief, kind
    # And the preferences that produced the bug are gone, not merely balanced.
    assert "Prefer a person doing something concrete over an object" not in brief
    assert "the picture is that person's face" not in brief
    assert "choose the person it happens to" not in brief


def test_the_director_has_to_furnish_the_room():
    """"Bring the scene to life" is furniture and a light source, not a mood.

    An unfurnished frame is what makes a subject look cut out and pasted on,
    and it is the difference between the reference frames and a sprite on a
    gradient.
    """
    brief = _brief()
    assert "named, furnished room" in brief
    assert "one light source" in brief


def test_one_symbol_in_a_real_room_is_allowed_again():
    """The blanket ban overshot what it was written against.

    The reference account does use a symbolic figure - a silhouette in a
    doorway, a crack of red light under a door - and they work because they
    sit inside a real space. What does not work is the same shapes floating on
    nothing, which is all the rule needs to forbid.
    """
    brief = _brief()
    assert "At most one symbolic element per frame" in brief
    assert "has to sit in the room" in brief
    # The part that was always right stays.
    for banned in ("collage", "split screen", "grid of panels"):
        assert banned in brief, banned


def test_the_subject_survives_the_storyboard_and_the_manifest():
    scenes = acd.scenes_from_payload({"scenes": [
        {"text": "一句话", "image_prompt": "x", "subject": " a cracked phone screen "},
        {"text": "另一句", "image_prompt": "x"},
        {"text": "第三句", "image_prompt": "x", "subject": 7},
    ]})
    assert [s.subject for s in scenes] == ["a cracked phone screen", "", ""]
    # And back out of a manifest, which is what --resume reads.
    restored = acd.scenes_from_manifest(
        {"scenes": [asdict(scene) for scene in scenes]})
    assert [s.subject for s in restored] == ["a cracked phone screen", "", ""]


# ----------------------------------------- the default style's hierarchy ---


def test_the_default_style_paints_the_whole_scene():
    """Five of the seven reference frames are fully painted interiors.

    The previous preset read them as "white line room, painted subject",
    which is true of two. Its first real frame was a door and a mailbox drawn
    as a flat vector icon on bare navy, and the style's own "large areas of
    unbroken navy" clause was one of three rules emptying the frame.
    """
    preset = acd.STYLE_PRESETS["midnight"]
    assert "fully painted in colour" in preset.prompt
    assert "black ink outlines" in preset.prompt
    assert "furnished to the edges" in preset.prompt
    assert "no flat vector icons" in preset.prompt
    assert "no plain empty background" in preset.prompt
    # Both earlier contracts, gone rather than contradicted.
    assert "Single-colour white line illustration" not in preset.prompt
    assert "unbroken navy" not in preset.prompt
    assert "full-colour painting" not in preset.avoid


def test_the_default_style_still_says_where_the_light_comes_from():
    """One warm source against the navy is what stops it reading as flat."""
    prompt = acd.STYLE_PRESETS["midnight"].prompt
    assert "one warm practical light source" in prompt.lower()
    assert "silhouette" in prompt


def test_the_shipped_styles_file_matches_the_built_in_default():
    """styles.json wins at runtime, so editing only the built-in changes nothing.

    The two are allowed to diverge on a user's machine - that is the whole
    point of the file - but the copy committed here is the one everybody
    starts from, and it silently overrode the code for the default style.
    """
    shipped = json.loads((acd.ROOT / "styles.json").read_text(encoding="utf-8"))
    for name, preset in shipped["presets"].items():
        if isinstance(preset, dict) and name in acd.STYLE_PRESETS:
            assert preset["prompt"] == acd.STYLE_PRESETS[name].prompt, name


# ----------------------------------------------- a frame that is a scene ----


def test_the_frame_is_filled_not_emptied():
    """"Leave a third of the frame empty" was the loudest of three rules.

    With the style asking for unbroken navy and a wide shot shrinking a place
    to a third of the frame, the first real frame was about 60% bare ground.
    The subject keeps clear space around its own outline; the frame is a scene.
    """
    assert "Leave at least a third of the frame" not in acd.COMPOSITION
    assert "fill the frame" in acd.COMPOSITION
    assert "never a subject floating on an empty field" in acd.COMPOSITION


def test_a_place_fills_a_wide_shot():
    """A third of the frame height is right for a person and makes a place an icon."""
    wide = acd.SHOT_SIZES["wide"].subject_height
    assert "filling the frame if it is a place" in wide
    assert "a third of the frame height if it is a person or an object" in wide


def test_at_least_half_the_subjects_are_places_or_objects():
    """"Several" let a director return two places in five scenes and stop."""
    assert "at least half of the scenes" in acd.DIRECTION


# ------------------------------------------------------ the director -------


def _director_env(monkeypatch, tmp_path, **extra):
    drafts = tmp_path / "drafts"
    drafts.mkdir(exist_ok=True)
    values = {"ARK_API_KEY": "ark-test", "ARK_TTS_VOICE_TYPE": "v",
              "JIAN_YING_DRAFT_DIR": str(drafts), "DEEPSEEK_API_KEY": "",
              "ARK_TEXT_MODEL": "", "DEEPSEEK_MODEL": "", "DEEPSEEK_BASE_URL": ""}
    values.update(extra)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return acd.Config.load()


def test_the_director_goes_to_deepseek_when_a_key_is_set(monkeypatch, tmp_path):
    """deepseek-chat answered the same brief in 6s against 98s on Ark."""
    cfg = _director_env(monkeypatch, tmp_path, DEEPSEEK_API_KEY="sk-test")
    assert cfg.text_provider == "deepseek"
    assert cfg.text_base_url == "https://api.deepseek.com"
    assert cfg.text_model == "deepseek-chat"
    assert cfg.text_api_key == "sk-test"
    # Images and narration stay on Ark - that is what the Agent Plan is for.
    assert cfg.ark_api_key == "ark-test"


def test_without_a_deepseek_key_the_director_stays_on_ark(monkeypatch, tmp_path):
    cfg = _director_env(monkeypatch, tmp_path)
    assert cfg.text_provider == "ark"
    assert cfg.text_api_key == "ark-test"
    assert cfg.text_model == acd.DEFAULT_ARK_TEXT_MODEL


def test_the_storyboard_turns_thinking_off(monkeypatch, tmp_path):
    """The flag that was the difference between a storyboard and a timeout.

    With thinking on, deepseek-v4-flash reasoned past 38,000 characters and
    never answered; with it off, the same model finished in 15 seconds.
    """
    cfg = _director_env(monkeypatch, tmp_path)
    seen = {}

    def fake_stream_chat(url, api_key, body, what):
        seen.update(body)
        return '{"scenes": [{"text": "一句", "image_prompt": "x"}]}'

    monkeypatch.setattr(acd, "stream_chat", fake_stream_chat)
    acd.request_storyboard(cfg, "brief", "copy", 1)
    assert seen["thinking"] == {"type": "disabled"}
    assert acd.DEFAULT_DEEPSEEK_MODEL == "deepseek-chat"


# ------------------------------------------------------------ streaming ----


class _Stream:
    """A streamed chat response, as requests hands it back."""

    def __init__(self, lines, status=200, cut_after=None):
        self.status_code = status
        self.encoding = None
        self._lines = lines
        self._cut_after = cut_after
        self.text = ""

    def iter_lines(self, decode_unicode=False):
        for index, line in enumerate(self._lines):
            if self._cut_after is not None and index == self._cut_after:
                raise acd.requests.exceptions.ChunkedEncodingError("cut")
            yield line

    def close(self):
        pass


def _sse(**delta):
    return "data: " + json.dumps({"choices": [{"delta": delta}]}, ensure_ascii=False)


def test_stream_chat_returns_only_the_answer(monkeypatch):
    """Reasoning is read and discarded; the storyboard is `content` alone."""
    lines = [_sse(reasoning_content="thinking about scenes..."),
             _sse(content='{"scenes"'), _sse(content=': []}'), "data: [DONE]"]
    monkeypatch.setattr(acd, "post", lambda *a, **k: _Stream(lines))
    assert acd.stream_chat("u", "k", {"model": "m"}, "Test") == '{"scenes": []}'


def test_stream_chat_asks_for_a_stream_and_decodes_utf8(monkeypatch):
    """requests guesses Latin-1 for an event stream; the Chinese came back as mojibake."""
    seen = {}
    stream = _Stream([_sse(content="很多人以为"), "data: [DONE]"])

    def fake_post(url, **kwargs):
        seen.update(kwargs)
        return stream

    monkeypatch.setattr(acd, "post", fake_post)
    assert acd.stream_chat("u", "k", {"model": "m"}, "Test") == "很多人以为"
    assert seen["json"]["stream"] is True and seen["stream"] is True
    assert stream.encoding == "utf-8"


def test_a_model_that_only_reasons_is_named_rather_than_blamed_on_json(monkeypatch):
    """The failure that looked like a network fault, reported as what it is."""
    lines = [_sse(reasoning_content="x" * 500), "data: [DONE]"]
    monkeypatch.setattr(acd, "post", lambda *a, **k: _Stream(lines))
    with pytest.raises(RuntimeError, match="returned no answer") as caught:
        acd.stream_chat("u", "k", {"model": "deepseek-v4-flash"}, "Test")
    assert "deepseek-v4-flash" in str(caught.value)
    assert "500 characters of reasoning" in str(caught.value)


def test_a_stream_cut_midway_is_started_again(monkeypatch):
    """What arrived before the cut is not a storyboard, so it is not kept."""
    attempts = iter([
        _Stream([_sse(content="{partial"), _sse(content="more")], cut_after=1),
        _Stream([_sse(content='{"ok": true}'), "data: [DONE]"]),
    ])
    monkeypatch.setattr(acd, "post", lambda *a, **k: next(attempts))
    monkeypatch.setattr(acd.time, "sleep", lambda seconds: None)
    assert acd.stream_chat("u", "k", {"model": "m"}, "Test") == '{"ok": true}'


def test_the_storyboard_goes_to_the_director_endpoint(monkeypatch, tmp_path):
    cfg = _director_env(monkeypatch, tmp_path, DEEPSEEK_API_KEY="sk-test")
    seen = {}

    def fake_stream_chat(url, api_key, body, what):
        seen.update(url=url, key=api_key, model=body["model"])
        return '{"scenes": [{"text": "一句", "image_prompt": "x"}]}'

    monkeypatch.setattr(acd, "stream_chat", fake_stream_chat)
    data = acd.request_storyboard(cfg, "brief", "copy", 1)
    assert seen == {"url": "https://api.deepseek.com/chat/completions",
                    "key": "sk-test", "model": "deepseek-chat"}
    assert data["scenes"][0]["text"] == "一句"


# ---------------------------------------------------- billed requests, retried --
# A timed-out POST may already have been accepted upstream. Retried, a billed
# request pays twice for one frame and orphans the first job; the opt-out was
# written for exactly that and no caller used it.


def _failing(monkeypatch, exc):
    calls = []

    def request(method, url, **kwargs):
        calls.append(url)
        raise exc

    monkeypatch.setattr(acd.requests, "request", request)
    monkeypatch.setattr(acd.time, "sleep", lambda seconds: None)
    return calls


def test_a_billed_request_is_not_sent_again_once_it_may_have_arrived(monkeypatch):
    """A read timeout can come after the server accepted - and charged for - the image."""
    calls = _failing(monkeypatch, acd.requests.ReadTimeout("read timed out"))
    with pytest.raises(RuntimeError, match="billed twice"):
        acd.post("https://example.invalid/images", idempotent=False)
    assert len(calls) == 1


def test_a_connection_dropped_mid_response_is_not_resent_either(monkeypatch):
    """The "SSL EOF" this network path produces arrives after the request was written."""
    calls = _failing(monkeypatch, acd.requests.ConnectionError("Connection aborted."))
    with pytest.raises(RuntimeError, match="billed twice"):
        acd.post("https://example.invalid/images", idempotent=False)
    assert len(calls) == 1


def test_a_billed_request_that_never_left_is_still_retried(monkeypatch):
    """A connect timeout fails before a byte is sent, so sending again is free."""
    calls = _failing(monkeypatch, acd.requests.ConnectTimeout("connect timed out"))
    with pytest.raises(RuntimeError, match="after 3 attempts"):
        acd.post("https://example.invalid/images", idempotent=False)
    assert len(calls) == 3


def test_a_refused_connection_counts_as_never_sent():
    from urllib3.exceptions import MaxRetryError, NewConnectionError

    refused = acd.requests.ConnectionError(MaxRetryError(None, "/", NewConnectionError(None, "refused")))
    assert acd.never_sent(refused)
    assert not acd.never_sent(acd.requests.ConnectionError("Connection aborted."))
    assert not acd.never_sent(acd.requests.ReadTimeout("read timed out"))


def test_ordinary_requests_keep_their_retries(monkeypatch):
    """The storyboard and the downloads are safe to repeat, and still are."""
    calls = _failing(monkeypatch, acd.requests.ReadTimeout("read timed out"))
    with pytest.raises(RuntimeError, match="after 3 attempts"):
        acd.get("https://example.invalid/picture.png")
    assert len(calls) == 3


class _ImageConfig(_StubConfig):
    ark_image_url = "https://example.invalid/images"
    ark_api_key = "test-key"
    ark_image_model = "test-model"
    ark_image_size = "2560x1440"
    ark_image_response_format = "url"
    ark_image_output_format = "png"


def test_the_image_request_is_sent_as_billed(monkeypatch, tmp_path):
    seen = {}

    def fake_post(url, **kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop here")

    monkeypatch.setattr(acd, "post", fake_post)
    with pytest.raises(RuntimeError, match="stop here"):
        acd.generate_image(_ImageConfig(), "a prompt", tmp_path / "01_frame.png")
    assert seen["idempotent"] is False

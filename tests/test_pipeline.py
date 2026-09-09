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


# ------------------------------------------------- the synthesised opening --
# The one asset the repo is not allowed to ship was also the one asset it could
# not start without. These check the stand-in is a real cue, not a beep.

def test_the_opening_cue_can_be_generated_without_any_asset(tmp_path):
    import wave

    path = acd.generate_opening_sound(tmp_path / "cue.wav")
    assert path.is_file() and path.stat().st_size > 0
    with wave.open(str(path), "rb") as handle:
        assert handle.getframerate() == 44100
        assert handle.getnframes() > 44100          # longer than a click


def test_the_generated_cue_is_deterministic(tmp_path):
    """Two runs must be byte-identical, or every build churns the material."""
    first = acd.generate_opening_sound(tmp_path / "a.wav").read_bytes()
    second = acd.generate_opening_sound(tmp_path / "b.wav").read_bytes()
    assert first == second


def test_the_generated_cue_hits_hard_and_rings_out(tmp_path):
    """An impact, not a tone: fast body decay over a long quiet tail.

    The first attempt fell 3 dB across the opening quarter-second where the
    reference cue falls 9, and read as a sustained note. The second fixed the
    attack and then died 18 dB early, stopping dead under the narration.
    """
    import array
    import math
    import wave

    with wave.open(str(acd.generate_opening_sound(tmp_path / "cue.wav")), "rb") as handle:
        rate = handle.getframerate()
        frames = array.array("h")
        frames.frombytes(handle.readframes(handle.getnframes()))

    def band(start, length=0.25):
        chunk = frames[int(start * rate):int((start + length) * rate)]
        rms = math.sqrt(sum(value * value for value in chunk) / max(1, len(chunk))) / 32768
        return 20 * math.log10(max(rms, 1e-6))

    head, quarter, late = band(0.0), band(0.25), band(2.5)
    assert head > -22, f"the impact is too soft at {head:.1f} dB"
    assert head - quarter > 6, (
        f"only {head - quarter:.1f} dB of decay in the first quarter-second; "
        "that is a tone, not an impact")
    assert -50 < late < -35, (
        f"the tail sits at {late:.1f} dB - it should still be ringing quietly, "
        "neither silent nor loud enough to sit under the narration")


def test_a_configured_cue_wins_over_the_generated_one(tmp_path, monkeypatch):
    """Your own file, when you have one, is still the default."""
    import wave

    mine = tmp_path / "mine.wav"
    with wave.open(str(mine), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(44100)
        handle.writeframes(b"\x00\x00" * 4410)
    monkeypatch.setenv("OPENING_SOUND_PATH", str(mine))
    monkeypatch.setenv("ARK_API_KEY", "k")
    monkeypatch.setenv("ARK_TTS_VOICE_TYPE", "v")
    monkeypatch.setenv("JIAN_YING_DRAFT_DIR", str(tmp_path))
    cfg = acd.Config.load()
    assert acd.resolve_opening_sound(cfg, tmp_path) == mine


def test_a_typo_in_the_configured_path_is_still_an_error(tmp_path, monkeypatch):
    """Only the default may be absent. A wrong path set on purpose is a bug."""
    monkeypatch.setenv("OPENING_SOUND_PATH", str(tmp_path / "nope.wav"))
    monkeypatch.setenv("ARK_API_KEY", "k")
    monkeypatch.setenv("ARK_TTS_VOICE_TYPE", "v")
    monkeypatch.setenv("JIAN_YING_DRAFT_DIR", str(tmp_path))
    with pytest.raises(RuntimeError, match="does not exist"):
        acd.Config.load()


def test_a_changed_title_is_not_spoken_from_a_stale_clip(tmp_path):
    """--resume keeps existing audio, so the cache key has to be the text.

    With a fixed `title.mp3` a resume with a different --title spoke the old
    title over the new one on screen, and the run reported success.
    """
    import hashlib

    def cache_name(title: str) -> str:
        return f"title_{hashlib.sha1(title.strip().encode('utf-8')).hexdigest()[:12]}.mp3"

    assert cache_name("男人不能为女人做的3件事") != cache_name("女人不能为男人做的3件事")
    assert cache_name("同一个标题") == cache_name("  同一个标题  ")


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


def test_an_unwritable_cue_does_not_lose_the_run(tmp_path, monkeypatch):
    """A missing stinger is a worse opening, not a reason to lose a build.

    By the time the draft is assembled the narration and the images are paid
    for. The title's voice-over already makes this call; the cue now makes the
    same one.
    """
    monkeypatch.delenv("OPENING_SOUND_PATH", raising=False)
    monkeypatch.setattr(acd, "DEFAULT_OPENING_SOUND_PATH", "assets/__absent__.mp3")
    monkeypatch.setenv("ARK_API_KEY", "k")
    monkeypatch.setenv("ARK_TTS_VOICE_TYPE", "v")
    monkeypatch.setenv("JIAN_YING_DRAFT_DIR", str(tmp_path))
    cfg = acd.Config.load()

    def refuse(*_args, **_kwargs):
        raise OSError("No space left on device")

    monkeypatch.setattr(acd, "generate_opening_sound", refuse)
    assert acd.resolve_opening_sound(cfg, tmp_path / "run") is None


def test_the_cue_holds_its_pitch_after_the_drop(tmp_path):
    """The 咚 is a drop to a low note, and it has to stay there.

    The glide originally fell back to its START frequency once the glide
    window closed, snapping 52 Hz up to 96 at 64% amplitude - a click in the
    middle of the hit, and the opposite of the drop the sound is named for.
    Measured by correlating against both frequencies after the glide.
    """
    import array
    import cmath
    import math
    import wave

    with wave.open(str(acd.generate_opening_sound(tmp_path / "cue.wav")), "rb") as handle:
        rate = handle.getframerate()
        frames = array.array("h")
        frames.frombytes(handle.readframes(handle.getnframes()))

    window = [v / 32768 for v in frames[int(0.12 * rate):int(0.28 * rate)]]

    def energy(hertz):
        total = sum(v * cmath.exp(-2j * math.pi * hertz * i / rate)
                    for i, v in enumerate(window))
        return abs(total) / max(1, len(window))

    landed, started = energy(52.0), energy(96.0)
    assert landed > started * 4, (
        f"after the glide the cue sits at 96 Hz ({started:.5f}) more than at "
        f"52 Hz ({landed:.5f}) - the pitch snapped back instead of holding")

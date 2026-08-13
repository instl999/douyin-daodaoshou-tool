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


# ------------------------------------------------------------------- shots --

def test_ken_burns_moves_never_start_below_full_frame():
    """A pan at scale <= 1.0 would expose the edge of the image."""
    for scale_start, scale_end, *_ in acd.KEN_BURNS_MOVES:
        assert scale_start > 1.0
        assert scale_end > 1.0


def test_every_ken_burns_move_actually_moves():
    for scale_start, scale_end, x0, x1, y0, y1 in acd.KEN_BURNS_MOVES:
        assert (scale_start, x0, y0) != (scale_end, x1, y1), "a static move is just a frozen still"


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

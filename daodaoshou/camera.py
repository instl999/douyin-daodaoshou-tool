"""The camera: which move each shot gets, and how far it travels."""

from __future__ import annotations

from typing import Any

from .models import Scene
from .styles import DEFAULT_SHOT_SIZE

# Camera moves, described as a *direction* rather than as fixed endpoints:
# (zoom, pan_x, pan_y), each -1 / 0 / +1.
#
# How far the move actually travels is derived from the shot's length, so a
# 1.6s shot and a 6.2s shot move at the same perceived speed. Fixed endpoints
# meant the same 12% push read as a fast zoom on a short shot and as no motion
# at all on a long one.
PUSH_IN = (+1, 0, 0)
PULL_OUT = (-1, 0, 0)
PAN_RIGHT = (0, +1, 0)
PAN_LEFT = (0, -1, 0)
PUSH_DRIFT_LEFT = (+1, -1, 0)
PUSH_DRIFT_RIGHT = (+1, +1, 0)
PUSH_TILT_DOWN = (+1, 0, -1)
PULL_DRIFT_RIGHT = (-1, +1, 0)
KEN_BURNS_MOVES = (PUSH_IN, PULL_OUT, PAN_RIGHT, PAN_LEFT, PUSH_DRIFT_LEFT,
                   PUSH_DRIFT_RIGHT, PUSH_TILT_DOWN, PULL_DRIFT_RIGHT)

# Which move a shot gets follows from what the shot is for. The five moves
# used to rotate in order, so a shot's move depended only on its position: a
# pull-out could land on the close-up a paragraph builds to, stepping away
# from the face at the moment it matters. The storyboard already says what
# every shot is for, so:
#
#   close   push in - closer to the face, the hands, the object
#   medium  push in with a drift - motion without a statement
#   wide    pull out or pan - the place revealing itself
#   last shot of a paragraph, and of the video
#           pull out - the camera steps back into the breath that follows
#
# Each framing's moves are taken in turn so a run of the same framing still
# varies, and no two shots in a row get the same move.
CAMERA_BY_SHOT = {
    "close": (PUSH_IN, PUSH_TILT_DOWN),
    "medium": (PUSH_DRIFT_LEFT, PUSH_DRIFT_RIGHT, PUSH_IN),
    "wide": (PULL_OUT, PAN_RIGHT, PAN_LEFT),
}
CAMERA_EXHALE = (PULL_OUT, PULL_DRIFT_RIGHT)
# Fraction of the frame travelled per second, and the ceiling for one shot.
DEFAULT_KEN_BURNS_RATE = 0.035
KEN_BURNS_MAX_TRAVEL = 0.22
# Every shot starts already scaled up, so a pan has room before it would
# expose the edge of the image.
KEN_BURNS_BASE_SCALE = 1.08
# A pan covers this much of the zoom travel, and never more than the headroom
# the scale provides (with a margin).
KEN_BURNS_PAN_RATIO = 0.35
KEN_BURNS_PAN_SAFETY = 0.9

def plan_camera(scenes: list[Scene]) -> list[tuple[int, int, int]]:
    """One camera move per scene, chosen from what the shot is for (see CAMERA_BY_SHOT)."""
    last = len(scenes) - 1

    def exhales(index: int) -> bool:
        return index == last or scenes[index].pause_after

    moves: list[tuple[int, int, int]] = []
    turns: dict[str, int] = {}
    for index, scene in enumerate(scenes):
        avoid = {moves[-1]} if moves else set()
        if exhales(index):
            options = CAMERA_EXHALE
        else:
            shot = scene.shot_size if scene.shot_size in CAMERA_BY_SHOT else DEFAULT_SHOT_SIZE
            turn = turns.get(shot, 0)
            turns[shot] = turn + 1
            base = CAMERA_BY_SHOT[shot]
            options = base[turn % len(base):] + base[:turn % len(base)]
            if index < last and exhales(index + 1):
                # The step back belongs to the shot that ends the thought.
                avoid |= set(CAMERA_EXHALE)
        moves.append(next((move for move in options if move not in avoid), options[0]))
    return moves


def ken_burns_keyframes(duration_us: int, move: tuple[int, int, int],
                        rate: float = DEFAULT_KEN_BURNS_RATE) -> tuple[float, float, float, float, float, float]:
    """Turn a move direction into concrete endpoints for a shot of this length.

    Travel is proportional to duration, so every shot moves at the same
    perceived speed. Returns (scale_start, scale_end, x0, x1, y0, y1) with x/y
    in half-canvas units.
    """
    seconds = max(0.1, duration_us / 1_000_000)
    travel = min(KEN_BURNS_MAX_TRAVEL, rate * seconds)
    zoom, pan_x, pan_y = move

    if zoom > 0:
        scale_start, scale_end = KEN_BURNS_BASE_SCALE, KEN_BURNS_BASE_SCALE + travel
    elif zoom < 0:
        scale_start, scale_end = KEN_BURNS_BASE_SCALE + travel, KEN_BURNS_BASE_SCALE
    else:
        # A pure pan holds a middle scale so it has headroom on both sides.
        held = KEN_BURNS_BASE_SCALE + travel / 2
        scale_start = scale_end = held

    # At scale S the image overhangs the canvas by (S - 1) half-canvas units on
    # each side. Staying inside that is what keeps the edge out of frame, and
    # the smaller endpoint is the binding one.
    headroom = max(0.0, min(scale_start, scale_end) - 1.0)
    reach = min(travel * KEN_BURNS_PAN_RATIO, headroom * KEN_BURNS_PAN_SAFETY)
    return (scale_start, scale_end,
            -pan_x * reach, pan_x * reach,
            -pan_y * reach, pan_y * reach)


def apply_ken_burns(video: Any, keyframe_property: Any, duration_us: int,
                    move: tuple[int, int, int], rate: float = DEFAULT_KEN_BURNS_RATE) -> None:
    scale_start, scale_end, x_start, x_end, y_start, y_end = ken_burns_keyframes(duration_us, move, rate)
    video.add_keyframe(keyframe_property.uniform_scale, 0, scale_start)
    video.add_keyframe(keyframe_property.uniform_scale, duration_us, scale_end)
    if x_start != x_end:
        video.add_keyframe(keyframe_property.position_x, 0, x_start)
        video.add_keyframe(keyframe_property.position_x, duration_us, x_end)
    if y_start != y_end:
        video.add_keyframe(keyframe_property.position_y, 0, y_start)
        video.add_keyframe(keyframe_property.position_y, duration_us, y_end)



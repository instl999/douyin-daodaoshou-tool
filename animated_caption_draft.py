"""Generate a Jianying draft from Chinese copy with Volcengine Ark Agent Plan."""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import sys
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent

# ---------------------------------------------------------------- canvas ----
# The draft is landscape. Every layout coordinate below is derived from these
# two numbers so that the canvas size is only written once.
CANVAS_WIDTH = 1920
CANVAS_HEIGHT = 1080
CANVAS_HALF_HEIGHT = CANVAS_HEIGHT / 2

# Layout Y values in .env are written in pixels against a 1920-tall reference
# frame, not against the current canvas. Keeping the reference independent of
# the canvas means the same NARRATION_SUBTITLE_Y sits at the same *relative*
# height whether the draft is 1920x1080 or 1080x1920.
#
#   normalized_y = env_pixels / LAYOUT_REFERENCE_HALF_HEIGHT
#
# pyJianYingDraft expresses ClipSettings.transform_y in half-canvas-heights, so
# the on-screen range is [-1, 1]. The default -700 resolves to -0.729, i.e.
# about 146 px above the bottom edge of a 1080-tall canvas.
LAYOUT_REFERENCE_HALF_HEIGHT = 960
# Never let a misconfigured value push text off the visible frame.
SAFE_NORMALIZED_Y = 0.92

DEFAULT_ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/plan/v3"
DEFAULT_ARK_IMAGE_URL = f"{DEFAULT_ARK_BASE_URL}/images/generations"
DEFAULT_ARK_TTS_URL = "https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional"
DEFAULT_ARK_TEXT_MODEL = "deepseek-v4-flash"
DEFAULT_ARK_IMAGE_MODEL = "doubao-seedream-5.0-lite"
DEFAULT_ARK_TTS_MODEL = "seed-tts-2.0"
DEFAULT_OPENING_SOUND_PATH = "assets/opening_dong.mp3"

# Whole-video art direction. Pick one with IMAGE_STYLE_PRESET, or override the
# text entirely with IMAGE_STYLE_PROMPT.
STYLE_PRESETS = {
    # The short-video emotional-story look: thick even ink lines, flat muted
    # colour, soft even light, rosy cheeks, sparse domestic backgrounds.
    "story": (
        "Chinese webtoon manhua panel in the style of popular emotional-story short videos, contemporary Chinese "
        "everyday setting, semi-realistic adult characters with slightly caricatured and very readable facial "
        "expressions, believable adult proportions, thick even-weight black ink outlines on every figure and prop, "
        "flat colour fills with soft airbrushed shading and almost no gradients, muted low-saturation palette of "
        "dusty blue-grey, warm beige, soft olive and pale peach skin, gentle rosy blush on cheeks and nose, even "
        "soft ambient lighting with low contrast and no dramatic shadows or rim light, simple uncluttered domestic "
        "or street interior with only a few clearly drawn props and a plain wall, medium two-shot framing with "
        "generous negative space, clean 2D digital illustration with a subtle warm printed-paper cast, "
        "not photorealistic, no 3D render, no watercolour, no glossy anime highlights, no neon, no chibi, 16:9"
    ),
    # Cleaner and more premium: thinner lines, softer rendering, closer to a
    # Korean webtoon. Reads as higher production value, slightly less warm.
    "webtoon": (
        "Korean-style realistic webtoon panel, contemporary Chinese everyday setting, semi-realistic adult faces "
        "with natural proportions and subtle expressive acting, thin clean tapered ink linework, smooth soft "
        "cel shading with gentle gradients, restrained natural palette of warm neutrals and desaturated blues, "
        "soft diffused daylight, shallow depth with a simply rendered background, polished digital illustration, "
        "not photorealistic, no 3D render, no watercolour, no heavy outlines, no chibi, 16:9"
    ),
    # The previous default: saturated, high-contrast, cinematic.
    "cinematic": (
        "Chinese social-realism manhua panel, mature narrative webcomic aesthetic, contemporary Chinese everyday "
        "setting, semi-realistic adult faces and believable human anatomy, strongly expressive facial emotions and "
        "dramatic but natural body language, bold crisp black ink outlines with varied line weight, flat color fills "
        "with hard-edged cel shading, subtle printed-comic texture on skin and clothing, saturated deep navy blue "
        "contrasted with muted warm earth tones, high-contrast cinematic lighting, simple clean interior or urban "
        "background with clear spatial depth, polished 2D illustration, not photorealistic, no 3D render, "
        "no watercolor, no soft pastel anime, no chibi, 16:9"
    ),
}
DEFAULT_STYLE_PRESET = "story"

MAX_COPY_CHARACTERS = 1800
DEFAULT_SCENE_CHARACTERS = 22
MIN_SCENE_CHARACTERS = 8
DEFAULT_IMAGE_CONCURRENCY = 3
MAX_IMAGE_CONCURRENCY = 8
MANIFEST_VERSION = 4

NARRATION_SUBTITLE_TRACK = "narration_subtitles"
TITLE_OVERLAY_TRACK = "title_overlay"

DEFAULT_NARRATION_SUBTITLE_Y = -700
DEFAULT_TITLE_Y = 520

# Camera moves, cycled one per scene, described as a *direction* rather than
# as fixed endpoints: (zoom, pan_x, pan_y), each -1 / 0 / +1.
#
# How far the move actually travels is derived from the shot's length, so a
# 1.6s shot and a 6.2s shot move at the same perceived speed. Fixed endpoints
# meant the same 12% push read as a fast zoom on a short shot and as no motion
# at all on a long one.
KEN_BURNS_MOVES = (
    (+1, 0, 0),    # push in
    (-1, 0, 0),    # pull out
    (0, +1, 0),    # pan right
    (+1, -1, 0),   # push in while drifting left
    (+1, 0, -1),   # push in with a slight tilt down
)
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

TITLE_PRESETS = {
    # name: (fill rgb, border rgb)
    # Warm off-white on dark brown; sits inside the muted "story" palette
    # instead of fighting it the way pure red does.
    "paper": ((0.97, 0.94, 0.88), (0.16, 0.12, 0.10)),
    "red": ((0.92, 0.12, 0.12), (1.0, 1.0, 1.0)),
    "white": ((1.0, 1.0, 1.0), (0.92, 0.12, 0.12)),
    "gold": ((1.0, 0.82, 0.12), (0.10, 0.10, 0.10)),
}
DEFAULT_TITLE_STYLE = "paper"

# Keys read from .env rather than the process environment, for --check-config.
_ENV_FROM_FILE: set[str] = set()


@dataclass
class Character:
    """A recurring person, described once and reused verbatim in every prompt."""

    id: str
    desc: str


@dataclass
class Scene:
    text: str
    image_prompt: str
    cast: list[str] = field(default_factory=list)
    audio_path: str | None = None
    image_path: str | None = None
    duration_us: int | None = None


SCENE_FIELDS = ("text", "image_prompt", "cast", "audio_path", "image_path", "duration_us")


# ------------------------------------------------------------ environment ----

def load_env() -> None:
    """Read .env into the process environment.

    Existing process variables win, matching the previous behaviour. The keys
    that actually came from the file are recorded so --check-config can show
    where each setting was resolved from -- a stale shell variable silently
    shadowing .env is otherwise very hard to notice.
    """
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = _parse_env_value(value.strip())
        if key not in os.environ:
            _ENV_FROM_FILE.add(key)
        os.environ.setdefault(key, value)


def _parse_env_value(value: str) -> str:
    """Strip surrounding quotes, or an unquoted trailing ` # comment`."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    if value and value[0] in "\"'":
        closing = value.find(value[0], 1)
        if closing > 0:
            return value[1:closing]
    comment = re.search(r"\s+#", value)
    return value[: comment.start()].rstrip() if comment else value


def env_source(name: str) -> str:
    if name in _ENV_FROM_FILE:
        return ".env"
    return "environment" if os.getenv(name, "").strip() else "default"


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing {name}; set it in .env.")
    return value


def env_value(name: str, default: str) -> str:
    return os.getenv(name, "").strip() or default


def env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be one of 1/0/true/false/yes/no/on/off, not {raw!r}.")


def positive_env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name, str(default)).strip() or str(default)
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, not {raw!r}.") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be at least {minimum}.")
    return value


def bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip() or str(default)
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, not {raw!r}.") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}.")
    return value


def bounded_env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name, str(default)).strip() or str(default)
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number, not {raw!r}.") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}.")
    return value


def resolve_asset_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def layout_y(pixels: int) -> float:
    """Convert a reference-frame pixel offset to a clamped transform_y."""
    normalized = pixels / LAYOUT_REFERENCE_HALF_HEIGHT
    return max(-SAFE_NORMALIZED_Y, min(SAFE_NORMALIZED_Y, normalized))


# ----------------------------------------------------------------- config ----

@dataclass
class Config:
    """Every setting, parsed exactly once at startup.

    --check-config and a real run both go through Config.load, so the two can
    no longer drift apart.
    """

    # Ark text / storyboard
    ark_api_key: str
    ark_base_url: str
    ark_text_model: str
    scene_characters: int
    scene_length_mode: str

    # Ark image
    ark_image_url: str
    ark_image_model: str
    ark_image_size: str
    ark_image_response_format: str
    ark_image_output_format: str
    ark_image_seed: int | None
    ark_image_cny_per_image: float | None
    image_concurrency: int
    image_style_prompt: str

    # Ark TTS
    ark_tts_url: str
    ark_tts_model: str
    ark_tts_voice_type: str
    ark_tts_speech_rate: int
    ark_tts_loudness_rate: int
    tts_concurrency: int

    # Jianying + assets
    draft_dir: Path
    opening_sound_path: Path
    opening_sound_volume: float
    opening_lead_us: int
    bgm_path: Path | None
    bgm_volume: float
    watermark_path: Path | None

    # Visual layout
    subtitle_y: float
    subtitle_size: float
    subtitle_style: str
    subtitle_border_width: float
    subtitle_letter_spacing: int
    subtitle_max_line_width: float
    subtitle_animation: str
    subtitle_animation_us: int
    title_style: str
    title_y: float
    title_size: float
    title_border_width: float
    title_us: int
    title_animation: str
    ken_burns_rate: float

    @classmethod
    def load(cls) -> Config:
        mode = env_value("SCENE_LENGTH_MODE", "density").lower()
        if mode not in {"density", "quality"}:
            raise RuntimeError("SCENE_LENGTH_MODE must be either density or quality.")

        subtitle_style = env_value("SUBTITLE_STYLE", "outline").lower()
        if subtitle_style not in {"outline", "box"}:
            raise RuntimeError("SUBTITLE_STYLE must be either outline or box.")

        title_style = env_value("TITLE_STYLE", DEFAULT_TITLE_STYLE).lower()
        if title_style not in TITLE_PRESETS:
            options = ", ".join(sorted(TITLE_PRESETS))
            raise RuntimeError(f"TITLE_STYLE must be one of: {options}.")

        style_preset = env_value("IMAGE_STYLE_PRESET", DEFAULT_STYLE_PRESET).lower()
        if style_preset not in STYLE_PRESETS:
            options = ", ".join(sorted(STYLE_PRESETS))
            raise RuntimeError(f"IMAGE_STYLE_PRESET must be one of: {options}.")

        draft_dir = Path(required("JIAN_YING_DRAFT_DIR")).expanduser()
        if not draft_dir.is_dir():
            raise RuntimeError(f"JIAN_YING_DRAFT_DIR does not exist: {draft_dir}")

        seed_raw = os.getenv("ARK_IMAGE_SEED", "").strip()
        price_raw = os.getenv("ARK_IMAGE_CNY_PER_IMAGE", "").strip()

        return cls(
            ark_api_key=required("ARK_API_KEY"),
            ark_base_url=env_value("ARK_BASE_URL", DEFAULT_ARK_BASE_URL).rstrip("/"),
            ark_text_model=env_value("ARK_TEXT_MODEL", DEFAULT_ARK_TEXT_MODEL),
            scene_characters=positive_env_int(
                "SCENE_CHARACTERS_PER_IMAGE", DEFAULT_SCENE_CHARACTERS, minimum=MIN_SCENE_CHARACTERS
            ),
            scene_length_mode=mode,

            ark_image_url=env_value("ARK_IMAGE_URL", DEFAULT_ARK_IMAGE_URL),
            ark_image_model=env_value("ARK_IMAGE_MODEL", DEFAULT_ARK_IMAGE_MODEL),
            ark_image_size=env_value("ARK_IMAGE_SIZE", "2560x1440"),
            ark_image_response_format=env_value("ARK_IMAGE_RESPONSE_FORMAT", "url"),
            ark_image_output_format=env_value("ARK_IMAGE_OUTPUT_FORMAT", "png"),
            ark_image_seed=positive_env_int("ARK_IMAGE_SEED", 0, minimum=0) if seed_raw else None,
            ark_image_cny_per_image=(
                bounded_env_float("ARK_IMAGE_CNY_PER_IMAGE", 0.0, 0.0, 1000.0) if price_raw else None
            ),
            image_concurrency=bounded_env_int(
                "IMAGE_CONCURRENCY", DEFAULT_IMAGE_CONCURRENCY, 1, MAX_IMAGE_CONCURRENCY
            ),
            image_style_prompt=env_value("IMAGE_STYLE_PROMPT", STYLE_PRESETS[style_preset]),

            ark_tts_url=env_value("ARK_TTS_URL", DEFAULT_ARK_TTS_URL),
            ark_tts_model=env_value("ARK_TTS_MODEL", DEFAULT_ARK_TTS_MODEL),
            ark_tts_voice_type=required("ARK_TTS_VOICE_TYPE"),
            ark_tts_speech_rate=bounded_env_int("ARK_TTS_SPEECH_RATE", 0, -50, 100),
            ark_tts_loudness_rate=bounded_env_int("ARK_TTS_LOUDNESS_RATE", 0, -50, 100),
            tts_concurrency=bounded_env_int("TTS_CONCURRENCY", 3, 1, MAX_IMAGE_CONCURRENCY),

            draft_dir=draft_dir,
            opening_sound_path=_require_asset(
                "OPENING_SOUND_PATH", DEFAULT_OPENING_SOUND_PATH, {".mp3", ".wav"}
            ),
            opening_sound_volume=bounded_env_float("OPENING_SOUND_VOLUME", 0.7, 0.0, 2.0),
            opening_lead_us=round(bounded_env_float("OPENING_LEAD_SECONDS", 0.8, 0.0, 5.0) * 1_000_000),
            bgm_path=_optional_asset("BGM_PATH", {".mp3", ".wav"}),
            bgm_volume=bounded_env_float("BGM_VOLUME", 0.10, 0.0, 1.0),
            watermark_path=_optional_asset("WATERMARK_PATH", {".png", ".jpg", ".jpeg"}),

            subtitle_y=layout_y(bounded_env_int(
                "NARRATION_SUBTITLE_Y", DEFAULT_NARRATION_SUBTITLE_Y,
                -LAYOUT_REFERENCE_HALF_HEIGHT, LAYOUT_REFERENCE_HALF_HEIGHT,
            )),
            subtitle_size=bounded_env_float("NARRATION_SUBTITLE_SIZE", 8.0, 1.0, 30.0),
            subtitle_style=subtitle_style,
            subtitle_border_width=bounded_env_float("SUBTITLE_BORDER_WIDTH", 24.0, 0.0, 40.0),
            subtitle_letter_spacing=bounded_env_int("SUBTITLE_LETTER_SPACING", 2, 0, 20),
            subtitle_max_line_width=bounded_env_float("SUBTITLE_MAX_LINE_WIDTH", 0.88, 0.4, 1.0),
            subtitle_animation=env_value("SUBTITLE_ANIMATION", "向上擦除"),
            subtitle_animation_us=round(
                bounded_env_float("SUBTITLE_ANIMATION_SECONDS", 0.3, 0.0, 3.0) * 1_000_000
            ),
            title_style=title_style,
            title_y=layout_y(bounded_env_int(
                "TITLE_Y", DEFAULT_TITLE_Y,
                -LAYOUT_REFERENCE_HALF_HEIGHT, LAYOUT_REFERENCE_HALF_HEIGHT,
            )),
            title_size=bounded_env_float("TITLE_SIZE", 14.0, 1.0, 30.0),
            title_border_width=bounded_env_float("TITLE_BORDER_WIDTH", 28.0, 0.0, 40.0),
            title_us=round(bounded_env_float("TITLE_SECONDS", 3.0, 0.5, 15.0) * 1_000_000),
            title_animation=env_value("TITLE_ANIMATION", "冲屏位移"),
            # 0 disables the camera move entirely; KEN_BURNS=0 still works.
            ken_burns_rate=(
                bounded_env_float("KEN_BURNS_RATE", DEFAULT_KEN_BURNS_RATE, 0.0, 0.2)
                if env_flag("KEN_BURNS", True) else 0.0
            ),
        )


def _require_asset(setting: str, default: str, suffixes: set[str]) -> Path:
    path = resolve_asset_path(env_value(setting, default))
    if not path.is_file():
        raise RuntimeError(f"{setting} does not exist: {path}")
    if path.suffix.lower() not in suffixes:
        raise RuntimeError(f"{setting} must use one of: {', '.join(sorted(suffixes))}.")
    return path


def _optional_asset(setting: str, suffixes: set[str]) -> Path | None:
    raw = os.getenv(setting, "").strip()
    if not raw:
        return None
    return _require_asset(setting, raw, suffixes)


def describe_configuration(cfg: Config) -> None:
    """Print the settings whose resolved value is easy to get wrong."""
    subtitle_px = round(cfg.subtitle_y * CANVAS_HALF_HEIGHT)
    title_px = round(cfg.title_y * CANVAS_HALF_HEIGHT)
    print(f"Canvas: {CANVAS_WIDTH}x{CANVAS_HEIGHT} @30fps (landscape)")
    print(f"Art style: {env_value('IMAGE_STYLE_PRESET', DEFAULT_STYLE_PRESET)}"
          f"{' (overridden by IMAGE_STYLE_PROMPT)' if os.getenv('IMAGE_STYLE_PROMPT', '').strip() else ''}")
    print(
        f"Subtitle Y: {os.getenv('NARRATION_SUBTITLE_Y', DEFAULT_NARRATION_SUBTITLE_Y)} "
        f"-> transform_y {cfg.subtitle_y:.3f} -> {abs(subtitle_px)} px "
        f"{'below' if subtitle_px < 0 else 'above'} centre "
        f"({round(CANVAS_HALF_HEIGHT + subtitle_px)} px from the bottom edge)"
    )
    print(f"Title Y:    transform_y {cfg.title_y:.3f} ({round(CANVAS_HALF_HEIGHT - title_px)} px from the top edge)")
    if cfg.bgm_volume > 0:
        print(f"BGM volume: {cfg.bgm_volume:.2f} linear ({20 * math.log10(cfg.bgm_volume):.1f} dB)")
    else:
        print("BGM volume: muted")
    for name in ("ARK_API_KEY", "ARK_TTS_VOICE_TYPE", "JIAN_YING_DRAFT_DIR",
                 "NARRATION_SUBTITLE_Y", "IMAGE_STYLE_PRESET", "IMAGE_STYLE_PROMPT"):
        print(f"  {name}: {env_source(name)}")


# ------------------------------------------------------------------ http ----

def ensure_ok(response: requests.Response, what: str) -> requests.Response:
    """Raise with the response body attached; a bare status code is useless."""
    if response.status_code >= 400:
        body = response.text.strip().replace("\n", " ")[:500]
        raise RuntimeError(f"{what} failed with HTTP {response.status_code}: {body}")
    return response


def request_with_retry(method: str, url: str, *, retry_on_timeout: bool = True, **kwargs: Any) -> requests.Response:
    kwargs.setdefault("timeout", 60)
    for attempt in range(3):
        try:
            response = requests.request(method, url, **kwargs)
            if response.status_code != 429 and response.status_code < 500:
                return response
            if attempt == 2:
                return response
            wait = retry_after_seconds(response) or (attempt + 1) * 3
            print(f"HTTP {response.status_code}; retrying in {wait}s ({attempt + 1}/3)...", flush=True)
            time.sleep(wait)
            continue
        except (requests.Timeout, requests.ConnectionError) as exc:
            # A timed-out POST may already have been accepted upstream. For
            # endpoints that create a billed task, retrying would pay twice and
            # orphan the first task, so the caller can opt out.
            if not retry_on_timeout:
                raise RuntimeError(
                    f"Network request failed and was not retried (non-idempotent request): {exc}"
                ) from exc
            if attempt == 2:
                raise RuntimeError(f"Network request failed after 3 attempts: {exc}") from exc
            print(f"Network timeout; retrying ({attempt + 1}/3)...", flush=True)
            time.sleep((attempt + 1) * 3)
    raise RuntimeError("Request failed")


def retry_after_seconds(response: requests.Response) -> int | None:
    raw = response.headers.get("Retry-After", "").strip()
    if not raw.isdigit():
        return None
    return min(int(raw), 60)


def post(url: str, **kwargs: Any) -> requests.Response:
    return request_with_retry("POST", url, **kwargs)


def get(url: str, **kwargs: Any) -> requests.Response:
    return request_with_retry("GET", url, **kwargs)


# ------------------------------------------------------------ storyboard ----

def compose_image_prompt(cfg: Config, scene: Scene, characters: list[Character]) -> str:
    style = cfg.image_style_prompt.strip().rstrip(".")
    lookup = {character.id: character.desc for character in characters}
    described = [lookup[cid] for cid in scene.cast if cid in lookup]
    cast_block = ""
    if described:
        cast_block = (
            "Recurring cast, render these exact people with identical face, hair, build and clothing in "
            f"every panel: {'; '.join(described)}. "
        )
    return (
        f"{scene.image_prompt.strip().rstrip('.')}. Usage: one 16:9 Chinese narrative manhua panel matched directly "
        "to this exact subtitle. "
        f"{cast_block}{style}. Depict the concrete moment, people, action, setting, and emotion described by this subtitle. "
        "Include only the people, objects, and surroundings needed to communicate the complete subtitle; keep the composition natural "
        "and narrative, and do not visually overemphasize one incidental detail. Keep all screens, signs, documents, packaging, and "
        "interfaces blank. No visible text, letters, digits, punctuation, "
        "logos, watermarks, subtitles, or fake interface copy."
    )


def scene_text_length_bounds(mode: str, characters_per_scene: int) -> tuple[int, int]:
    """Return configured-density bounds or the fixed quality-first bounds."""
    if mode == "quality":
        return 10, 28
    minimum = max(MIN_SCENE_CHARACTERS, round(characters_per_scene * 0.5))
    maximum = max(minimum + 8, round(characters_per_scene * 1.2))
    return minimum, maximum


def scene_limits(mode: str, characters_per_scene: int, copy: str) -> tuple[int, int]:
    character_count = len(re.sub(r"\s+", "", copy))
    minimum_scene_characters, maximum_scene_characters = scene_text_length_bounds(mode, characters_per_scene)
    target_characters = maximum_scene_characters if mode == "quality" else characters_per_scene
    target = max(6, (character_count + target_characters - 1) // target_characters)
    maximum = min(120, max(24, (character_count + minimum_scene_characters - 1) // minimum_scene_characters))
    return target, maximum


def storyboard_batches(copy: str, batch_size: int = 360) -> list[str]:
    units = [unit.strip() for unit in re.split(r"(?<=[。！？；!?])|\n+", copy) if unit.strip()]
    batches: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}\n{unit}" if current else unit
        if current and len(re.sub(r"\s+", "", candidate)) > batch_size:
            batches.append(current)
            current = unit
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches or [copy]


def storyboard_prompt(cfg: Config, batch_number: int, batch_count: int,
                      batch_target: int, batch_maximum: int,
                      known_characters: list[Character]) -> str:
    minimum_scene_characters, maximum_scene_characters = scene_text_length_bounds(
        cfg.scene_length_mode, cfg.scene_characters
    )
    length_instruction = (
        "Keep each scene 10 to 28 Chinese characters and prioritize clear, focused visuals. "
        if cfg.scene_length_mode == "quality"
        else (
            f"Aim for about {cfg.scene_characters} Chinese characters per scene; keep each scene between "
            f"{minimum_scene_characters} and {maximum_scene_characters} Chinese characters. "
        )
    )
    if known_characters:
        roster = "; ".join(f'{c.id} = {c.desc}' for c in known_characters)
        cast_instruction = (
            "These characters are already established earlier in the same video; reuse their ids and do NOT "
            f"restate or alter their descriptions: {roster}. Only add a new entry to \"characters\" for a "
            "person who does not appear in that list. "
        )
    else:
        cast_instruction = (
            "List every recurring person in \"characters\" with a short stable id and one English description "
            "fixing their apparent age, hair, face, build and clothing. These descriptions are reused verbatim "
            "in every panel, so they must not change. "
        )
    return (
        "You are the storyboard director for a Chinese narration video rendered entirely as a mature social-realism manhua. "
        f"Split this part ({batch_number}/{batch_count}) of the copy into about {batch_target} independent subtitle scenes, "
        f"never more than {batch_maximum}. {length_instruction}Preserve the complete meaning and original order. "
        "One subtitle scene must map to exactly one image. For every scene, write image_prompt as one concise, coherent English "
        "natural-language description of the image that best matches only that scene's Chinese subtitle. First follow the people, "
        "objects, action, location, time, mood, and relationship explicitly present in the subtitle. If the subtitle describes a "
        "concrete event, depict that event literally in a believable everyday setting. If it is abstract, use the simplest human "
        "situation that communicates the whole sentence without changing its meaning. The scene content should feel true to life, "
        "but the rendering must remain a flat Chinese webtoon manhua panel with thick black outlines and soft even "
        "shading, never photography or 3D. Do not force a finance theme. "
        "Never add charts, tables, dashboards, graphs, market arrows, coins, banks, office imagery, or decorative business symbols "
        "unless that exact subtitle genuinely calls for them. Do not visually magnify an incidental word at the expense of the full "
        "sentence. Favor a natural human moment and a clear action over abstract icons or infographic composition. Keep screens, "
        "signs, documents, packaging, and interfaces blank; do not request visible text, letters, digits, punctuation, logos, "
        "watermarks, subtitles, speech bubbles, or fake interface copy. "
        f"{cast_instruction}"
        "Set \"cast\" on each scene to the ids of the characters visible in that panel, or [] if nobody recurring appears. "
        'Return JSON only: {"characters":[{"id":"A","desc":"English description"}],'
        '"scenes":[{"text":"Chinese scene copy","image_prompt":"English image prompt","cast":["A"]}]}'
    )


def parse_storyboard_payload(content: str) -> dict[str, Any]:
    """Parse the model's JSON, tolerating a ```json fence."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("storyboard payload is not a JSON object")
    return data


def scenes_from_payload(data: dict[str, Any]) -> list[Scene]:
    scenes: list[Scene] = []
    for item in data.get("scenes") or []:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        image_prompt = item.get("image_prompt")
        if not isinstance(text, str) or not isinstance(image_prompt, str):
            continue
        if not text.strip() or not image_prompt.strip():
            continue
        raw_cast = item.get("cast")
        cast = [str(cid).strip() for cid in raw_cast if str(cid).strip()] if isinstance(raw_cast, list) else []
        scenes.append(Scene(text=text.strip(), image_prompt=image_prompt.strip(), cast=cast))
    return scenes


def characters_from_payload(data: dict[str, Any]) -> list[Character]:
    characters: list[Character] = []
    for item in data.get("characters") or []:
        if not isinstance(item, dict):
            continue
        cid, desc = item.get("id"), item.get("desc")
        if isinstance(cid, str) and isinstance(desc, str) and cid.strip() and desc.strip():
            characters.append(Character(cid.strip(), desc.strip()))
    return characters


def plan_scenes(cfg: Config, copy: str) -> tuple[list[Scene], list[Character]]:
    target, maximum = scene_limits(cfg.scene_length_mode, cfg.scene_characters, copy)
    batches = storyboard_batches(copy)
    total_characters = max(1, len(re.sub(r"\s+", "", copy)))
    scenes: list[Scene] = []
    characters: list[Character] = []
    report_progress("Storyboard", 0, len(batches))

    for batch_number, batch in enumerate(batches, 1):
        batch_characters = len(re.sub(r"\s+", "", batch))
        batch_target = max(1, round(target * batch_characters / total_characters))
        batch_maximum = min(30, max(6, batch_target + 4))
        prompt = storyboard_prompt(cfg, batch_number, len(batches), batch_target, batch_maximum, characters)
        data = request_storyboard(cfg, prompt, batch, batch_target)

        for character in characters_from_payload(data):
            if character.id not in {existing.id for existing in characters}:
                characters.append(character)
        batch_scenes = scenes_from_payload(data)
        if not batch_scenes:
            raise RuntimeError(f"Storyboard model returned no usable scenes for batch {batch_number}.")
        if len(batch_scenes) > batch_maximum:
            raise RuntimeError(
                f"Storyboard batch {batch_number} returned {len(batch_scenes)} scenes; the safety limit is {batch_maximum}."
            )
        scenes.extend(batch_scenes)
        report_progress("Storyboard", batch_number, len(batches))

    if not scenes:
        raise RuntimeError("Storyboard model returned no scenes.")
    if len(scenes) > maximum:
        raise RuntimeError(f"Storyboard returned {len(scenes)} scenes; the safety limit for this copy is {maximum}.")
    return scenes, characters


def request_storyboard(cfg: Config, prompt: str, batch: str, batch_target: int) -> dict[str, Any]:
    """Call the text model, retrying once at a lower temperature on bad JSON."""
    last_content = ""
    for attempt, temperature in enumerate((0.55, 0.2)):
        response = post(
            f"{cfg.ark_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {cfg.ark_api_key}", "Content-Type": "application/json"},
            json={
                "model": cfg.ark_text_model,
                "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": batch}],
                "temperature": temperature,
                "max_tokens": min(8192, max(1536, batch_target * 200)),
                "response_format": {"type": "json_object"},
            },
            timeout=300,
        )
        ensure_ok(response, "Storyboard request")
        try:
            last_content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise RuntimeError(f"Storyboard response had an unexpected shape: {response.text[:300]}") from exc
        try:
            return parse_storyboard_payload(last_content)
        except (json.JSONDecodeError, ValueError) as exc:
            if attempt == 0:
                print(f"Storyboard JSON was malformed ({exc}); retrying at a lower temperature...", flush=True)
                continue
            raise RuntimeError(
                f"Storyboard model did not return valid JSON ({exc}). "
                f"Raw response starts with: {last_content[:300]!r}"
            ) from exc
    raise RuntimeError("Storyboard request failed")


# --------------------------------------------------------------- progress ----

def report_progress(stage: str, completed: int, total: int) -> None:
    total = max(1, total)
    completed = min(max(0, completed), total)
    width = 28
    filled = round(width * completed / total)
    bar = "#" * filled + "-" * (width - filled)
    ending = "\n" if completed == total else "\r"
    print(f"{stage}: [{bar}] {completed}/{total} ({completed / total:.0%})", end=ending, flush=True)


def print_image_cost_estimate(cfg: Config, minimum_images: int, maximum_images: int,
                              label: str = "Estimated image cost") -> None:
    unit_cost = cfg.ark_image_cny_per_image
    if unit_cost is None:
        print(
            f"{label}: {minimum_images}-{maximum_images} images on {cfg.ark_image_model}; "
            "set ARK_IMAGE_CNY_PER_IMAGE for a price estimate.",
            flush=True,
        )
        return
    print(
        f"{label}: CNY {minimum_images * unit_cost:.2f}-{maximum_images * unit_cost:.2f} "
        f"({minimum_images}-{maximum_images} images x CNY {unit_cost:.2f}/image; model={cfg.ark_image_model}; "
        "image API only, TTS and storyboard-text-model costs excluded)",
        flush=True,
    )


# -------------------------------------------------------------------- tts ----

def synthesize_tts(cfg: Config, text: str, target: Path) -> None:
    request_id = str(uuid.uuid4())
    payload = {
        "user": {"uid": "copy_to_video_draft_generator"},
        "req_params": {
            "text": text,
            "speaker": cfg.ark_tts_voice_type,
            "audio_params": {
                "format": "mp3",
                "sample_rate": 24000,
                "speech_rate": cfg.ark_tts_speech_rate,
                "loudness_rate": cfg.ark_tts_loudness_rate,
            },
        },
    }
    response = post(
        cfg.ark_tts_url,
        headers={
            "X-Api-Key": cfg.ark_api_key,
            "X-Api-Resource-Id": cfg.ark_tts_model,
            "X-Api-Request-Id": request_id,
            "Content-Type": "application/json",
        },
        json=payload,
        stream=True,
        timeout=120,
    )
    ensure_ok(response, "Ark TTS request")
    audio_chunks: list[bytes] = []
    for raw_line in response.iter_lines():
        if not raw_line:
            continue
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
        if line.startswith("data:"):
            line = line[5:].strip()
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Ark TTS returned an invalid stream event: {line[:200]}") from exc
        header = event.get("header") or {}
        code = event.get("code", header.get("code"))
        if code not in (None, 0, 20000000):
            message = event.get("message") or header.get("message") or event
            raise RuntimeError(f"Ark TTS failed ({code}): {message}")
        if event.get("data"):
            audio_chunks.append(base64.b64decode(event["data"]))
    if not audio_chunks:
        raise RuntimeError("Ark TTS returned no audio data.")
    target.write_bytes(b"".join(audio_chunks))


def populate_audio_durations(scenes: list[Scene]) -> None:
    from pyJianYingDraft import AudioMaterial

    for index, scene in enumerate(scenes, 1):
        if not scene.audio_path or not Path(scene.audio_path).is_file():
            raise RuntimeError(f"Missing voice-over audio for scene {index}.")
        scene.duration_us = AudioMaterial(scene.audio_path).duration


# ------------------------------------------------------------------ image ----

def generate_image(cfg: Config, prompt: str, target: Path) -> None:
    payload: dict[str, Any] = {
        "model": cfg.ark_image_model,
        "prompt": prompt,
        "size": cfg.ark_image_size,
        "sequential_image_generation": "disabled",
        "response_format": cfg.ark_image_response_format,
        "output_format": cfg.ark_image_output_format,
        "watermark": False,
    }
    if cfg.ark_image_seed is not None:
        payload["seed"] = cfg.ark_image_seed
    response = post(
        cfg.ark_image_url,
        headers={"Authorization": f"Bearer {cfg.ark_api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=300,
    )
    ensure_ok(response, "Image generation")
    body = response.json()
    images = body.get("data") or body.get("images") or []
    if not images:
        raise RuntimeError(f"Image API returned no image data: {body}")
    image = images[0]
    if image.get("b64_json"):
        target.write_bytes(base64.b64decode(image["b64_json"]))
        return
    image_url = image.get("url")
    if not image_url:
        raise RuntimeError(f"Image API returned an image without data or URL: {image}")
    download = ensure_ok(get(image_url, timeout=300), "Image download")
    target.write_bytes(download.content)


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


def validate_draft_target(cfg: Config, draft_name: str, replace: bool) -> None:
    if (cfg.draft_dir / draft_name).exists() and not replace:
        raise RuntimeError(
            f"Draft already exists: {cfg.draft_dir / draft_name}. "
            "Use a new --draft-name, or add --replace to overwrite it. "
            "Overwriting deletes the whole draft folder, including edits made in Jianying."
        )


def build_draft(cfg: Config, scenes: list[Scene], draft_name: str, replace: bool, title: str) -> Path:
    from pyJianYingDraft import (
        AudioMaterial,
        AudioSegment,
        ClipSettings,
        DraftFolder,
        FontType,
        KeyframeProperty,
        TextBackground,
        TextBorder,
        TextIntro,
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
    title_overlay_track = draft.append_track(TrackSpec(TrackType.text, TITLE_OVERLAY_TRACK))
    bgm_material = AudioMaterial(str(cfg.bgm_path)) if cfg.bgm_path else None
    bgm_track = draft.append_track(TrackSpec(TrackType.audio, "BGM")) if bgm_material else None

    subtitle_font = FontType["特黑体"]
    if cfg.subtitle_style == "box":
        subtitle_colour, subtitle_border, subtitle_background = (
            (0.0, 0.0, 0.0),
            TextBorder(color=(1.0, 1.0, 1.0), width=cfg.subtitle_border_width),
            TextBackground(color="#000000", alpha=0.3, round_radius=0.2, height=0.14,
                           width=0.14, horizontal_offset=0.5, vertical_offset=0.5),
        )
    else:
        # White fill on a black stroke, plus a soft shadow: the standard
        # short-form caption treatment, and legible over any panel. The stroke
        # is deliberately below the maximum -- a 40-wide outline muddies the
        # flat pale panels the "story" art direction produces.
        subtitle_colour, subtitle_border, subtitle_background = (
            (1.0, 1.0, 1.0),
            TextBorder(color=(0.0, 0.0, 0.0), width=cfg.subtitle_border_width),
            None,
        )

    cursor = cfg.opening_lead_us
    for index, scene in enumerate(scenes, 1):
        audio = AudioMaterial(scene.audio_path or "")
        narration_range = Timerange(cursor, audio.duration)
        # The first still is on screen from frame zero so the opening sound
        # effect and title do not play over black.
        visual_start = 0 if index == 1 else cursor
        visual_duration = audio.duration + (cfg.opening_lead_us if index == 1 else 0)

        # One image, one uninterrupted segment. Scenes are never split, and the
        # cut into the next scene is hard: no transition, no intro animation.
        # All of the motion comes from the keyframed camera move.
        video = VideoSegment(scene.image_path or "", Timerange(visual_start, visual_duration))
        if cfg.ken_burns_rate > 0:
            move = KEN_BURNS_MOVES[(index - 1) % len(KEN_BURNS_MOVES)]
            apply_ken_burns(video, KeyframeProperty, visual_duration, move, cfg.ken_burns_rate)
        draft.add_segment(video, video_track)

        subtitle = TextSegment(
            scene.text, narration_range,
            font=subtitle_font,
            style=TextStyle(size=cfg.subtitle_size, color=subtitle_colour, align=1,
                            letter_spacing=cfg.subtitle_letter_spacing,
                            auto_wrapping=True, max_line_width=cfg.subtitle_max_line_width),
            clip_settings=ClipSettings(transform_x=0.0, transform_y=cfg.subtitle_y),
            border=subtitle_border,
            background=subtitle_background,
            shadow=TextShadow(alpha=0.7, diffuse=25.0, distance=8.0, angle=-90.0),
        )
        if subtitle_intro is not None:
            subtitle.add_animation(subtitle_intro, duration=cfg.subtitle_animation_us)
        draft.add_segment(subtitle, narration_subtitle_track)

        draft.add_segment(AudioSegment(audio, narration_range), audio_track)
        scene.duration_us = audio.duration
        cursor += audio.duration

    total_duration = cursor
    add_opening_sound(cfg, draft, opening_sfx_track, total_duration)
    if watermark_track is not None and cfg.watermark_path:
        watermark = VideoSegment(
            str(cfg.watermark_path), Timerange(0, total_duration),
            clip_settings=ClipSettings(scale_x=1.0, scale_y=1.0, transform_x=0.0, transform_y=0.0),
        )
        draft.add_segment(watermark, watermark_track)

    add_title(cfg, draft, title_overlay_track, title, total_duration, title_intro, subtitle_font)
    if bgm_material is not None and bgm_track is not None:
        add_bgm(cfg, draft, bgm_track, bgm_material, total_duration)

    draft.save()
    return cfg.draft_dir / draft_name


def add_opening_sound(cfg: Config, draft: Any, track: Any, total_duration: int) -> None:
    from pyJianYingDraft import AudioMaterial, AudioSegment, Timerange

    material = AudioMaterial(str(cfg.opening_sound_path))
    duration = min(material.duration, total_duration)
    if duration <= 0:
        return
    segment = AudioSegment(material, Timerange(0, duration),
                           source_timerange=Timerange(0, duration), volume=cfg.opening_sound_volume)
    segment.add_fade(0, min(200_000, duration // 2))
    draft.add_segment(segment, track)


def add_title(cfg: Config, draft: Any, track: Any, title: str, total_duration: int,
              title_intro: Any, font: Any) -> None:
    from pyJianYingDraft import ClipSettings, TextBorder, TextSegment, TextShadow, TextStyle, Timerange

    duration = min(total_duration, cfg.title_us)
    if duration <= 0 or not title.strip():
        return
    fill, border = TITLE_PRESETS[cfg.title_style]
    segment = TextSegment(
        title, Timerange(0, duration),
        font=font,
        style=TextStyle(size=cfg.title_size, bold=True, color=fill, align=1,
                        letter_spacing=cfg.subtitle_letter_spacing, auto_wrapping=True),
        border=TextBorder(color=border, width=cfg.title_border_width),
        shadow=TextShadow(alpha=0.75, diffuse=25.0, distance=10.0, angle=-90.0),
        clip_settings=ClipSettings(transform_x=0.0, transform_y=cfg.title_y),
    )
    if title_intro is not None:
        segment.add_animation(title_intro, duration=min(400_000, duration))
    draft.add_segment(segment, track)


def bgm_loop_plan(total_duration: int, material_duration: int) -> list[tuple[int, int, int, int]]:
    """Lay out the looped BGM as (start, duration, fade_in, fade_out).

    The previous version only faded at loop seams, so a track long enough to
    cover the whole video got no fades at all, and every video ended with the
    music cut off mid-note.
    """
    if total_duration <= 0 or material_duration <= 0:
        return []
    plan: list[tuple[int, int, int, int]] = []
    cursor = 0
    while cursor < total_duration:
        duration = min(material_duration, total_duration - cursor)
        if duration <= 0:
            break
        seam = min(400_000, duration // 2)
        fade_in = 600_000 if cursor == 0 else seam
        fade_out = 900_000 if cursor + duration >= total_duration else seam
        half = max(0, duration // 2)
        plan.append((cursor, duration, min(fade_in, half), min(fade_out, half)))
        cursor += duration
    return plan


def add_bgm(cfg: Config, draft: Any, track: Any, material: Any, total_duration: int) -> None:
    """Loop the BGM under the narration, fading in at the head and out at the tail."""
    from pyJianYingDraft import AudioSegment, Timerange

    if cfg.bgm_volume <= 0:
        return
    for start, duration, fade_in, fade_out in bgm_loop_plan(total_duration, material.duration):
        segment = AudioSegment(material, Timerange(start, duration),
                               source_timerange=Timerange(0, duration), volume=cfg.bgm_volume)
        segment.add_fade(fade_in, fade_out)
        draft.add_segment(segment, track)


# ----------------------------------------------------------------- state ----

def save_run_state(asset_root: Path, draft_name: str, title: str, copy: str, scenes: list[Scene],
                   characters: list[Character], status: str, failures: list[dict[str, Any]]) -> None:
    state = {
        "version": MANIFEST_VERSION,
        "draft_name": draft_name,
        "title": title,
        "copy": copy,
        "status": status,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "characters": [asdict(character) for character in characters],
        "scenes": [asdict(scene) for scene in scenes],
        "failures": failures,
    }
    (asset_root / "manifest.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    (asset_root / "failures.json").write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")


def append_run_log(asset_root: Path, event: str, **details: Any) -> None:
    record = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event, **details}
    with (asset_root / "run.log").open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(record, ensure_ascii=False) + "\n")


def scenes_from_manifest(state: dict[str, Any]) -> list[Scene]:
    scenes: list[Scene] = []
    for item in state.get("scenes", []):
        values = {key: item.get(key) for key in SCENE_FIELDS}
        values["cast"] = values.get("cast") or []
        scenes.append(Scene(**values))
    return scenes


# ------------------------------------------------------------------ main ----

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a Jianying draft from copy.")
    source = parser.add_mutually_exclusive_group(required=False)
    source.add_argument("--text")
    source.add_argument("--input", type=Path)
    parser.add_argument("--title", help="Title overlay; defaults to the first line of the copy.")
    parser.add_argument("--draft-name")
    parser.add_argument("--resume", metavar="DRAFT_NAME", help="Resume a failed run stored under output/DRAFT_NAME.")
    parser.add_argument("--replace", action="store_true",
                        help="Overwrite an existing Jianying draft. This deletes the whole draft folder.")
    parser.add_argument("--check-config", action="store_true",
                        help="Validate local configuration and assets without API calls.")
    parser.add_argument("--plan-only", dest="plan_only", action="store_true",
                        help="Generate and print a storyboard only; this still calls the storyboard API.")
    parser.add_argument("--verbose", action="store_true", help="Print a full traceback on failure.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    load_env()
    cfg = Config.load()
    if args.check_config:
        print("Configuration OK")
        describe_configuration(cfg)
        return 0

    failures: list[dict[str, Any]] = []
    characters: list[Character] = []

    if args.resume:
        draft_name = args.resume
        asset_root = ROOT / "output" / draft_name
        manifest_path = asset_root / "manifest.json"
        if not manifest_path.is_file():
            raise RuntimeError(f"Resume manifest does not exist: {manifest_path}")
        state = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if int(state.get("version", 0)) < MANIFEST_VERSION:
            print(
                f"Manifest was written by an older version (v{state.get('version')}); "
                "resuming, but fields it no longer uses are ignored.",
                flush=True,
            )
        copy = state.get("copy", "")
        if not copy:
            raise RuntimeError("Resume manifest has no source copy; start a new run instead.")
        title = args.title or state.get("title") or next(
            (line.strip() for line in copy.splitlines() if line.strip()), copy.strip()
        )
        scenes = scenes_from_manifest(state)
        if not scenes:
            raise RuntimeError("Resume manifest has no scenes.")
        characters = [
            Character(str(item["id"]), str(item["desc"]))
            for item in state.get("characters", [])
            if isinstance(item, dict) and item.get("id") and item.get("desc")
        ]
        failures = state.get("failures", [])
        # A resume used to overwrite the draft unconditionally, which deletes
        # any edits already made in Jianying. It now needs --replace like any
        # other run.
        validate_draft_target(cfg, draft_name, args.replace)
        append_run_log(asset_root, "resume_started", scene_count=len(scenes))
    else:
        if not args.text and not args.input:
            parser.error("one of --text or --input is required unless --resume is used")
        copy = args.text or args.input.read_text(encoding="utf-8")
        copy_character_count = len(re.sub(r"\s+", "", copy))
        if copy_character_count < 10:
            raise RuntimeError("Copy is too short.")
        if copy_character_count > MAX_COPY_CHARACTERS:
            raise RuntimeError(f"Copy is {copy_character_count} characters; the current maximum is {MAX_COPY_CHARACTERS}.")
        title = args.title or next((line.strip() for line in copy.splitlines() if line.strip()), copy.strip())
        draft_name = args.draft_name or time.strftime("auto_video_%Y%m%d_%H%M%S")
        validate_draft_target(cfg, draft_name, args.replace)
        asset_root = ROOT / "output" / draft_name
        asset_root.mkdir(parents=True, exist_ok=True)
        target_scenes, maximum_scenes = scene_limits(cfg.scene_length_mode, cfg.scene_characters, copy)
        print_image_cost_estimate(cfg, target_scenes, maximum_scenes)
        append_run_log(asset_root, "storyboard_started")
        scenes, characters = plan_scenes(cfg, copy)
        append_run_log(asset_root, "storyboard_completed", scene_count=len(scenes),
                       character_count=len(characters))
        save_run_state(asset_root, draft_name, title, copy, scenes, characters, "planned", failures)

    if args.plan_only:
        print(json.dumps(
            {"draft_name": draft_name,
             "characters": [asdict(character) for character in characters],
             "scenes": [asdict(scene) for scene in scenes]},
            ensure_ascii=False, indent=2,
        ))
        return 0

    audio_dir, image_dir = asset_root / "audio", asset_root / "images"
    audio_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)
    for index, scene in enumerate(scenes, 1):
        adopt_existing_asset(scene, "audio_path", audio_dir / f"{index:02d}.mp3")
        adopt_existing_asset(scene, "image_path", image_dir / f"{index:02d}.png")
    save_run_state(asset_root, draft_name, title, copy, scenes, characters, "reconciled", failures)
    append_run_log(asset_root, "assets_reconciled")

    def make_tts(index: int, scene: Scene) -> tuple[int, Path]:
        audio_path = audio_dir / f"{index:02d}.mp3"
        synthesize_tts(cfg, scene.text, audio_path)
        return index, audio_path

    pending_tts = [(index, scene) for index, scene in enumerate(scenes, 1)
                   if not scene.audio_path or not Path(scene.audio_path).is_file()]
    if pending_tts:
        append_run_log(asset_root, "tts_started", count=len(pending_tts), workers=cfg.tts_concurrency)
        completed_tts = 0
        report_progress("Voice-over", 0, len(pending_tts))
        with ThreadPoolExecutor(max_workers=min(cfg.tts_concurrency, len(pending_tts))) as executor:
            futures = {executor.submit(make_tts, index, scene): index for index, scene in pending_tts}
            for future in as_completed(futures):
                index = futures[future]
                try:
                    _, audio_path = future.result()
                except Exception as exc:
                    failure = {"stage": "tts", "scene": index, "error": str(exc)}
                    failures.append(failure)
                    save_run_state(asset_root, draft_name, title, copy, scenes, characters, "failed", failures)
                    append_run_log(asset_root, "tts_failed", **failure)
                    raise
                scenes[index - 1].audio_path = str(audio_path.resolve())
                save_run_state(asset_root, draft_name, title, copy, scenes, characters, "tts_in_progress", failures)
                append_run_log(asset_root, "tts_completed", scene=index)
                completed_tts += 1
                report_progress("Voice-over", completed_tts, len(pending_tts))

    populate_audio_durations(scenes)
    save_run_state(asset_root, draft_name, title, copy, scenes, characters, "audio_durations_ready", failures)

    pending_images = [(index, scene) for index, scene in enumerate(scenes, 1)
                      if not scene.image_path or not Path(scene.image_path).is_file()]
    if args.resume:
        print_image_cost_estimate(cfg, len(pending_images), len(pending_images), "Estimated remaining image cost")

    def make_image(index: int, scene: Scene) -> tuple[int, Path]:
        image_path = image_dir / f"{index:02d}.png"
        generate_image(cfg, compose_image_prompt(cfg, scene, characters), image_path)
        return index, image_path

    if pending_images:
        active_image_workers = min(cfg.image_concurrency, len(pending_images))
        append_run_log(asset_root, "images_started", count=len(pending_images), workers=active_image_workers)
        report_progress("Images", 0, len(pending_images))
        finished_images = 0
        current_image_failures: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=active_image_workers, thread_name_prefix="ark-image") as executor:
            futures = {}
            for index, scene in pending_images:
                append_run_log(asset_root, "image_started", scene=index)
                futures[executor.submit(make_image, index, scene)] = index

            for future in as_completed(futures):
                index = futures[future]
                try:
                    _, image_path = future.result()
                except Exception as exc:
                    failure = {"stage": "image", "scene": index, "error": str(exc)}
                    failures.append(failure)
                    current_image_failures.append(failure)
                    save_run_state(asset_root, draft_name, title, copy, scenes, characters, "failed", failures)
                    append_run_log(asset_root, "image_failed", **failure)
                else:
                    scenes[index - 1].image_path = str(image_path.resolve())
                    save_run_state(asset_root, draft_name, title, copy, scenes, characters, "image_in_progress", failures)
                    append_run_log(asset_root, "image_completed", scene=index)
                finally:
                    finished_images += 1
                    report_progress("Images", finished_images, len(pending_images))

        if current_image_failures:
            save_run_state(asset_root, draft_name, title, copy, scenes, characters, "failed", failures)
            first_failure = current_image_failures[0]
            raise RuntimeError(
                f"{len(current_image_failures)} image(s) failed; successful images were kept for --resume. "
                f"First failure: scene {first_failure['scene']}: {first_failure['error']}"
            )

    report_progress("Draft", 0, 1)
    try:
        draft_path = build_draft(cfg, scenes, draft_name, args.replace, title)
    except Exception as exc:
        failure = {"stage": "draft", "error": str(exc)}
        failures.append(failure)
        save_run_state(asset_root, draft_name, title, copy, scenes, characters, "failed", failures)
        append_run_log(asset_root, "draft_failed", **failure)
        raise
    report_progress("Draft", 1, 1)
    failures = []
    save_run_state(asset_root, draft_name, title, copy, scenes, characters, "completed", failures)
    append_run_log(asset_root, "completed", draft_path=str(draft_path))
    print(f"Done: {draft_path}")
    return 0


def adopt_existing_asset(scene: Scene, attribute: str, path: Path) -> None:
    if getattr(scene, attribute):
        return
    if path.is_file() and path.stat().st_size > 0:
        setattr(scene, attribute, str(path.resolve()))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted; finished assets were kept. Re-run with --resume to continue.", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        if "--verbose" in sys.argv:
            traceback.print_exc()
        print(f"Failed: {exc}", file=sys.stderr)
        sys.exit(1)

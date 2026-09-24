"""Every setting, parsed once - and the global speed they are all measured against."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path

from .bgm import BGM_SUFFIXES, DEFAULT_BGM_LIBRARY, DEFAULT_BGM_LIFT_VOLUME, DEFAULT_BGM_VOLUME, bgm_library
from .camera import DEFAULT_KEN_BURNS_RATE
from .env import (
    ConfigProblems,
    bounded_env_float,
    bounded_env_int,
    env_choice,
    env_flag,
    env_source,
    env_value,
    positive_env_int,
    required,
    resolve_asset_path,
)
from .images import IMAGE_REFERENCE_MODES
from .jianying import resolve_draft_dir
from .layout import (
    CANVAS_HALF_HEIGHT,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DEFAULT_NARRATION_SUBTITLE_Y,
    DEFAULT_SUBTITLE_BORDER_WIDTH,
    DEFAULT_SUBTITLE_EM_PX,
    DEFAULT_SUBTITLE_FONT,
    DEFAULT_SUBTITLE_SIZE,
    DEFAULT_TITLE_BORDER_WIDTH,
    DEFAULT_TITLE_FONT,
    DEFAULT_TITLE_MAX_LINE_WIDTH,
    DEFAULT_TITLE_SECONDS,
    DEFAULT_TITLE_SIZE,
    DEFAULT_TITLE_STYLE,
    DEFAULT_TITLE_Y,
    LAYOUT_REFERENCE_HALF_HEIGHT,
    TITLE_PRESETS,
    characters_per_line,
    layout_y,
)
from .models import Scene
from .storyboard import DEFAULT_SCENE_CHARACTERS, MIN_SCENE_CHARACTERS
from .styles import DEFAULT_STYLE_PRESET, STYLE_PRESETS, STYLES_FILE_SETTING, StylePreset, load_style_presets, styles_file_path
from .title import DEFAULT_TITLE_LEAD_SECONDS

DEFAULT_ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/plan/v3"
DEFAULT_ARK_IMAGE_URL = f"{DEFAULT_ARK_BASE_URL}/images/generations"
DEFAULT_ARK_TTS_URL = "https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional"
# The storyboard is structured extraction, not deliberation, so the director
# is always asked to answer without thinking first (STORYBOARD_THINKING). That
# one flag is the difference between a storyboard and a timeout. Measured on
# one six-scene script, same brief, same copy:
#
#                                          thinking on          thinking off
#     Ark           deepseek-v4-flash     no answer in 240s    done in 15s
#     Ark           doubao-seed-2.0-lite  done in 98s          done in 27s
#     DeepSeek API  deepseek-chat         -                    done in  6s
#
# With thinking on, nothing arrives while the model deliberates, and this
# network path drops a connection silent for about 69s - which is what
# "Network request failed after 3 attempts" was. The director goes to
# DeepSeek's own API whenever DEEPSEEK_API_KEY is set, being the fastest of
# the three; without one it stays on Ark with the original default.
DEFAULT_ARK_TEXT_MODEL = "deepseek-v4-flash"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_ARK_IMAGE_MODEL = "doubao-seedream-5.0-lite"
DEFAULT_ARK_TTS_MODEL = "seed-tts-2.0"
DEFAULT_OPENING_SOUND_PATH = "assets/opening_dong.mp3"

# ----------------------------------------------------------- global speed ----
#
# VIDEO_SPEED is one number for the whole video: 1.2 means the 1.0x cut played
# 1.2x faster. 1.0 is the baseline, and every duration in this package, in
# .env and in styles.json is written at 1.0 and means what it says there.
#
# It is deliberately not a voice setting, because the voice is the one thing
# that cannot be sped up on its own. ARK_TTS_SPEECH_RATE reads the copy faster
# and nothing else moves, so the narration arrives early over pictures still
# holding their old length and camera moves still crawling - which reads as a
# dubbing error rather than as a faster video. The rule the whole package
# follows instead is one line:
#
#     a duration divides by speed, a per-second rate multiplies by it,
#     and anything measured in pixels does not move.
#
# Config.load applies it once, when settings become runtime values, so every
# consumer downstream is on the video's clock by construction and no new
# duration can be added that quietly is not. Scene lengths need no scaling at
# all: they are measured from the audio that actually came back, and the audio
# was spoken at this speed.
#
# Music and the opening cue are left alone on purpose. They are cues, not a
# clock; nothing in the picture is timed against them, and the stinger played
# 1.2x is a different sound.
DEFAULT_VIDEO_SPEED = 1.2
BASELINE_VIDEO_SPEED = 1.0
# The bounds are the speech API's own: speech_rate is a percentage offset in
# [-50, 100]. Past them the copy could no longer be spoken at the rate the
# pictures are cut to, and the two would separate again.
MIN_VIDEO_SPEED = 0.5
MAX_VIDEO_SPEED = 2.0

DEFAULT_IMAGE_CONCURRENCY = 3
MAX_IMAGE_CONCURRENCY = 8
# ----------------------------------------------------------------- speed ----

def validate_speed(value: float | str | None) -> float:
    """A usable global speed, or a refusal saying why.

    Unset means the default. Anything else is checked and **rejected** rather
    than quietly pulled into range: a speed outside what the voice can be read
    at would leave the pictures cut to a pace the narration cannot match, which
    is the one thing this setting exists to prevent, so silently building at
    2.0x for someone who asked for 4.0x would be answering a question they did
    not ask.
    """
    if value is None or value == "":
        return DEFAULT_VIDEO_SPEED
    try:
        speed = float(value)
    except (TypeError, ValueError):
        raise RuntimeError(f"VIDEO_SPEED must be a number, not {value!r}.") from None
    if not MIN_VIDEO_SPEED <= speed <= MAX_VIDEO_SPEED:
        raise RuntimeError(
            f"VIDEO_SPEED must be between {MIN_VIDEO_SPEED} and "
            f"{MAX_VIDEO_SPEED}; {speed:g} is outside what the voice can be "
            "read at, so the picture and the narration could not stay together."
        )
    return speed


def speech_rate_for(speed: float, trim: int = 0) -> int:
    """The speech_rate percentage that reads the copy at `speed`.

    `trim` is ARK_TTS_SPEECH_RATE, which stays a per-voice adjustment: a voice
    that reads a shade fast at its natural pace still reads a shade fast at
    1.2x. The two multiply rather than add, so the trim keeps meaning the same
    proportion of the delivery at every speed.

    The result is clamped to the API's own range instead of being rejected,
    because the only way to reach the edge is to combine two settings that are
    each individually legal, and failing a whole run over that would be worse
    than reading at 2.0x.
    """
    combined = speed * (1.0 + trim / 100.0)
    return max(-50, min(100, round((combined - 1.0) * 100)))


def speeds_match(a: float, b: float) -> bool:
    """Whether two speeds would produce the same narration.

    A tolerance, not equality: a speed reaches the service as an integer
    percentage, so 1.200 and 1.2004 are the same reading and re-synthesising
    twenty clips to chase the fourth decimal would be a bill for nothing.
    """
    return speech_rate_for(a) == speech_rate_for(b)


def paced_us(microseconds: int, speed: float) -> int:
    """A baseline duration in timeline time. The one conversion there is.

    Rounded to whole microseconds because that is the unit a Jianying draft is
    written in; a fractional one would be truncated somewhere else instead.
    """
    return round(microseconds / speed)


def drop_stale_narration(scenes: list[Scene], resuming: bool,
                         was: float, now: float) -> bool:
    """Forget narration read at a different speed. Returns whether any was.

    A clip read at another speed is the wrong clip, however well its text
    matches, and it is not enough to forget it in the manifest: the assets on
    disk are adopted by filename on the next run, so the paths have to be
    cleared before that happens.

    The pictures are kept. They have no speed of their own, they are the
    expensive half of a run, and there is nothing wrong with them.
    """
    if not resuming or speeds_match(was, now):
        return False
    for scene in scenes:
        scene.audio_path, scene.duration_us = None, None
    return True


# ----------------------------------------------------------------- config ----

@dataclass
class Config:
    """Every setting, parsed exactly once at startup.

    --check-config and a real run both go through Config.load, so the two can
    no longer drift apart.

    **Every duration here is already on the video's clock.** .env is written at
    the 1.0x baseline - PARAGRAPH_PAUSE_SECONDS=0.5 means 0.5 s in a video running
    at natural pace - and `load` divides by `speed` as it parses, which is the
    same moment it turns every other setting into a runtime value. Doing it
    here rather than at each use is what makes the global speed safe to extend:
    a duration added later is scaled because it came through this class, not
    because whoever added it remembered. `ken_burns_rate` is the one setting
    that multiplies instead, being a rate per second rather than a duration.
    """

    # How fast the whole video runs. 1.0 is the baseline, 1.2 the default.
    speed: float

    # Ark text / storyboard
    ark_api_key: str
    ark_base_url: str
    ark_text_model: str
    # Where the storyboard director is called, resolved once: DeepSeek's own
    # API when DEEPSEEK_API_KEY is set, Ark otherwise. Images and narration
    # stay on Ark either way - they are what the Agent Plan is for.
    text_base_url: str
    text_api_key: str
    text_model: str
    text_provider: str
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
    # A ceiling on the frames one run may draw; None for no ceiling.
    max_images: int | None
    image_concurrency: int
    image_style_prompt: str
    # "anchor" draws one frame first and sends a copy with every other frame.
    image_reference: str
    style_preset: str
    style: StylePreset
    styles_source: str

    # Ark TTS
    ark_tts_url: str
    ark_tts_model: str
    ark_tts_voice_type: str
    ark_tts_speech_rate: int
    ark_tts_loudness_rate: int
    tts_concurrency: int

    # Jianying + assets
    draft_dir: Path
    draft_dir_source: str
    opening_sound_path: Path
    opening_sound_volume: float
    opening_lead_us: int
    speak_title: bool
    title_lead_us: int
    bgm_path: Path | None
    bgm_library: Path | None
    bgm_volume: float
    bgm_lift_volume: float
    bgm_ramp_us: int
    paragraph_pause_us: int
    ending_hold_us: int
    watermark_path: Path | None
    color_grade: str
    color_grade_intensity: float

    # Visual layout
    subtitle_y: float
    subtitle_size: float
    subtitle_font: str
    subtitle_style: str
    subtitle_border_width: float
    subtitle_letter_spacing: int
    subtitle_max_line_width: float
    subtitle_em_px: float
    subtitle_animation: str
    subtitle_animation_us: int
    title_style: str
    # Whether the title's fill ramps character by character, or stays one
    # colour per line. Follows the colourway unless TITLE_COLOR_MODE says.
    title_ramp: bool
    title_font: str
    title_y: float
    title_size: float
    title_border_width: float
    title_max_line_width: float
    title_us: int
    title_animation: str
    title_outro: str
    ken_burns_rate: float

    @classmethod
    def load(cls, speed: float | None = None, purpose: str = "build") -> Config:
        """Every setting, parsed and checked, with every problem reported at once.

        `purpose` is "build" for a run, "check" for --check-config and "plan"
        for --plan-only. A storyboard needs only the text model, so planning
        does not ask for Jianying, a voice or the opening cue - it used to,
        and a storyboard could not be previewed on a machine without the
        editor installed.

        A problem no longer stops the parse. Each setting that fails is
        recorded and given its default so the rest can still be checked, and
        the whole list is raised at the end: a fresh clone used to need three
        rounds of --check-config to learn it wanted a drafts folder, a key
        and a voice, one at a time.
        """
        problems = ConfigProblems()
        bounded_float = problems.or_default(bounded_env_float)
        bounded_int = problems.or_default(bounded_env_int)
        positive_int = problems.or_default(positive_env_int)
        flag = problems.or_default(env_flag)
        media = purpose != "plan"

        def needed(name: str) -> str:
            return problems.check(lambda: required(name), "")

        # First, because most of what follows is measured against it. An
        # explicit argument is --speed on the command line; it wins over .env
        # the way every other flag does.
        speed = problems.check(lambda: validate_speed(
            speed if speed is not None else os.getenv("VIDEO_SPEED", "").strip() or None),
            DEFAULT_VIDEO_SPEED)

        mode = problems.check(lambda: env_choice(
            "SCENE_LENGTH_MODE", "density", {"density", "quality"},
            "SCENE_LENGTH_MODE must be either density or quality."), "density")
        subtitle_style = problems.check(lambda: env_choice(
            "SUBTITLE_STYLE", "plate", {"plate", "outline", "box"},
            "SUBTITLE_STYLE must be one of: plate, outline, box."), "plate")
        image_reference = problems.check(lambda: env_choice(
            "IMAGE_REFERENCE", "off", IMAGE_REFERENCE_MODES,
            f"IMAGE_REFERENCE must be one of: {', '.join(IMAGE_REFERENCE_MODES)}."), "off")

        # The art style is resolved first because it supplies the defaults for
        # the colour grade and the title colourway, which .env then overrides.
        loaded = problems.check(load_style_presets, None)
        style_presets, style_default, styles_source = loaded or (
            dict(STYLE_PRESETS), DEFAULT_STYLE_PRESET, "built-in (the styles file has a problem)")
        style_preset = env_value("IMAGE_STYLE_PRESET", style_default).lower()
        if style_preset not in style_presets:
            # Only worth saying when the styles file itself was read: against
            # the built-ins alone, a preset of your own is bound to look unknown.
            if loaded is not None:
                problems.messages.append(
                    f"IMAGE_STYLE_PRESET must be one of: {', '.join(sorted(style_presets))}. "
                    f"(Presets come from {styles_file_path()}; edit it or point "
                    f"{STYLES_FILE_SETTING} at another file to add your own.)"
                )
            style_preset = style_default if style_default in style_presets else DEFAULT_STYLE_PRESET
        style = style_presets[style_preset]

        title_style = problems.check(lambda: env_choice(
            "TITLE_STYLE", style.title, TITLE_PRESETS,
            f"TITLE_STYLE must be one of: {', '.join(sorted(TITLE_PRESETS))}."), DEFAULT_TITLE_STYLE)
        title_color_mode = problems.check(lambda: env_choice(
            "TITLE_COLOR_MODE", "", {"", "ramp", "lines"},
            "TITLE_COLOR_MODE must be ramp or lines (or empty to follow the colourway)."), "")

        if media:
            draft_dir, draft_dir_source = problems.check(resolve_draft_dir, (Path(), "unresolved"))
        else:
            draft_dir, draft_dir_source = Path(), "not needed to plan"

        ark_base_url = env_value("ARK_BASE_URL", DEFAULT_ARK_BASE_URL).rstrip("/")
        ark_text_model = env_value("ARK_TEXT_MODEL", DEFAULT_ARK_TEXT_MODEL)
        deepseek_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        # Ark draws and speaks, so a run always needs its key; a plan needs it
        # only when the storyboard is written on Ark too.
        ark_api_key = (needed("ARK_API_KEY") if media or not deepseek_key
                       else os.getenv("ARK_API_KEY", "").strip())
        if deepseek_key:
            text_provider = "deepseek"
            text_base_url = env_value("DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL).rstrip("/")
            text_api_key = deepseek_key
            text_model = env_value("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL)
        else:
            text_provider = "ark"
            text_base_url, text_api_key, text_model = ark_base_url, ark_api_key, ark_text_model

        seed_raw = os.getenv("ARK_IMAGE_SEED", "").strip()
        price_raw = os.getenv("ARK_IMAGE_CNY_PER_IMAGE", "").strip()

        config = cls(
            speed=speed,
            ark_api_key=ark_api_key,
            ark_base_url=ark_base_url,
            ark_text_model=ark_text_model,
            text_base_url=text_base_url,
            text_api_key=text_api_key,
            text_model=text_model,
            text_provider=text_provider,
            scene_characters=positive_int(
                "SCENE_CHARACTERS_PER_IMAGE", DEFAULT_SCENE_CHARACTERS, minimum=MIN_SCENE_CHARACTERS
            ),
            scene_length_mode=mode,

            ark_image_url=env_value("ARK_IMAGE_URL", DEFAULT_ARK_IMAGE_URL),
            ark_image_model=env_value("ARK_IMAGE_MODEL", DEFAULT_ARK_IMAGE_MODEL),
            ark_image_size=env_value("ARK_IMAGE_SIZE", "2560x1440"),
            ark_image_response_format=env_value("ARK_IMAGE_RESPONSE_FORMAT", "url"),
            ark_image_output_format=env_value("ARK_IMAGE_OUTPUT_FORMAT", "png"),
            ark_image_seed=positive_int("ARK_IMAGE_SEED", 0, minimum=0) if seed_raw else None,
            ark_image_cny_per_image=(
                bounded_float("ARK_IMAGE_CNY_PER_IMAGE", 0.0, 0.0, 1000.0) if price_raw else None
            ),
            # For a run nobody is watching - an agent's, say - where the first
            # sign of a storyboard that split into sixty scenes would otherwise
            # be the bill. Checked once the storyboard exists and before any
            # narration or picture is paid for.
            max_images=positive_int("MAX_IMAGES", 1, minimum=1) if os.getenv("MAX_IMAGES", "").strip() else None,
            image_concurrency=bounded_int(
                "IMAGE_CONCURRENCY", DEFAULT_IMAGE_CONCURRENCY, 1, MAX_IMAGE_CONCURRENCY
            ),
            image_style_prompt=env_value("IMAGE_STYLE_PROMPT", style.prompt),
            image_reference=image_reference,
            style_preset=style_preset,
            style=style,
            styles_source=styles_source,

            ark_tts_url=env_value("ARK_TTS_URL", DEFAULT_ARK_TTS_URL),
            ark_tts_model=env_value("ARK_TTS_MODEL", DEFAULT_ARK_TTS_MODEL),
            ark_tts_voice_type=needed("ARK_TTS_VOICE_TYPE") if media else os.getenv("ARK_TTS_VOICE_TYPE", "").strip(),
            # The copy is *re-spoken* faster, not resampled afterwards: the
            # service takes a rate, so there is no pitch shift, and the scene
            # lengths that everything else is cut to are still measured from
            # the audio that actually came back.
            ark_tts_speech_rate=speech_rate_for(
                speed, bounded_int("ARK_TTS_SPEECH_RATE", 0, -50, 100)),
            ark_tts_loudness_rate=bounded_int("ARK_TTS_LOUDNESS_RATE", 0, -50, 100),
            tts_concurrency=bounded_int("TTS_CONCURRENCY", 3, 1, MAX_IMAGE_CONCURRENCY),

            draft_dir=draft_dir,
            draft_dir_source=draft_dir_source,
            # Required. The cue is a specific sound these videos are known
            # by; it ships with the repo, and nothing synthesises a stand-in
            # for it, so a missing one is a broken install rather than a
            # missing option.
            opening_sound_path=problems.check(lambda: _require_asset(
                "OPENING_SOUND_PATH", DEFAULT_OPENING_SOUND_PATH, {".mp3", ".wav"}), Path()) if media else Path(),
            # Unity by default: the opening cue plays exactly as supplied. It
            # is the user's own file and is meant to sound the way it sounds.
            opening_sound_volume=bounded_float("OPENING_SOUND_VOLUME", 1.0, 0.0, 2.0),
            opening_lead_us=paced_us(
                round(bounded_float("OPENING_LEAD_SECONDS", 0.8, 0.0, 5.0) * 1_000_000),
                speed),
            speak_title=flag("SPEAK_TITLE", True),
            # Scaled with the rest even though it is measured off the cue's
            # own decay: the title's voice is now 1.2x too, so a lead left at
            # 0.45 s would be a longer share of a shorter opening.
            title_lead_us=paced_us(
                round(bounded_float("TITLE_LEAD_SECONDS", DEFAULT_TITLE_LEAD_SECONDS, 0.0, 3.0)
                      * 1_000_000),
                speed,
            ),
            # An explicit track, which wins over the library when set.
            bgm_path=problems.check(lambda: _optional_asset("BGM_PATH", BGM_SUFFIXES), None) if media else None,
            bgm_library=(problems.check(lambda: _optional_dir("BGM_LIBRARY", DEFAULT_BGM_LIBRARY), None)
                         if media else None),
            # Both levels are quoted as linear gain and both now sit in the
            # 20-25 dB below the voice that a bed under narration wants. The
            # lift used to be 0.20, which is 14 dB down: audibly music rather
            # than atmosphere, and the one thing in the mix loud enough to
            # compete with the line that follows it.
            bgm_volume=bounded_float("BGM_VOLUME", DEFAULT_BGM_VOLUME, 0.0, 1.0),
            bgm_lift_volume=bounded_float("BGM_LIFT_VOLUME", DEFAULT_BGM_LIFT_VOLUME, 0.0, 1.0),
            bgm_ramp_us=paced_us(
                round(bounded_float("BGM_RAMP_SECONDS", 0.25, 0.05, 2.0) * 1_000_000),
                speed),
            paragraph_pause_us=paced_us(
                round(bounded_float("PARAGRAPH_PAUSE_SECONDS", 0.5, 0.0, 3.0) * 1_000_000),
                speed),
            # Zero: the video ends on the last syllable of the last
            # subtitle. It used to hold 1.8s on the closing picture, which
            # reads as the file failing to stop - a feed autoplays the next
            # video over it, and an export carries nearly two seconds of dead
            # air at the end of every upload. Set it if a still close is
            # wanted; nothing else in the timeline depends on it being there.
            ending_hold_us=paced_us(
                round(bounded_float("ENDING_HOLD_SECONDS", 0.0, 0.0, 10.0) * 1_000_000),
                speed),
            watermark_path=(problems.check(lambda: _optional_asset("WATERMARK_PATH", {".png", ".jpg", ".jpeg"}), None)
                            if media else None),
            color_grade=env_value("COLOR_GRADE", style.grade),
            color_grade_intensity=bounded_float("COLOR_GRADE_INTENSITY", 12.0, 0.0, 100.0),

            subtitle_y=layout_y(bounded_int(
                "NARRATION_SUBTITLE_Y", DEFAULT_NARRATION_SUBTITLE_Y,
                -LAYOUT_REFERENCE_HALF_HEIGHT, LAYOUT_REFERENCE_HALF_HEIGHT,
            )),
            subtitle_size=bounded_float("NARRATION_SUBTITLE_SIZE", DEFAULT_SUBTITLE_SIZE, 1.0, 30.0),
            subtitle_font=env_value("SUBTITLE_FONT", DEFAULT_SUBTITLE_FONT),
            subtitle_style=subtitle_style,
            subtitle_border_width=bounded_float(
                "SUBTITLE_BORDER_WIDTH", DEFAULT_SUBTITLE_BORDER_WIDTH, 0.0, 40.0
            ),
            subtitle_letter_spacing=bounded_int("SUBTITLE_LETTER_SPACING", 2, 0, 20),
            subtitle_max_line_width=bounded_float("SUBTITLE_MAX_LINE_WIDTH", 0.88, 0.4, 1.0),
            subtitle_em_px=bounded_float("SUBTITLE_EM_PX", DEFAULT_SUBTITLE_EM_PX, 1.0, 40.0),
            subtitle_animation=env_value("SUBTITLE_ANIMATION", "向上擦除"),
            subtitle_animation_us=paced_us(
                round(bounded_float("SUBTITLE_ANIMATION_SECONDS", 0.3, 0.0, 3.0) * 1_000_000),
                speed,
            ),
            title_style=title_style,
            title_ramp=(TITLE_PRESETS[title_style].ramp if not title_color_mode
                        else title_color_mode == "ramp"),
            title_font=env_value("TITLE_FONT", DEFAULT_TITLE_FONT),
            title_y=layout_y(bounded_int(
                "TITLE_Y", DEFAULT_TITLE_Y,
                -LAYOUT_REFERENCE_HALF_HEIGHT, LAYOUT_REFERENCE_HALF_HEIGHT,
            )),
            title_size=bounded_float("TITLE_SIZE", DEFAULT_TITLE_SIZE, 1.0, 30.0),
            title_border_width=bounded_float(
                "TITLE_BORDER_WIDTH", DEFAULT_TITLE_BORDER_WIDTH, 0.0, 40.0
            ),
            title_max_line_width=bounded_float(
                "TITLE_MAX_LINE_WIDTH", DEFAULT_TITLE_MAX_LINE_WIDTH, 0.4, 1.0
            ),
            title_us=paced_us(
                round(bounded_float("TITLE_SECONDS", DEFAULT_TITLE_SECONDS, 0.5, 15.0)
                      * 1_000_000),
                speed,
            ),
            title_animation=env_value("TITLE_ANIMATION", "缩小"),
            title_outro=env_value("TITLE_OUTRO", "放大"),
            # 0 disables the camera move entirely; KEN_BURNS=0 still works.
            #
            # Multiplied, not divided: this is a fraction of the frame per
            # *second*, and a video played 1.2x faster crosses 1.2x as much of
            # the frame each second. The two changes cancel over a shot - a
            # scene 1.2x shorter at a 1.2x rate travels exactly as far as it
            # did - which is what a camera move looks like when the whole video
            # is simply running faster, rather than a push that has slowed to a
            # crawl underneath quicker narration.
            ken_burns_rate=(
                bounded_float("KEN_BURNS_RATE", DEFAULT_KEN_BURNS_RATE, 0.0, 0.2) * speed
                if flag("KEN_BURNS", True) else 0.0
            ),
        )
        problems.raise_if_any()
        return config

def _require_asset(setting: str, default: str, suffixes: set[str]) -> Path:
    path = resolve_asset_path(env_value(setting, default))
    if not path.is_file():
        raise RuntimeError(f"{setting} does not exist: {path}")
    if path.suffix.lower() not in suffixes:
        raise RuntimeError(f"{setting} must use one of: {', '.join(sorted(suffixes))}.")
    return path


def _asset_if_present(setting: str, default: str, suffixes: set[str]) -> Path | None:
    """The configured asset, or None when it simply is not there.

    An explicitly-set path that does not exist is still an error - a typo in
    .env should be reported, not silently replaced by a generated stand-in.
    Only the *default* is allowed to be absent.
    """
    raw = env_value(setting, default)
    path = resolve_asset_path(raw)
    if path.is_file():
        if path.suffix.lower() not in suffixes:
            raise RuntimeError(f"{setting} must use one of: {', '.join(sorted(suffixes))}.")
        return path
    if os.getenv(setting, "").strip():
        raise RuntimeError(f"{setting} does not exist: {path}")
    return None


def _optional_asset(setting: str, suffixes: set[str]) -> Path | None:
    raw = os.getenv(setting, "").strip()
    if not raw:
        return None
    return _require_asset(setting, raw, suffixes)


def _optional_dir(setting: str, default: str) -> Path | None:
    """A folder of media, which may simply not be there.

    Missing is not an error even when the setting names it: the default is a
    folder the repository does not ship, and a run with no music is a run with
    no music. A path that IS set and is not a directory is a typo worth
    reporting, though - the alternative is a silently music-free video.
    """
    raw = os.getenv(setting, "").strip()
    path = resolve_asset_path(raw or default)
    if path.is_dir():
        return path
    if raw:
        raise RuntimeError(f"{setting} is not a directory: {path}")
    return None


def describe_configuration(cfg: Config) -> None:
    """Print the settings whose resolved value is easy to get wrong."""
    subtitle_px = round(cfg.subtitle_y * CANVAS_HALF_HEIGHT)
    title_px = round(cfg.title_y * CANVAS_HALF_HEIGHT)
    print(f"Canvas: {CANVAS_WIDTH}x{CANVAS_HEIGHT} @30fps (landscape)")
    print(f"Speed:  {cfg.speed:.2f}x"
          + ("  (1.00x is natural pace; everything below is already scaled "
             "to it)" if cfg.speed != BASELINE_VIDEO_SPEED else "")
          + f"  -> voice speech_rate {cfg.ark_tts_speech_rate:+d}%")
    label = f" {cfg.style.label}" if cfg.style.label else ""
    print(f"Art style: {cfg.style_preset}{label}"
          f"{' (overridden by IMAGE_STYLE_PROMPT)' if os.getenv('IMAGE_STYLE_PROMPT', '').strip() else ''}"
          f"  [{cfg.styles_source}]")
    print(f"  rendered as: {cfg.style.medium}")
    print("  reference:   " + ("anchor - one frame is drawn first and every other frame is matched to it "
                               "(experimental)" if cfg.image_reference == "anchor" else "off"))
    print(
        f"Subtitle Y: {os.getenv('NARRATION_SUBTITLE_Y', DEFAULT_NARRATION_SUBTITLE_Y)} "
        f"-> transform_y {cfg.subtitle_y:.3f} -> {abs(subtitle_px)} px "
        f"{'below' if subtitle_px < 0 else 'above'} centre "
        f"({round(CANVAS_HALF_HEIGHT + subtitle_px)} px from the bottom edge)"
    )
    print(f"Title Y:    transform_y {cfg.title_y:.3f} ({round(CANVAS_HALF_HEIGHT - title_px)} px from the top edge)")
    print(f"Caption:    {cfg.subtitle_font} at size {cfg.subtitle_size:g} "
          f"(~{round(cfg.subtitle_size * cfg.subtitle_em_px)} px per character), style {cfg.subtitle_style}")
    limit = characters_per_line(cfg.title_size, cfg.title_max_line_width, cfg.subtitle_em_px)
    print(f"Title:      {cfg.title_font} at size {cfg.title_size:g} "
          f"(~{round(cfg.title_size * cfg.subtitle_em_px)} px per character, {limit} per line), "
          f"colourway {cfg.title_style} ({'ramp' if cfg.title_ramp else 'one colour per line'}), "
          f"{cfg.title_us / 1e6:.1f}s")
    print(f"Opening:    {cfg.opening_sound_path} at {cfg.opening_sound_volume:.2f}, "
          f"copy starts at {cfg.opening_lead_us / 1e6:.2f}s or later")
    print(f"Title voice: {'on' if cfg.speak_title else 'off'}"
          + (f", {cfg.title_lead_us / 1e6:.2f}s after the cue" if cfg.speak_title else ""))
    print(f"Drafts go to: {cfg.draft_dir}"
          + ("  (found automatically)" if cfg.draft_dir_source == "detected"
             else "  (JIAN_YING_DRAFT_DIR)"))
    if cfg.bgm_path is not None:
        print(f"BGM: {cfg.bgm_path}  (BGM_PATH; the library is not consulted)")
    else:
        entries = bgm_library(cfg.bgm_library)
        where = cfg.bgm_library or resolve_asset_path(DEFAULT_BGM_LIBRARY)
        tagged = sum(1 for _path, tag in entries if tag)
        print(f"BGM library: {where}"
              + (f"  {len(entries)} track(s), {tagged} tagged" if entries
                 else "  (not found - the video will have no music)"))
        if entries and not tagged:
            print("  ! no filename starts with a Chinese mood label, so nothing "
                  "can be matched; rename them like 紧张Kill Drill.mp3")
    if cfg.bgm_volume > 0:
        lift = max(cfg.bgm_volume, cfg.bgm_lift_volume)
        print(f"BGM volume: {cfg.bgm_volume:.3f} under speech ({20 * math.log10(cfg.bgm_volume):.1f} dB), "
              f"{lift:.3f} in the gaps ({20 * math.log10(lift):.1f} dB)")
    else:
        print("BGM volume: muted")
    print(f"Pauses:     {cfg.paragraph_pause_us / 1e6:.2f}s after a paragraph, "
          + (f"{cfg.ending_hold_us / 1e6:.2f}s held at the end"
             if cfg.ending_hold_us
             else "ending on the last subtitle"))
    print(f"Colour grade: {cfg.color_grade or 'none'} at {cfg.color_grade_intensity:.0f}%")
    for name in ("ARK_API_KEY", "ARK_TTS_VOICE_TYPE", "JIAN_YING_DRAFT_DIR", "VIDEO_SPEED",
                 "BGM_PATH", "BGM_LIBRARY", "ENDING_HOLD_SECONDS", "SPEAK_TITLE",
                 "NARRATION_SUBTITLE_Y", "NARRATION_SUBTITLE_SIZE", "SUBTITLE_FONT",
                 "TITLE_STYLE", "TITLE_FONT", "TITLE_SIZE", "COLOR_GRADE",
                 "IMAGE_STYLE_PRESET", "IMAGE_STYLE_PROMPT", STYLES_FILE_SETTING):
        print(f"  {name}: {env_source(name)}")



"""Generate a Jianying draft from Chinese copy with Volcengine Ark Agent Plan."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import os
import re
import sys
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import requests
from urllib3.exceptions import NewConnectionError


def _use_utf8_stdout() -> None:
    """Make stdout survive a console that is not UTF-8.

    Windows encodes stdout with the *console* code page, not UTF-8: cp1252
    under Git Bash, cp1252 again under many CI shells. Almost everything this
    program prints is Chinese - style labels, scene text, the progress lines -
    so on such a console the `print` itself raises UnicodeEncodeError.
    `--check-config` died on its second line, and a real run dies partway
    through, after the narration and the images have been paid for. The
    traceback names charmap.py, so it reads as a Python bug rather than a
    terminal setting.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:                  # a pipe, or a captured buffer
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass                                 # printing is not worth aborting over


_use_utf8_stdout()

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
# 1.2x faster. 1.0 is the baseline, and every duration in this file, in .env
# and in styles.json is written at 1.0 and means what it says there.
#
# It is deliberately not a voice setting, because the voice is the one thing
# that cannot be sped up on its own. ARK_TTS_SPEECH_RATE reads the copy faster
# and nothing else moves, so the narration arrives early over pictures still
# holding their old length and camera moves still crawling - which reads as a
# dubbing error rather than as a faster video. The rule the whole file follows
# instead is one line:
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

# The stinger lands first and the title is read over its decay. Measured off
# the cue itself: the impact peaks in its first 0.25 s and is 10 dB down by
# 0.5 s, so a voice starting at 0.45 s speaks into the tail rather than over
# the hit.
DEFAULT_TITLE_LEAD_SECONDS = 0.45
# A breath between the title and the first line of the copy, so the two do not
# run together as one sentence.
TITLE_TAIL_US = 250_000

# The longest the head may hold before the copy starts. The lead grows to fit
# the title's own voice, and nothing stopped that growing without limit: a
# forty-character --title reads for eight seconds, so the video opened on
# nearly nine seconds of title card. This is short-form video; the content has
# to start. A title that will not fit inside this is not a title, it is a
# sentence, and it keeps its type on screen but loses its voice-over.
MAX_OPENING_LEAD_US = 4_000_000

# Whole-video art direction. Nine presets; pick one with IMAGE_STYLE_PRESET.
#
# A preset is more than a prompt. Three other things have to move with it, or
# the video ends up fighting itself:
#
#   medium  the phrase that names the drawing itself. It goes to the storyboard
#           director *and* into every image prompt, so the director stops
#           describing manhua panels when the chosen style is a photograph.
#   avoid   what that medium must never collapse into, in the same voice.
#   grade   the Jianying filter that suits it. A neutral grey pass flattens the
#           navy out of "midnight" and does nothing at all for "noir".
#   title   the opening-title colourway that belongs to the palette.
#
# grade and title are defaults only -- COLOR_GRADE / TITLE_STYLE in .env win.

DEFAULT_STYLE_MEDIUM = "illustrated panel"
DEFAULT_STYLE_AVOID = "photography or 3D rendering"
DEFAULT_COLOR_GRADE = "灰调中性"


@dataclass(frozen=True)
class StylePreset:
    """One complete look: art direction plus everything that must match it."""

    prompt: str
    medium: str = DEFAULT_STYLE_MEDIUM
    avoid: str = DEFAULT_STYLE_AVOID
    grade: str = DEFAULT_COLOR_GRADE
    title: str = "crimson"
    label: str = ""

    @classmethod
    def parse(cls, name: str, value: Any, source: str) -> StylePreset:
        """Accept either a bare prompt string or the full object form.

        A string keeps the pre-9-style files working: only the prompt is
        replaced, and everything else falls back to the built-in of the same
        name, so overriding one preset's wording does not silently drop its
        colour grade and title colourway.
        """
        base = STYLE_PRESETS.get(name)
        if isinstance(value, str):
            prompt, extra = value.strip(), {}
        elif isinstance(value, dict):
            prompt, extra = str(value.get("prompt", "")).strip(), value
        else:
            raise RuntimeError(
                f"{source}: style {name!r} must be a prompt string, or an object with a \"prompt\" key."
            )
        if not prompt:
            raise RuntimeError(f"{source}: style {name!r} needs a non-empty \"prompt\".")

        def pick(field_name: str, default: str) -> str:
            chosen = str(extra.get(field_name) or "").strip()
            if chosen:
                return chosen
            return getattr(base, field_name) if base else default

        return cls(
            prompt=prompt,
            medium=pick("medium", DEFAULT_STYLE_MEDIUM),
            avoid=pick("avoid", DEFAULT_STYLE_AVOID),
            grade=pick("grade", DEFAULT_COLOR_GRADE),
            title=pick("title", "crimson").lower(),
            label=pick("label", ""),
        )

    def export_json(self) -> dict[str, str]:
        return {"label": self.label, "prompt": self.prompt, "medium": self.medium,
                "avoid": self.avoid, "grade": self.grade, "title": self.title}


STYLE_PRESETS: dict[str, StylePreset] = {
    # The reference look: white pen on midnight blue, spot colour on one thing.
    # Every panel is drawn with the same single line, so images generated hours
    # apart still cut together -- which is the reason it is the default.
    "midnight": StylePreset(
        label="深蓝彩漫",
        prompt=(
            "Contemporary Chinese comic illustration, fully painted in colour: flat cel-shaded fills with soft "
            "gradients where light falls, inside confident black ink outlines, semi-realistic proportions. The palette "
            "is cool - deep navy and blue walls, blue night shadow, blue-grey furniture - with one warm practical "
            "light source, a lamp, a window or a screen, laying amber across the subject; that warm-against-cool "
            "contrast is the look. Rooms are real and furnished to the edges with the ordinary things a lived-in space "
            "holds, drawn in enough detail to say whose room it is; distant walls and window frames may be left as "
            "pale line drawn over the blue. The subject is the most saturated, most brightly lit and most detailed "
            "thing in the frame, and everything else sits back in the blue. One saturated accent may carry the "
            "emotional beat - a red crack of light under a door, a pink note, a warm family photograph - and a figure "
            "who is present but is not the subject is a solid dark silhouette. Faces act in a few confident lines. "
            "Editorial, emotional, grounded. Not photorealistic, no 3D render, no watercolour, no flat vector icons, "
            "no plain empty background, no pale washed-out palette, 16:9"
        ),
        medium=("a fully painted contemporary Chinese comic illustration in a cool "
                "midnight-blue palette lit by one warm lamp"),
        avoid="photography, 3D rendering, flat vector icons, or a pale washed-out palette",
        grade="深蓝电影感",
        title="crimson",
    ),
    # The warm, literal one. Faces act here, so it carries dialogue and feeling
    # better than anything else in the set.
    "manhua": StylePreset(
        label="国漫条漫",
        prompt=(
            "Contemporary Chinese webtoon panel, mature emotional-story comic aesthetic, present-day mainland "
            "setting. Semi-realistic adults with believable proportions and faces that genuinely act, the "
            "expression carrying the sentence. Clean tapered black ink linework, heavier on the outer contour "
            "and light inside, flat colour fills with one soft cel shadow and one warm rim light, almost no "
            "gradients. Restrained palette of dusty blue-grey, warm beige, soft olive and pale peach skin, a "
            "gentle rosy blush on the cheeks. Even soft daylight, low contrast, no theatrical shadows. An "
            "ordinary interior or street holding only the few props the moment needs, plain wall behind. "
            "Subtle printed-paper grain. "
            "Not photorealistic, no 3D render, no watercolour, no glossy anime highlights, no neon, no chibi, 16:9"
        ),
        medium="a contemporary Chinese webtoon panel",
        avoid="photography or 3D rendering",
        grade="灰调中性",
        title="paper",
    ),
    # Almost nothing on the page. Best on writing that is already slow.
    "ink": StylePreset(
        label="水墨留白",
        prompt=(
            "Traditional Chinese ink painting on raw xuan rice paper, brush and black ink only, literati guohua "
            "manner. Bold confident wet strokes with visible dry-brush texture and ink bleeding softly into the "
            "fibre, tone built from three or four washes running pale grey to solid black, forms suggested "
            "rather than outlined and left open at the edges. Vast empty paper as breathing space, the subject "
            "set off-centre against a low horizon, distance carried by a paler wash and mist. Human figures "
            "small, gestural and anonymous, three or four strokes each, dignified rather than detailed. Warm "
            "ivory paper tone with faint fibre texture. Exactly one accent of vermilion red, no bigger than a "
            "seal, and no other colour anywhere. "
            "Not photorealistic, no 3D render, no colour painting, no pencil, no comic outlines, "
            "no written characters or calligraphy, 16:9"
        ),
        medium="a Chinese ink-and-wash painting on rice paper",
        avoid="photography, 3D rendering, or full-colour illustration",
        grade="水墨意境",
        title="ink",
    ),
    # Physical and tactile, and hard to make ugly: shape and shadow only.
    "papercut": StylePreset(
        label="剪纸拼贴",
        prompt=(
            "Layered cut-paper collage photographed straight on, everything built from flat shapes scissor-cut "
            "from coloured paper and stacked in three or four clearly separated depth planes, each layer "
            "casting a soft real drop shadow on the one behind it so the image reads as a physical object. "
            "Shapes bold, simplified and geometric, no outlines and no rendering, detail cut away rather than "
            "drawn; figures are clean silhouettes with a single cut for an eye or a mouth. Five papers only -- "
            "deep teal, warm terracotta, mustard, dusty rose and bone white -- each matte and uncoated with "
            "visible fibre and slightly imperfect hand-cut edges. Even soft studio light from the upper left. "
            "Graphic, tactile, warm. "
            "Not photorealistic, no 3D render, no line art, no gradients inside a shape, no glossy material, 16:9"
        ),
        medium="a layered cut-paper collage",
        avoid="photography, 3D rendering, or line-drawn illustration",
        grade="牛皮纸",
        title="paper",
    ),
    # Two inks and a lot of bare paper. Loud and cheap-looking on purpose.
    "riso": StylePreset(
        label="复古套印",
        prompt=(
            "Two-colour risograph print on off-white uncoated paper, 1960s screen-printed poster aesthetic. "
            "Only two spot inks, fluorescent orange and deep teal, overprinting into a third dark tone where "
            "they cross; everywhere else is bare cream paper. Coarse visible halftone dot screens instead of "
            "smooth tone, a deliberate registration offset of a millimetre or two so each ink sits slightly "
            "beside its shape, uneven roller density, speckle and small print faults. Shapes chunky and heavily "
            "simplified into confident mid-century flat forms with no fine detail, faces reduced to a few "
            "decisive marks. Bold poster composition, one dominant figure or object, generous empty paper. "
            "Not photorealistic, no 3D render, no smooth gradients, no full-colour palette, no digital gloss, 16:9"
        ),
        medium="a two-colour risograph poster print",
        avoid="photography, 3D rendering, or full-colour illustration",
        grade="复古工业",
        title="poster",
    ),
    # Paint you can see the brush in. The softest, kindest look in the set.
    "gouache": StylePreset(
        label="水粉绘本",
        prompt=(
            "Hand-painted gouache storybook illustration, opaque matte paint on textured cold-press board, "
            "visible directional brush strokes and small ridges of dried paint, edges soft and slightly "
            "irregular where one colour was laid over another. Warm nostalgic palette of ochre, sage, dusty "
            "rose, cream and faded denim blue, every colour mixed rather than pure, low saturation and high "
            "value. Gentle late-afternoon light with long soft shadows and a warm glow, no hard specular "
            "highlights. Simplified rounded figures in folk-illustration proportions with generous space around "
            "them, faces friendly and unfussy. Tender and quiet, the world a little softer than the real one. "
            "Not photorealistic, no 3D render, no ink outlines, no anime, no airbrush smoothness, no neon, 16:9"
        ),
        medium="a hand-painted gouache storybook illustration",
        avoid="photography or 3D rendering",
        grade="奶油",
        title="paper",
    ),
    # Real photography, one hard light, most of the frame left in the dark.
    "noir": StylePreset(
        label="黑白电影",
        prompt=(
            "Black-and-white cinematic film still, 1950s film-noir photography on a 40mm anamorphic lens. Pure "
            "monochrome with a deep crushed black point and clean specular whites, extreme chiaroscuro: a "
            "single hard key from one side, most of the frame in shadow, the subject cut out by a rim of light. "
            "Venetian blinds, window frames, railings and smoke throwing hard graphic shadows across faces and "
            "walls. Wet streets, rain, cigarette haze and fog catching visible shafts of light. Real adults in "
            "period-neutral coats and shirts, tense restrained body language, framed from slightly low or seen "
            "through a doorway. Fine silver-halide grain, gentle vignette, slight halation in the highlights. "
            "No colour, no cartoon, no illustration, no 3D render, no neon, 16:9"
        ),
        medium="a black-and-white film-noir photograph",
        avoid="illustration, cartoon art, or 3D rendering",
        grade="高清黑白",
        title="white",
    ),
    # Also photography, and the exact opposite of noir: colour is the point.
    "neon": StylePreset(
        label="霓虹夜城",
        prompt=(
            "Photorealistic cinematic night frame, contemporary East Asian megacity after rain, fast anamorphic "
            "lens shot wide open. The only light is neon and signage -- magenta, cyan, electric blue -- cutting "
            "through humid haze, plus one warm sodium practical; everything else falls into deep blue-black "
            "shadow. Wet asphalt and glass smearing the signs into long vertical reflections, steam off the "
            "vents, headlight trails, horizontal lens flare and soft bloom around every source. One lone modern "
            "figure kept small inside a huge frame of towers, walkways and cable runs, seen from behind or in "
            "profile, the city doing the talking. Shallow depth of field, heavy bokeh, fine sensor noise, a "
            "teal-and-magenta grade. "
            "No cartoon, no illustration, no 3D game render, no daylight, 16:9"
        ),
        medium="a photorealistic neon-lit night photograph",
        avoid="illustration, cartoon art, or a daylight scene",
        grade="赛博朋克",
        title="electric",
    ),
    # The bright counterpart to midnight: black pen on paper, one highlighter.
    "notebook": StylePreset(
        label="手绘笔记",
        prompt=(
            "Hand-drawn explainer sketch on off-white paper, the look of a well-kept notebook page. Everything "
            "drawn with a black felt-tip marker in a loose confident single-weight line, slightly wobbly and "
            "clearly human, with small pen overshoots at the corners; shading is quick parallel hatching, never "
            "solid fill. Simple but expressive human figures with dot eyes and readable posture, objects "
            "reduced to their most recognisable shape. One warm yellow highlighter swipe sits roughly behind "
            "the single most important element, and a red pen is used sparingly for circles, ticks and emphasis "
            "marks. Freehand dashed arrows and hand-drawn frames connect the ideas, the layout airy and "
            "organised with plenty of empty paper. Faint paper grain, soft warm shadow at the edges. "
            "Not photorealistic, no 3D render, no painted colour, no dark background, "
            "no printed text or letterforms, 16:9"
        ),
        medium="a hand-drawn marker sketch on off-white paper",
        avoid="photography, 3D rendering, or painted full-colour illustration",
        grade="高清明亮",
        title="marker",
    ),
}
DEFAULT_STYLE_PRESET = "midnight"

# Art direction can also live in a dedicated JSON file, so presets are
# editable (and addable) without touching the code. The file is created from
# the built-ins on first run; presets defined there override built-ins of the
# same name and may add new ones. Point IMAGE_STYLES_FILE at a different file
# to keep several palettes around.
STYLES_FILE_SETTING = "IMAGE_STYLES_FILE"
DEFAULT_STYLES_FILENAME = "styles.json"

# Framing is per scene, not part of the art direction: thirty medium shots in a
# row is the fastest way to make a video feel monotonous, however good the
# style is.
@dataclass(frozen=True)
class Framing:
    """One shot size: where the camera sits, and how much may be in the frame.

    The element budget and the subject's size live here rather than in a
    second dictionary keyed the same way. Two tables of the same shot sizes
    are two tables that disagree the first time one is added to.
    """

    # Where the camera is, in the words the image model is given.
    brief: str
    # Supporting elements allowed besides the subject. A wide shot is a place
    # and can carry a few; a close-up is a face and can carry almost nothing.
    # The number is a cap given to both the director and the image model, so
    # the brief cannot ask for more than the picture is allowed to draw.
    elements: int
    # How much of the frame height the subject fills. Written as a fraction on
    # purpose: "large" and "small" are what produced frames where the subject
    # and a background chair were drawn the same size.
    subject_height: str


SHOT_SIZES = {
    "wide": Framing(
        brief=(
            "Wide establishing shot: the whole room or street is visible, generous headroom, the setting doing as "
            "much work as the people, but one figure or object still clearly leads the frame."
        ),
        elements=4,
        subject_height=("filling the frame if it is a place, and at about a third of the frame height if it "
                        "is a person or an object"),
    ),
    "medium": Framing(
        brief=(
            "Medium shot from roughly the waist up: the subject fills the middle of the frame, enough background "
            "to read the place and no more."
        ),
        elements=2,
        subject_height="at about two thirds of the frame height",
    ),
    "close": Framing(
        brief=(
            "Close-up: the subject fills the frame - a face, a pair of hands, a single object - and the "
            "background is reduced to two or three simple shapes with no detail of their own. A close-up is "
            "not automatically a face; hands on a keyboard or a phone on a duvet is the same shot."
        ),
        elements=1,
        subject_height="filling at least three quarters of the frame height",
    ),
}
DEFAULT_SHOT_SIZE = "medium"

# What every frame has to do, whichever of the nine styles is drawing it.
#
# Composition is deliberately NOT in the style presets. A preset says what
# medium a picture is in; this says where the eye goes, and the same answer
# has to hold in all of them. It was in neither, and the usage block below
# used to argue against it in as many words - "do not visually overemphasize
# one incidental detail" is an instruction to flatten the frame, and flat is
# what came back: every element at one size, one weight and one level of
# detail, nothing to land on, and on a phone that reads as texture rather
# than as a picture.
COMPOSITION = (
    "Composition. The frame has exactly one subject and it is {subject}, drawn {height}: the largest thing in the "
    "picture, the sharpest, the most detailed, the most fully coloured, and the one with the most contrast against "
    "what is behind it, placed either dead centre or on a rule-of-thirds intersection. Everything else is support "
    "and has to look subordinate - smaller, flatter, fewer marks, less colour, less contrast - and nothing may "
    "overlap or crowd the subject's outline. "
    "The subject has to stay identifiable with the whole frame an inch wide, so its silhouette must read as a shape "
    "on its own, separated from the background by tone and not by an outline alone. "
    "Keep clear space immediately around the subject's outline, but fill the frame: it is a whole, furnished "
    "scene from edge to edge, and the subject leads it by size, light and colour - never a subject floating on an "
    "empty field. "
    "One moment, one place, one continuous space - never a collage, a split screen, a before-and-after pair, a grid "
    "of panels, an inset, or a row of icons. "
    "At most {elements} besides the subject; if the sentence names more, draw the ones it turns on and leave "
    "the rest out."
)

# What the subject is called when the director did not name one - an older
# manifest, or a model that skipped the field. The rest of the block still
# applies: an unnamed subject is still one subject.
DEFAULT_SUBJECT = "the person or thing this sentence is about"

MAX_COPY_CHARACTERS = 1800
DEFAULT_SCENE_CHARACTERS = 22
MIN_SCENE_CHARACTERS = 8
DEFAULT_IMAGE_CONCURRENCY = 3
MAX_IMAGE_CONCURRENCY = 8
# 6 added `speed`: a manifest without one was written before the global speed
# existed, so its narration was read at the baseline.
# 7 added `moods`: the director's reading of the copy, which the music is
# chosen from. A manifest without one falls back to reading the copy itself,
# so an older run still gets a bed rather than an error.
# 8 added each scene's `subject`: the one thing its frame is about. A scene
# without one still composes - the subject is named generically - but the
# picture is only as focused as the description it was given, so a resume
# from v7 keeps the flatter images it already paid for.
# 9 names every clip and frame after the inputs that made it (see "assets"),
# records the voice and the style beside the speed, and gives each scene a
# `take` for --redo. Files from an older run are trusted once when it is
# resumed, and renamed to the key they would have had.
MANIFEST_VERSION = 9

NARRATION_SUBTITLE_TRACK = "narration_subtitles"
TITLE_OVERLAY_TRACK = "title_overlay"


def title_track_name(index: int) -> str:
    """One track per title line: title_overlay, title_overlay_2, ..."""
    return TITLE_OVERLAY_TRACK if index == 0 else f"{TITLE_OVERLAY_TRACK}_{index + 1}"


COLOR_GRADE_TRACK = "grade"

# ------------------------------------------------------------ typography ----
# The numbers below are measured off the reference video (1920x1080, 30 fps)
# rather than guessed, so the draft opens looking like it instead of merely
# near it. What was measured, in frame pixels:
#
#   caption   em ~66 px, block centre at y 930 (86% of frame height), sitting
#             on a dark translucent plate ~96 px tall
#   title     em ~191 px (17.7% of frame height), two lines, line pitch 182 px
#             (0.95 em -- tighter than any default), block centred on the frame,
#             on screen for about 2.4 s
#
# Jianying does not expose text metrics, so sizes are carried in its own unit
# and converted with SUBTITLE_EM_PX below.

# Jianying does not expose its text metrics, so wrapping has to be estimated.
# One CJK character at TextStyle.size = S is taken to be S * SUBTITLE_EM_PX
# pixels wide, and a line to be that much again times SUBTITLE_LINE_SPACING.
# This is also how a size is derived from a measured pixel em, so if type comes
# out uniformly too large or too small, this is the single number to calibrate.
DEFAULT_SUBTITLE_EM_PX = 9.8
SUBTITLE_LINE_SPACING = 1.25

DEFAULT_NARRATION_SUBTITLE_Y = -700
# 66 px / 9.8. The old 8.0 rendered around 78 px, noticeably heavier than the
# reference, which keeps its captions deliberately quiet under the picture.
DEFAULT_SUBTITLE_SIZE = 6.8
# The reference caption is a medium-weight gothic, not the black weight this
# tool used to set: the plate and the stroke do the legibility work, and a
# black weight at this size closes up the counters.
DEFAULT_SUBTITLE_FONT = "SourceHanSansCN_Medium"
# Thinner than the old 24 because the plate already separates text from art.
DEFAULT_SUBTITLE_BORDER_WIDTH = 16.0

# Dead centre, which is where the reference puts it -- the opening title is the
# picture for its two and a half seconds, not a caption above one.
DEFAULT_TITLE_Y = 0
# 191 px / 9.8.
DEFAULT_TITLE_SIZE = 19.5
DEFAULT_TITLE_SECONDS = 2.4
DEFAULT_TITLE_BORDER_WIDTH = 22.0
# Heavy condensed display face; the reference uses one of this family, and at
# 19.5 a normal gothic looks thin and wide by comparison.
DEFAULT_TITLE_FONT = "优设标题黑"
# 182 / 191. Lines this large need to sit closer together than a text default
# would put them, or the two halves of a title stop reading as one block.
TITLE_LINE_PITCH = 0.95
# Titles shorter than this stay on one line; longer ones break, because two
# short lines can be set much larger than one long one.
TITLE_SINGLE_LINE_MAX = 8
MAX_TITLE_LINES = 3
DEFAULT_TITLE_MAX_LINE_WIDTH = 0.86

# Chinese line-breaking, kept to the two rules that actually show. There is no
# word segmenter here, and adding one for a twelve-character title would be a
# poor trade; these two character sets get the common cases right on their own.
#
#   a line must not START with a character that clings to the word before it
#   a line must not END with one that clings to the word after it
#
# The second set is the one that matters. Coverbs, negations, modals and
# numerals are exactly where a width-only break lands, and they are exactly the
# characters that read as broken when they end up alone at the end of a line.
TITLE_TRAILING_PUNCTUATION = "，,、。.；;：:！!？?～~—-…"
TITLE_NEVER_STARTS_A_LINE = set("的地得了着过们吗呢吧啊呀嘛么儿" + TITLE_TRAILING_PUNCTUATION)
TITLE_NEVER_ENDS_A_LINE = set(
    "把被给让使叫令帮替陪为向往从由对跟和与同及比朝沿依按据于在到"   # coverbs, prepositions
    "而但却且或若如即因所虽既除并也就都还又再才只不没别"             # conjunctions, negations
    "很太最更挺极超特非真略稍颇愈越已曾正刚将快绝尤甚仅未"             # degree and time adverbs
    "会能要可应该想愿敢肯须必得是有做去来说看用当成变"               # modals and light verbs
    "一二三四五六七八九十百千万第每这那哪几多半整全另各某本上下前后"  # numerals, determiners
)

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

# Opening-title colourways. The reference title is not one colour: it runs
# warm white into crimson across the block, with a near-black stroke and a hard
# offset shadow under it. This file used to say Jianying has no per-character
# colour and approximate the ramp by hand - first line primary, the rest
# accent. That limit is pyJianYingDraft's, not the draft format's: a text's
# style is a list of runs, each over a range of characters, and the library
# only ever writes one. So the ramp is drawn as it is now, a fill per
# character from the primary to the accent (see paint_characters), and a
# colourway names its two ends.


@dataclass(frozen=True)
class TitleColours:
    """primary = where the title starts, accent = where it ends, border = the stroke.

    `ramp` runs the fill from one to the other character by character. It is
    off for colourways made of two inks that never blend on the real thing -
    a risograph's two drums, a marker and a red pen, ink and a seal - where the
    lines stay two flat colours instead: primary first, accent after.
    """

    primary: tuple[float, float, float]
    accent: tuple[float, float, float]
    border: tuple[float, float, float]
    ramp: bool = True


TITLE_PRESETS: dict[str, TitleColours] = {
    # The reference: warm white over crimson on a near-black stroke.
    "crimson": TitleColours((0.98, 0.97, 0.95), (0.84, 0.13, 0.11), (0.05, 0.06, 0.09)),
    # Warm off-white over amber on deep brown; sits inside a muted illustrated
    # palette instead of fighting it the way pure red does.
    "paper": TitleColours((0.97, 0.94, 0.88), (0.93, 0.66, 0.18), (0.16, 0.12, 0.10)),
    # Ink on paper, with the vermilion of a seal for the accent.
    "ink": TitleColours((0.09, 0.09, 0.10), (0.78, 0.18, 0.12), (0.98, 0.96, 0.91), ramp=False),
    # All white on a crimson stroke: the loudest option, and the safest over
    # photography, where a coloured fill has nothing stable to sit against.
    "white": TitleColours((1.0, 1.0, 1.0), (1.0, 1.0, 1.0), (0.86, 0.12, 0.12)),
    "gold": TitleColours((1.0, 0.97, 0.90), (1.0, 0.80, 0.16), (0.09, 0.09, 0.09)),
    # Risograph inks, and the paper is cream, so the type is the two inks and
    # the stroke is the paper: deep teal over fluorescent orange, knocked out
    # in cream. Cream type on cream paper would live entirely on its stroke.
    "poster": TitleColours((0.05, 0.27, 0.29), (0.97, 0.36, 0.16), (0.99, 0.96, 0.89), ramp=False),
    # Neon: white over cyan on a near-black stroke that reads as the night.
    "electric": TitleColours((1.0, 1.0, 1.0), (0.24, 0.85, 0.95), (0.04, 0.03, 0.10)),
    # Marker on paper: black over red, knocked out with a white stroke.
    "marker": TitleColours((0.11, 0.11, 0.13), (0.86, 0.16, 0.14), (1.0, 1.0, 1.0), ramp=False),
}
DEFAULT_TITLE_STYLE = "crimson"

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
    # The one thing the frame is about, named by the director as a thing that
    # can be drawn. It is what COMPOSITION builds its hierarchy around, and
    # asking for it separately is what stops the image prompt from being a
    # list of everything in the sentence.
    subject: str = ""
    shot_size: str = DEFAULT_SHOT_SIZE
    pause_after: bool = False
    cast: list[str] = field(default_factory=list)
    audio_path: str | None = None
    image_path: str | None = None
    duration_us: int | None = None
    # How many times the frame has been redrawn with --redo. Part of the
    # frame's key, so a redraw is a new file rather than the old one found
    # again, and it moves a fixed ARK_IMAGE_SEED on so the redraw differs.
    take: int = 0


SCENE_FIELDS = ("text", "image_prompt", "subject", "shot_size", "pause_after",
                "cast", "audio_path", "image_path", "duration_us", "take")


@dataclass
class SceneTiming:
    """Where one scene's narration and picture sit on the timeline."""

    narration_start: int
    narration_duration: int
    visual_start: int
    visual_duration: int

    @property
    def narration_end(self) -> int:
        return self.narration_start + self.narration_duration


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


def env_choice(name: str, default: str, options: Any, message: str) -> str:
    """One of a fixed set of words, lower-cased, or `message` as the error."""
    value = env_value(name, default).lower()
    if value not in options:
        raise RuntimeError(message)
    return value


class ConfigProblems:
    """Every setting that failed to parse, collected rather than raised one at a time."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def check(self, parse: Any, fallback: Any) -> Any:
        """parse(), or `fallback` with the failure recorded."""
        try:
            return parse()
        except RuntimeError as exc:
            self.messages.append(str(exc))
            return fallback

    def or_default(self, parse: Any) -> Any:
        """`parse`, answering its own default (its second argument) when it fails."""
        def lenient(name: str, default: Any, *rest: Any, **options: Any) -> Any:
            return self.check(lambda: parse(name, default, *rest, **options), default)
        return lenient

    def raise_if_any(self) -> None:
        if len(self.messages) == 1:
            raise RuntimeError(self.messages[0])
        if self.messages:
            listed = "\n".join(f"  {number}. {message}" for number, message in enumerate(self.messages, 1))
            raise RuntimeError(f"{len(self.messages)} settings need attention:\n{listed}")


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


def resolve_asset_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def styles_file_path() -> Path:
    """Where the style presets live. Relative paths resolve against ROOT."""
    raw = os.getenv(STYLES_FILE_SETTING, "").strip()
    return resolve_asset_path(raw) if raw else ROOT / DEFAULT_STYLES_FILENAME


def builtin_styles_document() -> dict:
    return {
        "default": DEFAULT_STYLE_PRESET,
        "presets": {name: preset.export_json() for name, preset in STYLE_PRESETS.items()},
    }


def load_style_presets() -> tuple[dict[str, StylePreset], str, str]:
    """Return (presets, default_name, source).

    Built-ins are always present; presets from the JSON file override
    built-ins of the same name and may add new ones. A missing file is
    created from the built-ins so it can be edited in place; a malformed
    one is an error rather than a silent fallback.
    """
    presets = dict(STYLE_PRESETS)
    default_name = DEFAULT_STYLE_PRESET
    path = styles_file_path()
    if not path.exists():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(builtin_styles_document(), ensure_ascii=False, indent=2)
            path.write_text(payload + "\n", encoding="utf-8")
        except OSError:
            return presets, default_name, "built-in (styles file missing)"
        return presets, default_name, f"{path} (created from built-ins)"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{STYLES_FILE_SETTING} is not readable JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"{path} must be a JSON object with a \"presets\" mapping.")
    raw_presets = data.get("presets", data)
    if not isinstance(raw_presets, dict) or not raw_presets:
        raise RuntimeError(f"{path}: \"presets\" must be a non-empty object of style name -> style.")
    for name, value in raw_presets.items():
        key = str(name).strip().lower()
        if not key:
            raise RuntimeError(f"{path}: a style name cannot be blank.")
        presets[key] = StylePreset.parse(key, value, str(path))
    file_default = data.get("default")
    if file_default is not None:
        default_name = str(file_default).strip().lower()
        if default_name not in presets:
            raise RuntimeError(
                f"{path}: default style {file_default!r} is not one of: {', '.join(sorted(presets))}."
            )
    return presets, default_name, str(path)


def layout_y(pixels: int) -> float:
    """Convert a reference-frame pixel offset to a clamped transform_y."""
    return clamp_y(pixels / LAYOUT_REFERENCE_HALF_HEIGHT)


def clamp_y(normalized: float) -> float:
    return max(-SAFE_NORMALIZED_Y, min(SAFE_NORMALIZED_Y, normalized))


def characters_per_line(size: float, max_line_width: float, em_px: float) -> int:
    """How many CJK characters fit on one line at this size."""
    return max(1, int(max_line_width * CANVAS_WIDTH / max(1e-6, size * em_px)))


def title_cut(text: str, limit: int, min_tail: int = 3) -> int:
    """Pick where to break one title line, filling it as far as it reads well.

    Fill-first rather than balance-first: it keeps the line count down, which
    keeps the type large, and on the reference title it lands on exactly the
    break a person chose by hand. The four positions below the limit are then
    tried in turn so a particle never gets orphaned at either end.
    """
    high = max(1, min(limit, len(text) - min_tail))
    low = max(1, high - 3)
    best, best_score = high, None
    for cut in range(high, low - 1, -1):
        before, after = text[cut - 1], text[cut]
        score = float(cut - high)  # 0 for a full line, -1 per character given up
        if before in TITLE_TRAILING_PUNCTUATION:
            score += 10.0
        if before in TITLE_NEVER_ENDS_A_LINE:
            score -= 8.0
        if after in TITLE_NEVER_STARTS_A_LINE:
            score -= 8.0
        if before.isascii() and after.isascii() and before.isalnum() and after.isalnum():
            score -= 12.0  # never split a run of digits or Latin letters
        if best_score is None or score > best_score:
            best, best_score = cut, score
    return best


def split_title_lines(title: str, max_characters: int,
                      single_line_max: int = TITLE_SINGLE_LINE_MAX,
                      max_lines: int = MAX_TITLE_LINES) -> list[str]:
    """Break a title the way the reference does: two or three short, big lines.

    A newline written into --title is obeyed as given. Everything else is
    broken here rather than by Jianying's auto-wrap, because the wrap point
    decides the shape of the whole opening frame and auto-wrap picks it purely
    on width.
    """
    written = [part.strip() for part in re.split(r"[\r\n]+", title) if part.strip()]
    if len(written) > 1:
        return written[:max_lines]
    text = re.sub(r"\s+", "", title.strip())
    if not text:
        return []
    limit = max(2, max_characters)
    if len(text) <= max(single_line_max, 0):
        return [text]
    lines: list[str] = []
    rest = text
    while len(lines) < max_lines - 1 and len(rest) > limit:
        cut = title_cut(rest, limit)
        lines.append(rest[:cut].rstrip(TITLE_TRAILING_PUNCTUATION))
        rest = rest[cut:].lstrip(TITLE_TRAILING_PUNCTUATION)
    lines.append(rest)
    return [line for line in lines if line]


def fit_title_size(lines: list[str], size: float, max_line_width: float, em_px: float) -> float:
    """Shrink the title until its longest line fits inside the frame.

    Only ever shrinks. A title long enough to trigger this is already at three
    lines, so the alternative is Jianying wrapping it a fourth time and the
    stacked lines drifting apart from the pitch computed here.
    """
    longest = max((len(line) for line in lines), default=0)
    if longest <= 0:
        return size
    budget = max_line_width * CANVAS_WIDTH
    needed = longest * size * em_px
    return size if needed <= budget else max(1.0, size * budget / needed)


def title_line_offsets(count: int, base_y: float, size: float, em_px: float) -> list[float]:
    """Centre a stack of title lines on base_y at the reference line pitch.

    Each line is its own text segment, because Jianying colours a segment as a
    whole and the reference title changes colour partway down. That means the
    line spacing is ours to set rather than the text engine's.
    """
    if count <= 0:
        return []
    pitch = size * em_px * TITLE_LINE_PITCH / CANVAS_HALF_HEIGHT
    top = (count - 1) / 2
    offsets = [base_y + (top - index) * pitch for index in range(count)]
    # A block pushed past the safe area is moved as a whole. Clamping line by
    # line would pile the outer lines on top of each other against the edge,
    # which looks broken in a way an off-centre block does not.
    shift = 0.0
    if offsets[0] > SAFE_NORMALIZED_Y:
        shift = SAFE_NORMALIZED_Y - offsets[0]
    elif offsets[-1] < -SAFE_NORMALIZED_Y:
        shift = -SAFE_NORMALIZED_Y - offsets[-1]
    return [clamp_y(offset + shift) for offset in offsets]


Colour = tuple[float, float, float]


def _to_linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def _to_srgb(channel: float) -> float:
    channel = max(0.0, channel)
    return 12.92 * channel if channel <= 0.0031308 else 1.055 * channel ** (1 / 2.4) - 0.055


def _cbrt(value: float) -> float:
    return math.copysign(abs(value) ** (1 / 3), value)     # math.cbrt is 3.11+


def _oklab(colour: Colour) -> Colour:
    r, g, b = (_to_linear(channel) for channel in colour)
    # The three cone responses: long, medium and short wavelengths.
    long_ = _cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    medium = _cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    short = _cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    return (0.2104542553 * long_ + 0.7936177850 * medium - 0.0040720468 * short,
            1.9779984951 * long_ - 2.4285922050 * medium + 0.4505937099 * short,
            0.0259040371 * long_ + 0.7827717662 * medium - 0.8086757660 * short)


def _from_oklab(lab: Colour) -> Colour:
    lightness, a, b = lab
    long_ = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    medium = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    short = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    rgb = (4.0767416621 * long_ - 3.3077115913 * medium + 0.2309699292 * short,
           -1.2684380046 * long_ + 2.6097574011 * medium - 0.3413193965 * short,
           -0.0041960863 * long_ - 0.7034186147 * medium + 1.7076147010 * short)
    return tuple(min(1.0, _to_srgb(channel)) for channel in rgb)


def blend(start: Colour, end: Colour, amount: float) -> Colour:
    """The colour `amount` of the way from start to end, spaced evenly to the eye.

    Mixed in OKLab rather than in RGB. RGB halfway between warm white and
    crimson is a greyed salmon and the steps bunch up at the red end; in a
    perceptual space each character moves the same visible distance.
    """
    if amount <= 0:
        return start
    if amount >= 1:
        return end
    a, b = _oklab(start), _oklab(end)
    return _from_oklab(tuple(x + (y - x) * amount for x, y in zip(a, b, strict=True)))


# How far the ramp runs within one line, against the step from one line to the
# next. At 0.5 each line keeps the colour it reads as - the first warm white,
# the last crimson - and runs part of the way towards its neighbour, which is
# the reference's block. A ramp spread evenly over every character, compared
# on the rendered title, turned the end of the first line pink and took the
# punch out of the crimson; it is also what a single-line title still does.
TITLE_RAMP_DRIFT = 0.5


def title_fills(lines: list[str], colours: TitleColours, ramp: bool) -> list[list[Colour]]:
    """The fill of every character of every title line.

    A ramp starts on the primary at the first character and lands on the
    accent at the last, in reading order, eased so the two colours a
    colourway names are the ones that read. Without a ramp the first line is
    the primary and the rest the accent.
    """
    if not ramp:
        return [[colours.primary if row == 0 else colours.accent] * len(line)
                for row, line in enumerate(lines)]
    count = len(lines)
    fills: list[list[Colour]] = []
    for row, line in enumerate(lines):
        fills.append([])
        for column in range(len(line)):
            along = column / (len(line) - 1) if len(line) > 1 else 0.0
            t = (row + TITLE_RAMP_DRIFT * along) / (count - 1 + TITLE_RAMP_DRIFT) if count > 1 else along
            eased = t * t * (3 - 2 * t)
            fills[-1].append(tuple(round(channel, 4)
                                   for channel in blend(colours.primary, colours.accent, eased)))
    return fills


def colour_runs(fills: list[Colour]) -> list[tuple[int, int, Colour]]:
    """(start, end, fill) for each stretch of one colour - as few runs as the fills allow."""
    runs: list[tuple[int, int, Colour]] = []
    for index, fill in enumerate(fills):
        if runs and runs[-1][2] == fill:
            runs[-1] = (runs[-1][0], index + 1, fill)
        else:
            runs.append((index, index + 1, fill))
    return runs


def split_style_runs(content: dict[str, Any], fills: list[Colour]) -> dict[str, Any]:
    """A text material's content, with its one style run split by fill.

    Every run keeps the font, stroke, shadow and size of the original and
    changes only the fill colour, so a ramped title is still one typeface with
    one outline - just not one colour.
    """
    runs = colour_runs(fills)
    if len(runs) <= 1:
        return content
    template = content["styles"][0]
    styles = []
    for start, end, fill in runs:
        style = deepcopy(template)
        style["range"] = [start, end]
        style["fill"]["content"]["solid"]["color"] = list(fill)
        styles.append(style)
    return {**content, "styles": styles}


def paint_characters(segment: Any, fills: list[Colour]) -> None:
    """Colour a text segment character by character.

    The split is applied to the material the library exports, and installed
    on the segment itself, because pyJianYingDraft exports a text's material
    at the moment the segment is added to the draft.
    """
    export = segment.export_material

    def export_in_runs() -> dict[str, Any]:
        material = export()
        content = split_style_runs(json.loads(material["content"]), fills)
        return {**material, "content": json.dumps(content, ensure_ascii=False)}

    segment.export_material = export_in_runs


def subtitle_line_count(text: str, size: float, max_line_width: float, em_px: float) -> int:
    """Estimate how many lines Jianying will wrap this caption onto."""
    characters = len(text.strip())
    if characters <= 0:
        return 1
    per_line = max(1, int(max_line_width * CANVAS_WIDTH / max(1e-6, size * em_px)))
    return max(1, math.ceil(characters / per_line))


def subtitle_baseline_y(base_y: float, lines: int, size: float, em_px: float) -> float:
    """Hold the bottom line at a constant height however many lines there are.

    A Jianying text block is positioned by its centre, so a two-line caption
    grows both upward and downward and its last line sits lower than a
    one-line caption's. Across thirty shots that reads as the subtitle
    jittering up and down. Raising the block by half a line per extra line
    keeps the bottom edge where it was.
    """
    if lines <= 1:
        return clamp_y(base_y)
    line_height = size * em_px * SUBTITLE_LINE_SPACING / CANVAS_HALF_HEIGHT
    return clamp_y(base_y + (lines - 1) * line_height / 2)


# ------------------------------------------------------------------ bgm ----

DEFAULT_BGM_LIBRARY = "assets/bgm"
BGM_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}

# Both levels as linear gain, and both between 20 and 25 dB under the voice.
# That band is the whole setting: below it the bed stops doing anything, above
# it the music is a second thing to listen to while someone is talking.
DEFAULT_BGM_VOLUME = 0.056          # -25 dB, under speech
DEFAULT_BGM_LIFT_VOLUME = 0.10      # -20 dB, in the gaps

# The vocabulary the director labels the copy with, and the same words the
# library's filenames are tagged with. Keeping the two lists literally the
# same strings is what removes the need for a synonym table: a file called
# 紧张Kill Drill.mp3 is picked for a script the director called 紧张.
BGM_MOODS = ("紧张", "危机", "焦虑", "疑问", "疑惑", "失落", "转机",
             "升华", "欢乐", "舒缓", "平淡", "解释", "讲解", "措施", "解决")

# Tags that say WHERE in a video a track belongs rather than how it feels.
# One track is laid under the whole video here, so a cue written for an
# opening or an ending makes a worse bed than an untagged one even when its
# mood fits. Ranked down, not excluded: a library may hold nothing else.
BGM_POSITIONS = ("开头", "结尾", "后期", "提出")

# What the copy itself says, for when the director labels nothing. This is the
# fallback that keeps a run working when the model returns no mood at all - a
# resume from an older manifest, an older model, a stricter JSON mode - and
# not a second opinion on the mood the model did return. Deliberately coarse:
# common function words are left out, because a word that appears in every
# script cannot tell two scripts apart.
BGM_MOOD_KEYWORDS = {
    "紧张": ("压力", "逼", "冲突", "争", "吵", "来不及", "追", "抢", "拼"),
    "危机": ("危机", "崩", "破产", "失业", "债", "亏", "塌", "灾"),
    "焦虑": ("焦虑", "担心", "害怕", "恐惧", "不安", "睡不着", "慌"),
    "疑问": ("为什么", "难道", "到底", "究竟", "怎么会", "是不是"),
    "疑惑": ("疑惑", "困惑", "想不通", "看不懂", "奇怪"),
    "失落": ("失落", "难过", "孤独", "委屈", "后悔", "遗憾", "眼泪", "哭"),
    "转机": ("转机", "转折", "没想到", "突然", "直到"),
    "升华": ("终于", "明白", "释然", "放下", "成长", "从此", "值得"),
    "欢乐": ("开心", "快乐", "笑", "惊喜", "轻松", "有趣"),
    "舒缓": ("慢慢", "安静", "平静", "温柔", "陪"),
    "平淡": ("日常", "普通", "平常", "每天"),
    "解释": ("其实", "因为", "所以", "原因", "本质"),
    "讲解": ("第一", "第二", "首先", "其次", "也就是说"),
    "措施": ("建议", "办法", "方法", "怎么办", "试着"),
    "解决": ("解决", "改善", "行动"),
}

# How many moods are carried into the pick. Past three the weighting below has
# them contributing a quarter of a point against a full one, which is noise.
BGM_MOOD_LIMIT = 3


def bgm_tag(path: Path) -> str:
    """The Chinese label a library file carries, from the front of its name.

    The convention is the one the tracks already arrive with - the mood
    written in Chinese before the title, as in `紧张Kill Drill - Robert
    Ruth.mp3`. Everything up to the first non-Chinese character is the tag,
    which is what keeps a Chinese artist name at the END of a filename from
    being read as one.
    """
    stem = path.stem
    end = 0
    while end < len(stem) and "一" <= stem[end] <= "鿿":
        end += 1
    return stem[:end]


def bgm_library(directory: Path | None) -> list[tuple[Path, str]]:
    """Every playable file in the library, with its tag, sorted by name.

    Sorted so that two tracks scoring the same are not picked between by the
    order the filesystem happened to list them in: the same copy has to choose
    the same music on a re-run, or a --resume quietly rescores the video.
    """
    if directory is None or not directory.is_dir():
        return []
    return sorted(
        ((path, bgm_tag(path)) for path in directory.iterdir()
         if path.is_file() and path.suffix.lower() in BGM_SUFFIXES),
        key=lambda entry: entry[0].name,
    )


def copy_moods(copy: str, limit: int = BGM_MOOD_LIMIT) -> list[str]:
    """Moods read straight off the copy, for when the director labels none."""
    counted = [
        (sum(copy.count(word) for word in words), -index, mood)
        for index, (mood, words) in enumerate(BGM_MOOD_KEYWORDS.items())
    ]
    ranked = sorted(counted, reverse=True)
    return [mood for score, _, mood in ranked if score][:limit]


def score_bgm(tag: str, moods: list[str]) -> float:
    """How well one library tag answers a ranked list of moods."""
    if not tag:
        return 0.0
    score = 0.0
    for rank, mood in enumerate(moods):
        if mood and mood in tag:
            # Earlier moods weigh more, and a later one still counts: a track
            # tagged 紧张危机 should beat a plain 紧张 for a script that is both.
            score += 1.0 / (rank + 1)
    if score and any(position in tag for position in BGM_POSITIONS):
        score -= 0.25
    return score


def pick_bgm(entries: list[tuple[Path, str]], moods: list[str]) -> Path | None:
    """The best bed in the library for a script with these moods, if any fits.

    None when nothing scores. A library holding no track for this script is a
    video with no music, which is a better answer than an arbitrary track
    under three minutes of narration - the wrong music is more distracting
    than none, and it is the one choice here nobody would think to check.
    """
    chosen, best = None, 0.0
    for path, tag in entries:
        score = score_bgm(tag, moods)
        if score > best:
            chosen, best = path, score
    return chosen


def resolve_bgm(cfg: Config, copy: str, moods: list[str]) -> tuple[Path | None, str]:
    """Which music goes under this video, and one line saying why that one.

    BGM_PATH is somebody naming a track and wins outright. Otherwise the
    library is matched against the moods the director labelled the copy with,
    falling back to the words the copy itself uses.
    """
    if cfg.bgm_path is not None:
        return cfg.bgm_path, "BGM_PATH"
    entries = bgm_library(cfg.bgm_library)
    if not entries:
        return None, f"no music: {cfg.bgm_library or DEFAULT_BGM_LIBRARY} holds no audio"
    wanted = [mood for mood in moods if mood] or copy_moods(copy)
    if not wanted:
        return None, "no music: nothing in the copy said how it should feel"
    chosen = pick_bgm(entries, wanted)
    if chosen is None:
        return None, f"no music: no track is tagged {'/'.join(wanted)}"
    return chosen, f"{'/'.join(wanted)} -> {chosen.name}"


# -------------------------------------------------------- jianying paths ----

# Where an installed editor keeps its user data, and the folder holding drafts
# inside it. The mainland build is checked before the international one: a
# machine carrying both is a 剪映 user who also has CapCut, not the reverse.
JIANYING_APP_DIRS = ("JianyingPro", "CapCut")
JIANYING_DRAFT_LEAF = "com.lveditor.draft"
JIANYING_SETTINGS = ("User Data", "Config", "globalSetting")
JIANYING_DEFAULT_DRAFTS = ("User Data", "Projects", JIANYING_DRAFT_LEAF)


def jianying_app_roots() -> list[Path]:
    """Every directory an installed 剪映 / CapCut keeps its user data in."""
    bases: list[Path] = []
    for variable in ("LOCALAPPDATA", "APPDATA"):
        value = os.getenv(variable, "").strip()
        if value:
            bases.append(Path(value))
    home = Path.home()
    # Windows is the supported platform. The macOS location costs one stat
    # call and turns "nothing was found" into "it just worked" for anyone
    # running this there anyway.
    bases += [home / "AppData" / "Local", home / "Movies"]
    roots: list[Path] = []
    for base in bases:
        for app in JIANYING_APP_DIRS:
            candidate = base / app
            if candidate.is_dir() and candidate not in roots:
                roots.append(candidate)
    return roots


def relocated_draft_dir(app_root: Path) -> Path | None:
    """The drafts folder the user moved to, as Jianying itself recorded it.

    Jianying can keep its draft library on another disk, and a machine that
    has moved it still has the default folder sitting there empty - so a probe
    that knows only the default finds a directory, calls it a hit, and writes
    every draft somewhere the editor no longer reads. Nothing would look
    wrong: the run succeeds and the draft list stays empty.

    The chosen path is in Jianying's own settings file. The key holding it has
    been renamed between versions, so rather than bet on one name, any string
    value naming a directory that exists is accepted, keys mentioning drafts
    first.
    """
    settings = app_root.joinpath(*JIANYING_SETTINGS)
    try:
        data = json.loads(settings.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    named = [(key, value) for key, value in data.items()
             if isinstance(value, str) and value.strip()]
    named.sort(key=lambda pair: "draft" not in pair[0].lower())
    for _key, value in named:
        candidate = Path(value.strip()).expanduser()
        # Settings hold the library root; drafts live in the bundle-id folder
        # under it, and some versions record that folder directly.
        if candidate.name != JIANYING_DRAFT_LEAF:
            candidate = candidate / JIANYING_DRAFT_LEAF
        if candidate.is_dir():
            return candidate
    return None


def detect_draft_dir() -> Path | None:
    """Jianying's drafts folder on this machine, or None if there is none."""
    for root in jianying_app_roots():
        moved = relocated_draft_dir(root)
        if moved is not None:
            return moved
        default = root.joinpath(*JIANYING_DEFAULT_DRAFTS)
        if default.is_dir():
            return default
    return None


def resolve_draft_dir() -> tuple[Path, str]:
    """Where finished drafts are written, and how that was decided.

    JIAN_YING_DRAFT_DIR used to be required, and it was the setting every new
    user got wrong first: the path is four directories deep and ends in a
    bundle id. The editor installs itself in one of two known places, so the
    usual answer can simply be looked up, and the drafts land where Jianying
    already reads them instead of in a folder to be copied by hand.

    An explicit setting still wins, and is still checked. A typo there is a
    mistake to report rather than a reason to quietly use somewhere else: the
    whole point of setting it is that this machine is the unusual one.
    """
    configured = os.getenv("JIAN_YING_DRAFT_DIR", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_dir():
            raise RuntimeError(f"JIAN_YING_DRAFT_DIR does not exist: {path}")
        return path, "JIAN_YING_DRAFT_DIR"
    found = detect_draft_dir()
    if found is not None:
        return found, "detected"
    raise RuntimeError(
        "Jianying's drafts folder was not found on this machine. In 剪映专业版, "
        "全局设置 -> 草稿位置 shows where it keeps drafts; put that path in "
        "JIAN_YING_DRAFT_DIR in .env."
    )


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

def _cjk_share(text: str) -> float:
    """Fraction of the letters in `text` that are CJK."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum("一" <= c <= "鿿" for c in letters) / len(letters)


def title_language_differs(title: str, scenes: list[Scene]) -> bool:
    """True when the title is not in the language the scenes are narrated in.

    The director translates: give it English copy and it returns Chinese
    scenes. A --title passed alongside is used verbatim, so an English title
    ends up read aloud by the Chinese voice configured for the narration. The
    draft is not wrong, it just sounds wrong, and nothing else would say so.
    """
    if not scenes or not title.strip():
        return False
    spoken = "".join(scene.text or "" for scene in scenes)
    # A title of digits or punctuation - "2026", "#3" - has no language to
    # disagree with, and _cjk_share would score it 0.0 and call it Latin.
    if not [c for c in title if c.isalpha()]:
        return False
    if not [c for c in spoken if c.isalpha()]:
        return False
    return abs(_cjk_share(title) - _cjk_share(spoken)) > 0.5


# How many of the copy's opening sentences the title is checked against.
# Two, because sentence one is usually a hook and sentence two is the thesis
# the title was lifted from, and a headline repeated as the second thing said
# is heard as the same stutter as one repeated as the first.
TITLE_ECHO_SCENES = 2

# A restatement shares most of the title's characters AND a fair share of its
# character pairs. Characters alone match any two Chinese sentences built out
# of the same common words; pairs alone miss a title whose clause the copy
# reorders, which is the usual way an opening line restates a headline.
TITLE_ECHO_CHARACTERS = 0.8
TITLE_ECHO_PAIRS = 0.5

# Below this, a title is too short for those ratios to mean anything: two
# characters are wholly contained in half the sentences ever written.
TITLE_ECHO_MIN_LENGTH = 4

# Dropped before comparing. The director reflows punctuation and spacing as it
# splits the copy, and a stutter is a stutter whether or not a comma survived.
TITLE_ECHO_NOISE = set(
    TITLE_TRAILING_PUNCTUATION + "\"'“”‘’()（）《》〈〉[]【】"
)


def _echo_key(text: str) -> str:
    """`text` reduced to the characters that carry its meaning."""
    return "".join(character for character in text
                   if not character.isspace()
                   and character not in TITLE_ECHO_NOISE)


def _character_pairs(text: str) -> set[str]:
    return {text[index:index + 2] for index in range(len(text) - 1)}


def title_already_narrated(title: str, scenes: list[Scene],
                           lookahead: int = TITLE_ECHO_SCENES) -> bool:
    """True when the copy's opening already says what the title says.

    --title defaults to the first line of the copy, and that line is part of
    the copy the scenes narrate. Speaking the title as well then says the same
    sentence twice in a row - title voice, half a second, scene one saying it
    again. That is the common case, not the corner case: it happens on every
    run that does not pass --title.

    Matched on meaning, not on characters, and across the first `lookahead`
    sentences rather than only the first. An exact prefix is merely the
    tidiest way a copy repeats itself. The director rewrites as it splits, so
    the same headline comes back as a reordered clause - a prefix of nothing,
    and still the same sentence twice to anyone watching. It also lands in
    sentence two as often as in sentence one, because sentence one is a hook.

    The ratios are deliberately strict. Skipping the voice on a title the copy
    never says loses the opening line entirely, which is worse than the
    stutter this is here to prevent, so a near miss is left to be spoken.
    """
    if not scenes:
        return False
    wanted = _echo_key(title)
    if not wanted:
        return False
    opening = _echo_key("".join(scene.text or ""
                                for scene in scenes[:max(1, lookahead)]))
    if not opening:
        return False
    # Said outright, either way round: the title is in the copy, or the copy
    # opens on a fragment of it. No length guard here - a quotation is a
    # quotation however short.
    if wanted in opening or opening in wanted:
        return True
    if len(wanted) < TITLE_ECHO_MIN_LENGTH:
        return False
    shared = sum(character in opening for character in wanted) / len(wanted)
    pairs = _character_pairs(wanted)
    overlap = (len(pairs & _character_pairs(opening)) / len(pairs)) if pairs else 0.0
    return shared >= TITLE_ECHO_CHARACTERS and overlap >= TITLE_ECHO_PAIRS


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


# ------------------------------------------------------------------ http ----

def ensure_ok(response: requests.Response, what: str) -> requests.Response:
    """Raise with the response body attached; a bare status code is useless."""
    if response.status_code >= 400:
        body = response.text.strip().replace("\n", " ")[:500]
        raise RuntimeError(f"{what} failed with HTTP {response.status_code}: {body}")
    return response


def never_sent(exc: Exception) -> bool:
    """Whether a failed request provably never reached the server.

    A connect timeout, or a connection refused or unresolvable, fails before a
    byte of the request is written. Anything later - a read timeout, a
    connection dropped mid-response, the "SSL EOF" this network path produces
    - can come after the server has accepted the work and billed it.
    """
    if isinstance(exc, requests.ConnectTimeout):
        return True
    if not isinstance(exc, requests.ConnectionError):
        return False
    reason = exc.args[0] if exc.args else None
    # requests wraps urllib3's MaxRetryError, which carries the real cause.
    return isinstance(getattr(reason, "reason", reason), NewConnectionError)


def request_with_retry(method: str, url: str, *, idempotent: bool = True, **kwargs: Any) -> requests.Response:
    """Send a request, retrying rate limits, server errors and network failures.

    `idempotent=False` is for a request that creates billed work. A timed-out
    POST may already have been accepted upstream, and sending it again pays a
    second time and orphans the first job - so such a request is retried only
    when the failure proves the first attempt never arrived, and otherwise
    fails for --resume to make up. Paying once more for one missing frame is
    the recoverable mistake; paying twice for every slow one is not.
    """
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
            if not idempotent and not never_sent(exc):
                raise RuntimeError(
                    "Network request failed after it may have reached the server, so it was not "
                    f"sent again (a second attempt could be billed twice): {exc}"
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

def compose_image_prompt(cfg: Config, scene: Scene, characters: list[Character],
                         referenced: bool = False) -> str:
    """The full image prompt. `referenced` when a reference frame goes with it (IMAGE_REFERENCE)."""
    style = cfg.image_style_prompt.strip().rstrip(".")
    lookup = {character.id: character.desc for character in characters}
    described = [lookup[cid] for cid in scene.cast if cid in lookup]
    cast_block = ""
    if described:
        cast_block = (
            "Recurring cast, render these exact people with identical face, hair, build and clothing in "
            f"every panel: {'; '.join(described)}. "
        )
    framing = SHOT_SIZES.get(scene.shot_size, SHOT_SIZES[DEFAULT_SHOT_SIZE])
    medium = getattr(cfg, "style", None)
    medium = medium.medium if medium else DEFAULT_STYLE_MEDIUM
    composition = COMPOSITION.format(
        subject=(scene.subject or "").strip().rstrip(".") or DEFAULT_SUBJECT,
        height=framing.subject_height,
        elements=(f"{framing.elements} supporting elements" if framing.elements != 1
                  else "one supporting element"),
    )
    return (
        f"{scene.image_prompt.strip().rstrip('.')}. Usage: one 16:9 frame rendered as {medium}, matched directly "
        "to this exact subtitle. "
        f"{framing.brief} "
        f"{composition} "
        f"{cast_block}{style}. Depict the concrete moment, people, action, setting, and emotion described by this subtitle. "
        "Keep all screens, signs, documents, packaging, and "
        "interfaces blank. No visible text, letters, digits, punctuation, "
        "logos, watermarks, subtitles, or fake interface copy."
        + (f" {REFERENCE_NOTE}" if referenced else "")
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


# A blank line is how copy marks a paragraph, and a paragraph's end is where
# the video takes its breath (pause_after). The splitter used to collapse every
# run of newlines into one, so the director never saw a paragraph: copy.txt's
# five reached it as a single block, and the breaths were a guess about a
# structure the writer had already stated.
PARAGRAPH_BREAK = re.compile(r"\n[^\S\n]*\n")


def paragraph_batches(copy: str, batch_size: int = 360) -> list[tuple[str, bool]]:
    """Split the copy for the director: (part, whether it ends at a paragraph end).

    Sentences within a paragraph are joined by one newline and paragraphs by a
    blank line, so the director sees the structure. The flag is for the one
    paragraph end it cannot see - the one its part of the copy was cut at.
    """
    units: list[tuple[str, bool]] = []          # (sentence, whether it opens a paragraph)
    for paragraph in PARAGRAPH_BREAK.split(copy):
        sentences = [unit.strip() for unit in re.split(r"(?<=[。！？；!?])|\n+", paragraph) if unit.strip()]
        units += [(sentence, position == 0) for position, sentence in enumerate(sentences)]
    batches: list[tuple[str, bool]] = []
    current = ""
    for unit, opens_paragraph in units:
        candidate = f"{current}{chr(10) * (2 if opens_paragraph else 1)}{unit}" if current else unit
        if current and len(re.sub(r"\s+", "", candidate)) > batch_size:
            batches.append((current, opens_paragraph))
            current = unit
        else:
            current = candidate
    if current:
        batches.append((current, True))
    return batches or [(copy, True)]


def storyboard_batches(copy: str, batch_size: int = 360) -> list[str]:
    return [part for part, _ in paragraph_batches(copy, batch_size)]


# What a frame is allowed to be about, and how it should read to somebody
# who is not looking for anything.
#
# Kept SHORT on purpose. This is the system prompt for a model that reasons
# before it answers, and an earlier version explained every rule, naming the
# failure each was written against. Measured on one storyboard call it spent
# 33,000 characters of reasoning and emitted no answer at all within three
# minutes - long enough that the call died on the connection's idle limit
# before the model was ready to speak. The rules below are the same rules;
# what is gone is the argument for them, which only a person reading this
# file needs, and which now sits here where it costs nothing at runtime.
#
# The failures being guarded against, for whoever edits this next:
#
# - "One image per sentence" gets read as "everything in the sentence, in one
#   image", so the subject is asked for as its own field: a field holds one
#   answer where a description quietly holds four.
# - A person used to be chosen three times over - as the fallback for an
#   abstract line, as a stated preference over objects, and again whenever a
#   sentence was about feeling. On copy that is all feeling that is every
#   frame, which is what the reference frames showed going wrong.
# - An unfurnished frame makes the subject look cut out and pasted on, so a
#   room is required rather than suggested.
# - A symbolic figure inside a real room reads - a silhouette in a doorway -
#   where the same shapes floating on nothing do not. Only the second is
#   banned, and the earlier blanket ban overshot.
DIRECTION = (
    "Frame. One picture, one subject, named in \"subject\" as two to five English words. A subject is a PLACE "
    "(\"a lit kitchen at midnight\"), an OBJECT (\"a cracked photo frame\") or a PERSON (\"a woman at a kitchen "
    "table\"), and the three rank equally. Not an abstraction, and never two things joined by \"and\". "
    "Do not default to a person: at least half of the scenes in this batch must have a place or an object as "
    "their subject, and carry a feeling "
    "with the room it happens in - an unmade bed, a cold meal, a door left shut - unless the expression itself "
    "is the information. "
    "Write image_prompt as one sentence of at most 30 words: the subject, what it is doing, and only the few "
    "things the sentence turns on. "
    "Set every scene in a named, furnished room - two or three ordinary props that say whose room it is and "
    "what time it is, and one light source. "
    "At most one symbolic element per frame and it has to sit in the room. No diagram, chart, floating cluster "
    "of objects, collage, split screen, before-and-after pair, grid of panels or inset. "
    "These are watched on a phone: take the legible reading over the clever one, and keep poses and expressions "
    "ordinary."
)


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
        "You are the storyboard director for a Chinese narration video rendered entirely as "
        f"{cfg.style.medium}. "
        f"Split this part ({batch_number}/{batch_count}) of the copy into about {batch_target} independent subtitle scenes, "
        f"never more than {batch_maximum}. {length_instruction}Preserve the complete meaning and original order. "
        "One subtitle scene must map to exactly one image. "
        f"{DIRECTION} "
        "If the subtitle describes a concrete event, depict that event literally in a believable everyday setting. If it is "
        "abstract, use the simplest human situation that communicates the sentence without changing its meaning. The scene "
        f"content should feel true to life, but every frame must remain {cfg.style.medium}, never {cfg.style.avoid}. "
        "Do not force a finance theme. "
        "Never add charts, tables, dashboards, graphs, market arrows, coins, banks, office imagery, or decorative business symbols "
        "unless that exact subtitle genuinely calls for them. "
        # What this used to say was "do not visually magnify an incidental
        # word at the expense of the full sentence", which is the right worry
        # and the wrong instruction: read as written it forbids emphasising
        # anything, and the frames came back with nothing emphasised at all.
        # The subject is still chosen to carry the sentence - it just has to
        # be one thing rather than an even spread of five.
        "The subject has to be what the sentence is actually about, not a passing noun in it: a sentence about a decision "
        "is not a picture of the desk it was made at. "
        "Keep screens, "
        "signs, documents, packaging, and interfaces blank; do not request visible text, letters, digits, punctuation, logos, "
        "watermarks, subtitles, speech bubbles, or fake interface copy. "
        f"{cast_instruction}"
        "Set \"cast\" on each scene to the ids of the characters visible in that panel, or [] if nobody recurring appears. "
        "Set \"shot_size\" to vary the framing the way an editor would, never leaving it on one value for long. "
        "\"wide\" opens a section, establishes a place, or carries a sentence about society or the world at large; "
        "\"medium\" is the default for describing an event; \"close\" is for a feeling, a decision, a turn, or a "
        "conclusion, where the face is the point. Aim for roughly one wide and one close in every four scenes. "
        # The cap the picture is actually drawn to, quoted from the same table
        # the image prompt reads. A brief that asks for five things in a
        # close-up is a brief the frame has to throw four of away.
        "Each framing limits what may share the frame: "
        + "; ".join(f'{name} allows at most {size.elements} besides the subject'
                    for name, size in SHOT_SIZES.items())
        + ". "
        "Set \"pause_after\" to true on the scene that ends a paragraph, so the video can take a breath there, "
        "and leave it false inside one. A blank line in the copy marks where a paragraph ends; where the copy "
        "has none, judge where each complete thought ends. "
        # The music bed is chosen from this, and it costs nothing: one more
        # field on a call that is already being made for every batch. Asking
        # separately would be a second request per video for one word.
        f"Set \"mood\" to the one or two labels from this list that best describe how this part of the copy "
        f"feels, most telling first, and use these words exactly: {'、'.join(BGM_MOODS)}. "
        'Return JSON only: {"characters":[{"id":"A","desc":"English description"}],'
        '"mood":["紧张"],'
        '"scenes":[{"text":"Chinese scene copy","subject":"a woman at a kitchen table",'
        '"image_prompt":"English image prompt",'
        '"shot_size":"medium","pause_after":false,"cast":["A"]}]}'
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
        shot_size = item.get("shot_size")
        shot_size = shot_size.strip().lower() if isinstance(shot_size, str) else ""
        if shot_size not in SHOT_SIZES:
            shot_size = DEFAULT_SHOT_SIZE
        # Missing rather than invalid: a scene is usable without one, and
        # refusing a whole batch over a field the model skipped would throw
        # away the storyboard it did write. compose_image_prompt names the
        # subject generically instead.
        subject = item.get("subject")
        subject = subject.strip() if isinstance(subject, str) else ""
        scenes.append(Scene(text=text.strip(), image_prompt=image_prompt.strip(),
                            subject=subject, shot_size=shot_size,
                            pause_after=bool(item.get("pause_after")),
                            cast=cast))
    return scenes


def moods_from_payload(data: dict[str, Any]) -> list[str]:
    """The mood labels the director chose, keeping only ones we know.

    Filtered against BGM_MOODS rather than taken as given: the value is used
    to match a filename, so a word invented by the model matches nothing and
    would only push a real label out of the ranking.
    """
    raw = data.get("mood")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    moods: list[str] = []
    for item in raw:
        mood = str(item).strip()
        if mood in BGM_MOODS and mood not in moods:
            moods.append(mood)
    return moods


def characters_from_payload(data: dict[str, Any]) -> list[Character]:
    characters: list[Character] = []
    for item in data.get("characters") or []:
        if not isinstance(item, dict):
            continue
        cid, desc = item.get("id"), item.get("desc")
        if isinstance(cid, str) and isinstance(desc, str) and cid.strip() and desc.strip():
            characters.append(Character(cid.strip(), desc.strip()))
    return characters


def plan_scenes(cfg: Config, copy: str) -> tuple[list[Scene], list[Character], list[str]]:
    target, maximum = scene_limits(cfg.scene_length_mode, cfg.scene_characters, copy)
    batches = paragraph_batches(copy)
    total_characters = max(1, len(re.sub(r"\s+", "", copy)))
    scenes: list[Scene] = []
    characters: list[Character] = []
    # Counted across batches rather than taken from the first. A batch is a
    # few hundred characters, so the opening one is the hook and not
    # necessarily the video; the mood the whole copy keeps returning to is the
    # one the bed has to sit under for three minutes.
    mood_counts: dict[str, int] = {}
    report_progress("Storyboard", 0, len(batches))

    for batch_number, (batch, ends_paragraph) in enumerate(batches, 1):
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
        if ends_paragraph:
            # The director sees the blank lines inside its part of the copy,
            # but not the one its part was cut at.
            batch_scenes[-1].pause_after = True
        for rank, mood in enumerate(moods_from_payload(data)):
            # First-named counts for more, so a batch that is mostly tense and
            # a little sad does not average into neither.
            mood_counts[mood] = mood_counts.get(mood, 0) + (2 if rank == 0 else 1)
        scenes.extend(batch_scenes)
        report_progress("Storyboard", batch_number, len(batches))

    if not scenes:
        raise RuntimeError("Storyboard model returned no scenes.")
    if len(scenes) > maximum:
        raise RuntimeError(f"Storyboard returned {len(scenes)} scenes; the safety limit for this copy is {maximum}.")
    moods = [mood for mood, _ in sorted(
        mood_counts.items(), key=lambda item: (-item[1], BGM_MOODS.index(item[0])))]
    return scenes, characters, moods[:BGM_MOOD_LIMIT]


# Sent with every storyboard request. Ark's reasoning models think at length
# by default and stream nothing but reasoning while they do; DeepSeek's API
# accepts the same field. Turned off because the task is extraction.
STORYBOARD_THINKING = {"type": "disabled"}

# How long the stream may go without a single byte before it is abandoned.
# This is a gap between chunks, not a limit on the whole answer: a model can
# take as long as it likes provided it keeps sending, and reasoning models
# stream their thinking, so they keep sending.
STREAM_IDLE_SECONDS = 120
STREAM_ATTEMPTS = 3


def stream_chat(url: str, api_key: str, body: dict[str, Any], what: str) -> str:
    """POST a chat completion with streaming on, and return the answer text.

    Streamed for one reason. A model that thinks before it answers sends
    nothing until it is ready, and this network path drops a connection that
    has been silent for about 69 seconds - so a non-streamed call succeeded or
    failed depending on how long the model happened to deliberate that minute.
    Measured on one brief, the same director took 58s once and 86s the next.
    A stream keeps bytes moving while it reasons.

    Reasoning is read and discarded: only `content` is the answer. A stream
    that ends with reasoning and no answer is reported as exactly that, naming
    the model, because the alternative is a JSON error about an empty string.
    """
    last_error: Exception | None = None
    for attempt in range(STREAM_ATTEMPTS):
        response = post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={**body, "stream": True},
            stream=True,
            timeout=(30, STREAM_IDLE_SECONDS),
        )
        ensure_ok(response, what)
        # requests guesses ISO-8859-1 for a text/event-stream with no charset,
        # which turns every Chinese character in the storyboard into mojibake.
        response.encoding = "utf-8"
        parts: list[str] = []
        reasoned = 0
        try:
            for line in response.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    delta = (json.loads(payload).get("choices") or [{}])[0].get("delta") or {}
                except (ValueError, AttributeError, IndexError):
                    continue
                reasoned += len(delta.get("reasoning_content") or "")
                parts.append(delta.get("content") or "")
        except requests.RequestException as exc:
            # Cut mid-answer. What arrived is not a storyboard, so start over.
            last_error = exc
            print(f"{what}: the stream was cut ({exc.__class__.__name__}); "
                  f"retrying ({attempt + 1}/{STREAM_ATTEMPTS})...", flush=True)
            time.sleep((attempt + 1) * 3)
            continue
        finally:
            response.close()
        answer = "".join(parts).strip()
        if answer:
            return answer
        raise RuntimeError(
            f"{what}: {body.get('model')} returned no answer - {reasoned} characters of reasoning "
            "and nothing after it. The model deliberated instead of answering even though "
            "thinking was switched off; this provider may ignore the flag. Use a model that "
            "answers directly, such as DEEPSEEK_MODEL=deepseek-chat."
        )
    raise RuntimeError(f"{what}: the stream was cut {STREAM_ATTEMPTS} times: {last_error}")


def request_storyboard(cfg: Config, prompt: str, batch: str, batch_target: int) -> dict[str, Any]:
    """Call the director, retrying once at a lower temperature on bad JSON."""
    last_content = ""
    for attempt, temperature in enumerate((0.55, 0.2)):
        last_content = stream_chat(
            f"{cfg.text_base_url}/chat/completions",
            cfg.text_api_key,
            {
                "model": cfg.text_model,
                "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": batch}],
                "temperature": temperature,
                "max_tokens": min(8192, max(1536, batch_target * 200)),
                "response_format": {"type": "json_object"},
                "thinking": STORYBOARD_THINKING,
            },
            "Storyboard request",
        )
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
    write_atomically(target, b"".join(audio_chunks))


def populate_audio_durations(scenes: list[Scene]) -> None:
    from pyJianYingDraft import AudioMaterial

    for index, scene in enumerate(scenes, 1):
        if not scene.audio_path or not Path(scene.audio_path).is_file():
            raise RuntimeError(f"Missing voice-over audio for scene {index}.")
        scene.duration_us = AudioMaterial(scene.audio_path).duration


# ------------------------------------------------------------------ image ----

# Matching every frame to one picture (IMAGE_REFERENCE=anchor).
#
# The cast and the look are otherwise held together by words alone - the same
# character description and style prompt in every request - with a light
# grade laid over whatever drift gets through. Seedream can also be given a
# picture to match, the images API's `image` field, and a picture holds a face
# and a palette far better than a sentence does. So one frame, the anchor, is
# drawn first on its own, and every other frame is sent with a copy of it.
#
# Off by default. It is only as good as the endpoint's support for reference
# images, which varies by model and plan, and unlike everything else in this
# file it has not been measured against the reference video. An endpoint that
# rejects it fails the frame with a message naming this setting; nothing is
# billed for a rejected request, and --resume carries on once it is off.
IMAGE_REFERENCE_MODES = ("off", "anchor")
# Wide enough to carry a face and a palette, small enough that sending it with
# every frame costs nothing noticeable. The frames themselves are 2560 wide.
REFERENCE_WIDTH = 1280
# Without this a reference is read as "draw this again": the same room, the
# same framing, frame after frame.
REFERENCE_NOTE = (
    "The attached reference image fixes only the drawing style, the palette and the recurring people's faces, "
    "hair and clothes; do not copy its composition, framing, subject, setting or props."
)


def anchor_scene(scenes: list[Scene]) -> int:
    """Which frame the others are matched to: the first to show the recurring cast.

    A reference with a face in it holds the face; one without holds only the
    look. With no recurring cast at all, the first frame anchors the look.
    """
    return next((index for index, scene in enumerate(scenes) if scene.cast), 0)


def reference_image(path: Path) -> str:
    """A frame as the images API takes a reference: a downscaled JPEG data URI."""
    from PIL import Image

    with Image.open(path) as picture:
        picture = picture.convert("RGB")
        if picture.width > REFERENCE_WIDTH:
            height = round(picture.height * REFERENCE_WIDTH / picture.width)
            picture = picture.resize((REFERENCE_WIDTH, height), Image.LANCZOS)
        buffer = io.BytesIO()
        picture.save(buffer, format="JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def generate_image(cfg: Config, prompt: str, target: Path, seed: int | None = None,
                   reference: str | None = None) -> None:
    """Draw one frame. `seed` is the scene's own (see image_seed), not the setting;
    `reference` is a frame to match, as reference_image makes it."""
    payload: dict[str, Any] = {
        "model": cfg.ark_image_model,
        "prompt": prompt,
        "size": cfg.ark_image_size,
        "sequential_image_generation": "disabled",
        "response_format": cfg.ark_image_response_format,
        "output_format": cfg.ark_image_output_format,
        "watermark": False,
    }
    if seed is not None:
        payload["seed"] = seed
    if reference is not None:
        payload["image"] = reference
    response = post(
        cfg.ark_image_url,
        headers={"Authorization": f"Bearer {cfg.ark_api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=300,
        # Billed per image. A request cut off after the server took it is not
        # sent again; --resume draws what is missing.
        idempotent=False,
    )
    if reference is not None and response.status_code == 400:
        body = response.text.strip().replace("\n", " ")[:300]
        raise RuntimeError(
            f"Image generation refused the request with a reference image attached (HTTP 400: {body}). "
            f"IMAGE_REFERENCE=anchor sends one; if {cfg.ark_image_model} or this plan does not take reference "
            "images, set IMAGE_REFERENCE=off and --resume."
        )
    ensure_ok(response, "Image generation")
    body = response.json()
    images = body.get("data") or body.get("images") or []
    if not images:
        raise RuntimeError(f"Image API returned no image data: {body}")
    image = images[0]
    if image.get("b64_json"):
        write_atomically(target, base64.b64decode(image["b64_json"]))
        return
    image_url = image.get("url")
    if not image_url:
        raise RuntimeError(f"Image API returned an image without data or URL: {image}")
    download = ensure_ok(get(image_url, timeout=300), "Image download")
    write_atomically(target, download.content)


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


def validate_draft_target(cfg: Config, draft_name: str, replace: bool) -> None:
    if (cfg.draft_dir / draft_name).exists() and not replace:
        raise RuntimeError(
            f"Draft already exists: {cfg.draft_dir / draft_name}. "
            "Use a new --draft-name, or add --replace to overwrite it. "
            "Overwriting deletes the whole draft folder, including edits made in Jianying."
        )


def validate_plan_target(asset_root: Path, draft_name: str, replace: bool) -> None:
    """Planning writes no draft, but it does replace the run's storyboard."""
    if (asset_root / "manifest.json").is_file() and not replace:
        raise RuntimeError(
            f"output/{draft_name} already holds a storyboard. Use a new --draft-name, "
            f"--resume {draft_name} --plan-only to print the one it has, "
            "or add --replace to plan it afresh."
        )


def build_draft(cfg: Config, scenes: list[Scene], draft_name: str, replace: bool,
                title: str, title_audio: Path | None = None,
                opening_sound: Path | None = None,
                bgm: Path | None = None) -> Path:
    """Write the draft. `bgm` is the track resolve_bgm settled on.

    Falling back to cfg.bgm_path when it is None is not a second decision:
    resolve_bgm returns exactly that whenever BGM_PATH is set, so the two
    cannot disagree, and a caller that has not resolved anything still gets
    the explicitly configured track.
    """
    from pyJianYingDraft import (
        AudioMaterial,
        AudioSegment,
        ClipSettings,
        DraftFolder,
        FilterType,
        FontType,
        KeyframeProperty,
        TextBackground,
        TextBorder,
        TextIntro,
        TextOutro,
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
    title_outro = (
        None if cfg.title_outro.lower() in {"", "none", "off"}
        else resolve_enum(TextOutro, cfg.title_outro, "TITLE_OUTRO")
    )
    grade = (
        None if cfg.color_grade.lower() in {"", "none", "off"} or not cfg.color_grade_intensity
        else resolve_enum(FilterType, cfg.color_grade, "COLOR_GRADE")
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
    # The title is one text segment per line, and the lines are on screen at
    # the same time, so each needs its own track: a Jianying text track holds
    # one segment at a time.
    title_lines = split_title_lines(
        title, characters_per_line(cfg.title_size, cfg.title_max_line_width, cfg.subtitle_em_px)
    )
    title_tracks = [
        draft.append_track(TrackSpec(TrackType.text, title_track_name(index)))
        for index in range(len(title_lines))
    ]
    bgm_path = bgm or cfg.bgm_path
    bgm_material = AudioMaterial(str(bgm_path)) if bgm_path else None
    bgm_track = draft.append_track(TrackSpec(TrackType.audio, "BGM")) if bgm_material else None
    grade_track = draft.append_track(TrackSpec(TrackType.filter, COLOR_GRADE_TRACK)) if grade else None

    subtitle_font = resolve_enum(FontType, cfg.subtitle_font, "SUBTITLE_FONT")
    title_font = resolve_enum(FontType, cfg.title_font, "TITLE_FONT")
    if cfg.subtitle_style == "box":
        subtitle_colour, subtitle_border, subtitle_background = (
            (0.0, 0.0, 0.0),
            TextBorder(color=(1.0, 1.0, 1.0), width=cfg.subtitle_border_width),
            TextBackground(color="#000000", alpha=0.3, round_radius=0.2, height=0.14,
                           width=0.14, horizontal_offset=0.5, vertical_offset=0.5),
        )
    elif cfg.subtitle_style == "outline":
        # White fill on a black stroke and a soft shadow, no plate. Cleanest
        # over pale art, where a dark plate reads as a bar stuck on the frame.
        subtitle_colour, subtitle_border, subtitle_background = (
            (1.0, 1.0, 1.0),
            TextBorder(color=(0.0, 0.0, 0.0), width=cfg.subtitle_border_width),
            None,
        )
    else:
        # The reference treatment: white text on a thin dark stroke, sitting on
        # a dark translucent plate roughly one and a half line-heights tall.
        # The plate is what lets the caption stay this small and this light and
        # still hold against a busy panel; without it the size would have to go
        # back up and the caption would start competing with the picture.
        subtitle_colour, subtitle_border, subtitle_background = (
            (1.0, 1.0, 1.0),
            TextBorder(color=(0.04, 0.06, 0.10), width=cfg.subtitle_border_width),
            TextBackground(color="#0B1826", alpha=0.55, round_radius=0.12, height=0.30,
                           width=0.10, horizontal_offset=0.5, vertical_offset=0.5),
        )

    materials = [AudioMaterial(scene.audio_path or "") for scene in scenes]
    for scene, material in zip(scenes, materials, strict=True):
        scene.duration_us = material.duration

    # The title is read aloud over the stinger's decay, and the copy waits for
    # it. Without the wait the first line talks over the title's own voice: the
    # configured 0.8 s lead is enough for the stinger alone and about half of
    # what a spoken title needs.
    title_material = AudioMaterial(str(title_audio)) if title_audio else None
    # The breath after the title and the cap on the whole head are baseline
    # numbers like everything else in this file; cfg already holds its own
    # durations on the video's clock, and these two live at module scope, so
    # they are the two that have to be put on it here.
    tail_us = paced_us(TITLE_TAIL_US, cfg.speed)
    cap_us = paced_us(MAX_OPENING_LEAD_US, cfg.speed)
    if title_material is not None and not title_voice_fits(
            title_material.duration, cfg.title_lead_us, tail_us, cap_us):
        # Too long to read before the copy has to start. The type stays; only
        # the voice-over goes. Silently clamping instead would talk the copy
        # over the tail of its own title.
        print(f"Title voice-over skipped: reading it takes "
              f"{title_material.duration / 1e6:.1f}s and the opening may not "
              f"run past {cap_us / 1e6:.1f}s. Use a shorter --title.")
        title_material = None
    lead_us = opening_lead(cfg.opening_lead_us,
                           title_material.duration if title_material else 0,
                           cfg.title_lead_us, tail_us, cap_us)

    timings, total_duration = plan_timeline(
        [material.duration for material in materials],
        [scene.pause_after for scene in scenes],
        lead_us, cfg.paragraph_pause_us, cfg.ending_hold_us,
    )

    camera = plan_camera(scenes)
    for index, (scene, audio, timing) in enumerate(zip(scenes, materials, timings, strict=True), 1):
        narration_range = Timerange(timing.narration_start, timing.narration_duration)

        # One image, one uninterrupted segment. Scenes are never split, and the
        # cut into the next scene is hard: no transition, no intro animation.
        # All of the motion comes from the keyframed camera move. The picture
        # also covers the pause after its scene and the hold at the end, so a
        # breath never shows as a black frame.
        video = VideoSegment(scene.image_path or "",
                             Timerange(timing.visual_start, timing.visual_duration))
        if cfg.ken_burns_rate > 0:
            apply_ken_burns(video, KeyframeProperty, timing.visual_duration, camera[index - 1],
                            cfg.ken_burns_rate)
        draft.add_segment(video, video_track)

        subtitle = TextSegment(
            scene.text, narration_range,
            font=subtitle_font,
            style=TextStyle(size=cfg.subtitle_size, color=subtitle_colour, align=1,
                            letter_spacing=cfg.subtitle_letter_spacing,
                            auto_wrapping=True, max_line_width=cfg.subtitle_max_line_width),
            clip_settings=ClipSettings(transform_x=0.0, transform_y=subtitle_baseline_y(
                cfg.subtitle_y,
                subtitle_line_count(scene.text, cfg.subtitle_size,
                                    cfg.subtitle_max_line_width, cfg.subtitle_em_px),
                cfg.subtitle_size, cfg.subtitle_em_px,
            )),
            border=subtitle_border,
            background=subtitle_background,
            shadow=TextShadow(alpha=0.7, diffuse=25.0, distance=8.0, angle=-90.0),
        )
        if subtitle_intro is not None:
            subtitle.add_animation(subtitle_intro, duration=cfg.subtitle_animation_us)
        draft.add_segment(subtitle, narration_subtitle_track)

        draft.add_segment(AudioSegment(audio, narration_range), audio_track)

    if grade_track is not None and grade is not None:
        # One grade across the whole video. Independently generated panels drift
        # in temperature and brightness; a single light pass pulls them together.
        draft.add_filter(grade, Timerange(0, total_duration), COLOR_GRADE_TRACK,
                         cfg.color_grade_intensity)

    title_span: tuple[int, int] | None = None
    if title_material is not None:
        title_span = (cfg.title_lead_us, cfg.title_lead_us + title_material.duration)
        draft.add_segment(
            AudioSegment(title_material,
                         Timerange(cfg.title_lead_us, title_material.duration)),
            audio_track)

    # Defaulting to the configured cue rather than to nothing. The cue is
    # required and always resolvable from cfg, so there is no "no cue" case to
    # express - and a caller that omits the argument previously got
    # AudioMaterial("None") and a broken draft.
    add_opening_sound(cfg, draft, opening_sfx_track, total_duration,
                      opening_sound or cfg.opening_sound_path)
    if watermark_track is not None and cfg.watermark_path:
        watermark = VideoSegment(
            str(cfg.watermark_path), Timerange(0, total_duration),
            clip_settings=ClipSettings(scale_x=1.0, scale_y=1.0, transform_x=0.0, transform_y=0.0),
        )
        draft.add_segment(watermark, watermark_track)

    # The overlay holds at least as long as the lead: a title that vanishes
    # while its own voice is still reading it reads as a timing bug.
    title_hold = max(cfg.title_us, lead_us)
    add_title(cfg, draft, title_tracks, title_lines, total_duration, title_intro,
              title_outro, title_font, title_hold)
    if bgm_material is not None and bgm_track is not None:
        speech = [(timing.narration_start, timing.narration_end) for timing in timings]
        if title_span is not None:
            speech.insert(0, title_span)
        add_bgm(cfg, draft, bgm_track, bgm_material, total_duration, speech)

    draft.save()
    write_draft_meta(cfg.draft_dir / draft_name, draft_name)
    return cfg.draft_dir / draft_name


def write_draft_meta(draft_path: Path, draft_name: str) -> None:
    """Name the draft in its own metadata.

    pyJianYingDraft's create_draft copies its meta template verbatim, so
    draft_name, draft_fold_path and draft_root_path are left empty strings.
    Jianying lists drafts by folder, which is why this has never stopped one
    appearing - but a draft that does not know its own name or where it lives
    is wrong in a way that costs nothing to fix, and the sibling tool that
    writes the same format fills these in deliberately.
    """
    meta_path = draft_path / "draft_meta_info.json"
    if not meta_path.is_file():
        return
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
        meta["draft_name"] = draft_name
        meta["draft_fold_path"] = str(draft_path)
        meta["draft_root_path"] = str(draft_path.parent)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    except (ValueError, OSError) as exc:
        # The draft itself is written and openable; its label is not worth
        # failing a finished run over.
        print(f"Could not label the draft metadata: {exc}")


def add_opening_sound(cfg: Config, draft: Any, track: Any, total_duration: int,
                      sound_path: Path) -> None:
    from pyJianYingDraft import AudioMaterial, AudioSegment, Timerange

    material = AudioMaterial(str(sound_path))
    duration = min(material.duration, total_duration)
    if duration <= 0:
        return
    segment = AudioSegment(material, Timerange(0, duration),
                           source_timerange=Timerange(0, duration), volume=cfg.opening_sound_volume)
    segment.add_fade(0, min(200_000, duration // 2))
    draft.add_segment(segment, track)


def add_title(cfg: Config, draft: Any, tracks: list[Any], lines: list[str], total_duration: int,
              title_intro: Any, title_outro: Any, font: Any,
              hold_us: int | None = None) -> None:
    """Stack the title lines and colour them, ramped or one colour per line.

    The reference title runs warm white into crimson across a two-line block.
    Each line is still its own segment, because that is what puts the line
    pitch under our control - the reference sets it tighter than any text
    default would - and the ramp runs through the lines in reading order.
    """
    from pyJianYingDraft import ClipSettings, TextBorder, TextSegment, TextShadow, TextStyle, Timerange

    duration = min(total_duration, cfg.title_us if hold_us is None else hold_us)
    if duration <= 0 or not lines:
        return
    colours = TITLE_PRESETS[cfg.title_style]
    fills = title_fills(lines, colours, cfg.title_ramp)
    size = fit_title_size(lines, cfg.title_size, cfg.title_max_line_width, cfg.subtitle_em_px)
    offsets = title_line_offsets(len(lines), cfg.title_y, size, cfg.subtitle_em_px)
    for line, track, offset, line_fills in zip(lines, tracks, offsets, fills, strict=True):
        segment = TextSegment(
            line, Timerange(0, duration),
            font=font,
            style=TextStyle(size=size, bold=True, align=1, color=line_fills[0],
                            letter_spacing=cfg.subtitle_letter_spacing, auto_wrapping=False),
            border=TextBorder(color=colours.border, width=cfg.title_border_width),
            # Hard and offset down-right rather than soft and centred: the
            # reference shadow reads as a second layer of type behind the first.
            shadow=TextShadow(alpha=0.85, diffuse=8.0, distance=14.0, angle=-55.0),
            clip_settings=ClipSettings(transform_x=0.0, transform_y=offset),
        )
        paint_characters(segment, line_fills)
        if title_intro is not None:
            segment.add_animation(title_intro, duration=min(500_000, duration))
        if title_outro is not None:
            segment.add_animation(title_outro, duration=min(500_000, duration))
        draft.add_segment(segment, track)


def title_voice_fits(title_audio_us: int, title_lead_us: int,
                     tail_us: int = TITLE_TAIL_US,
                     cap_us: int = MAX_OPENING_LEAD_US) -> bool:
    """Whether reading the title aloud leaves the copy starting in time."""
    return title_lead_us + title_audio_us + tail_us <= cap_us


def opening_lead(configured_us: int, title_audio_us: int, title_lead_us: int,
                 tail_us: int = TITLE_TAIL_US,
                 cap_us: int = MAX_OPENING_LEAD_US) -> int:
    """How long the head holds before the first line of the copy.

    The configured lead is a floor, not the answer. It is 0.8 s - enough for the
    stinger to land alone, and about half of what a spoken title needs. Left at
    0.8 s the copy would start talking over the title's own voice.

    It is not an unbounded ceiling either. Callers drop the voice-over rather
    than let the head run past `cap_us`; this clamps as a second line of
    defence so no arithmetic path can produce a nine-second opening.
    """
    if title_audio_us <= 0:
        return configured_us
    return min(max(configured_us, title_lead_us + title_audio_us + tail_us),
               max(configured_us, cap_us))


def plan_timeline(durations: list[int], pauses: list[bool], lead_us: int,
                  pause_us: int, hold_us: int) -> tuple[list[SceneTiming], int]:
    """Lay out narration and picture, with room to breathe.

    A scene marked `pause_after` is followed by a beat of BGM only, so the
    paragraph breaks in the copy are audible instead of every sentence running
    into the next. The outgoing picture holds through that beat rather than
    cutting to black, and the last picture holds again at the end so the video
    does not stop dead on the final syllable.
    """
    timings: list[SceneTiming] = []
    cursor = lead_us
    last = len(durations) - 1
    for index, duration in enumerate(durations):
        gap = pause_us if (pauses[index] and index != last) else 0
        hold = hold_us if index == last else 0
        visual_start = 0 if index == 0 else cursor
        visual_duration = duration + gap + hold + (lead_us if index == 0 else 0)
        timings.append(SceneTiming(cursor, duration, visual_start, visual_duration))
        cursor += duration + gap
    return timings, cursor + (hold_us if durations else 0)


def bgm_volume_envelope(speech_spans: list[tuple[int, int]], total_us: int,
                        base: float, lift: float, ramp_us: int) -> list[tuple[int, float]]:
    """Absolute (time, volume) points: quiet under speech, lifted in the gaps.

    Head and tail fades are left to the loop plan's fades, which multiply with
    these levels, so this function only has to express the ducking.
    """
    if total_us <= 0:
        return []
    if not speech_spans:
        return [(0, lift), (total_us, lift)]

    points: list[tuple[int, float]] = []

    def push(time: float, volume: float) -> None:
        moment = max(0, min(total_us, round(time)))
        if points and moment <= points[-1][0]:
            if moment == points[-1][0]:
                points[-1] = (moment, volume)
            return
        points.append((moment, volume))

    # Only lift where there is genuinely room for the ramp, otherwise the music
    # would still be climbing when the next line starts.
    push(0, lift if speech_spans[0][0] >= 2 * ramp_us else base)
    for index, (start, end) in enumerate(speech_spans):
        previous_end = speech_spans[index - 1][1] if index else 0
        next_start = speech_spans[index + 1][0] if index + 1 < len(speech_spans) else total_us
        if start - previous_end >= 2 * ramp_us:
            push(start - ramp_us, lift)
        push(start, base)
        push(end, base)
        if next_start - end >= 2 * ramp_us:
            push(end + ramp_us, lift)
    push(total_us, points[-1][1])
    return points


def sample_envelope(points: list[tuple[int, float]], moment: int) -> float:
    """Linear interpolation, so a loop seam lands on the level it should."""
    if not points:
        return 0.0
    if moment <= points[0][0]:
        return points[0][1]
    for (t0, v0), (t1, v1) in zip(points, points[1:], strict=False):
        if t0 <= moment <= t1:
            if t1 == t0:
                return v1
            return v0 + (v1 - v0) * (moment - t0) / (t1 - t0)
    return points[-1][1]


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


def add_bgm(cfg: Config, draft: Any, track: Any, material: Any, total_duration: int,
            speech_spans: list[tuple[int, int]]) -> None:
    """Loop the BGM under the narration, ducked under speech and lifted in the gaps.

    Volume keyframes carry the level and the segment volume is left at 1.0, so
    the result is the same whether Jianying treats keyframes as replacing the
    static volume or as multiplying it. The loop plan's fades multiply on top
    and handle the head, the tail and the seams.
    """
    from pyJianYingDraft import AudioSegment, Timerange

    if cfg.bgm_volume <= 0:
        return
    lift = max(cfg.bgm_volume, cfg.bgm_lift_volume)
    envelope = bgm_volume_envelope(speech_spans, total_duration, cfg.bgm_volume, lift, cfg.bgm_ramp_us)
    for start, duration, fade_in, fade_out in bgm_loop_plan(total_duration, material.duration):
        segment = AudioSegment(material, Timerange(start, duration),
                               source_timerange=Timerange(0, duration), volume=1.0)
        segment.add_keyframe(0, sample_envelope(envelope, start))
        for moment, volume in envelope:
            if start < moment < start + duration:
                segment.add_keyframe(moment - start, volume)
        segment.add_keyframe(duration, sample_envelope(envelope, start + duration))
        segment.add_fade(fade_in, fade_out)
        draft.add_segment(segment, track)


# ---------------------------------------------------------------- assets ----
#
# Every clip and frame on disk is named after what made it: the scene number,
# for people, then a short digest of every input that decides its content.
#
#     audio/07_3fa9c2e1d0.mp3      text, voice, speech rate, loudness, model
#     images/07_b41f09aa2c.png     prompt, subject, framing, cast, style,
#                                  model, size, seed, take
#
# The name is the proof. Files used to be called 07.mp3 and 07.png and were
# adopted back by that name alone, which let a run pick up a file made for
# other inputs and still report success:
#
#   - a fresh run under an existing --draft-name took the previous run's clip
#     for scene 7, whatever the new scene 7 said. Re-running after a failure
#     re-splits the copy, so every subtitle sat over narration reading a
#     different sentence, and no API call was made to say otherwise;
#   - a resume after changing IMAGE_STYLE_PRESET kept every old frame and laid
#     the new style's grade and title over them, and one after changing the
#     voice read the missing lines in the new voice beside the old ones.
#
# Now a file is used only if it was made from exactly the inputs its scene has
# now. Reuse is safe by construction, anything stale is simply not found, and
# a crashed run's finished files are still picked up.
#
# What the user controls goes into a key; this file's own wording does not.
# The composition brief gets rewritten, and a resume after an upgrade keeps
# the frames it already paid for rather than quietly redrawing every one -
# the same call manifest v8 made.
ASSET_KEY_LENGTH = 10


def fingerprint(*parts: Any) -> str:
    """A short digest of `parts`, stable across runs and machines."""
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:ASSET_KEY_LENGTH]


def narration_key(cfg: Config, text: str) -> str:
    """Everything that decides what a spoken clip sounds like.

    The speech rate is the combined one - VIDEO_SPEED with the voice's own
    trim - so a clip read at another speed is a different clip, which is what
    the manifest's `speed` used to be kept for.
    """
    return fingerprint("narration", text.strip(), cfg.ark_tts_model, cfg.ark_tts_voice_type,
                       cfg.ark_tts_speech_rate, cfg.ark_tts_loudness_rate)


def image_seed(cfg: Config, scene: Scene) -> int | None:
    """The seed a scene's frame is drawn with, or None for a random one.

    A redrawn frame has to come back different, and with ARK_IMAGE_SEED set
    the same prompt and seed return the same picture - so each take moves the
    seed on by one. Take 0 uses the configured seed exactly.
    """
    if cfg.ark_image_seed is None:
        return None
    return cfg.ark_image_seed + scene.take


def picture_key(cfg: Config, scene: Scene, characters: list[Character]) -> str:
    """Everything the user controls that decides what a frame shows.

    Not the subtitle: the frame is drawn from image_prompt and subject, so
    correcting a typo in a line re-reads that line and keeps its picture.
    """
    lookup = {character.id: character.desc for character in characters}
    cast = [lookup[cid] for cid in scene.cast if cid in lookup]
    return fingerprint("picture", scene.image_prompt.strip(), (scene.subject or "").strip(),
                       scene.shot_size, cast, cfg.image_style_prompt.strip(), cfg.style.medium,
                       cfg.ark_image_model, cfg.ark_image_size, cfg.ark_image_output_format,
                       image_seed(cfg, scene), scene.take)


def style_key(cfg: Config) -> str:
    """The look as the image model receives it, to tell an edited preset apart."""
    return fingerprint(cfg.image_style_prompt.strip(), cfg.style.medium)


def keyed_path(directory: Path, index: int, key: str, suffix: str) -> Path:
    return directory / f"{index:02d}_{key}{suffix}"


def write_atomically(target: Path, data: bytes) -> None:
    """Write a file whole or not at all.

    A file's name is taken as proof of what it holds, so a crash halfway
    through a write must not leave half a clip under a good name for the next
    run to adopt.
    """
    partial = target.with_name(target.name + ".part")
    partial.write_bytes(data)
    partial.replace(target)


def _usable(path: Path | None) -> bool:
    return path is not None and path.is_file() and path.stat().st_size > 0


# A pre-v9 file: the bare scene number, 07.mp3.
_LEGACY_ASSET_NAME = re.compile(r"\d{2,}")


@dataclass
class Reconciled:
    """What a run found on disk before paying for anything."""

    narration_reused: int = 0
    # Had a file, but one made for other inputs: re-read / redrawn.
    narration_stale: int = 0
    pictures_reused: int = 0
    pictures_stale: int = 0
    # Files from a pre-v9 run, renamed to the key they would have had.
    migrated: int = 0


def _reconcile_one(scene: Scene, attribute: str, expected: Path, legacy: Path | None) -> str:
    """Point one of a scene's files at `expected` if that file exists.

    Returns 'reused', 'migrated', 'stale' (the scene had a file, made from
    other inputs) or 'missing'.
    """
    recorded = getattr(scene, attribute)
    if _usable(expected):
        setattr(scene, attribute, str(expected.resolve()))
        return "reused"
    if legacy is not None:
        # Written before files carried their inputs in their names. Those
        # runs trusted their files and so does resuming one: each is renamed,
        # once, to the key it would have had, and is an ordinary file after.
        for candidate in (Path(recorded) if recorded else None, legacy):
            if _usable(candidate) and _LEGACY_ASSET_NAME.fullmatch(candidate.stem):
                candidate.replace(expected)
                setattr(scene, attribute, str(expected.resolve()))
                return "migrated"
    setattr(scene, attribute, None)
    return "stale" if recorded else "missing"


def reconcile_assets(cfg: Config, scenes: list[Scene], characters: list[Character],
                     audio_dir: Path, image_dir: Path, *,
                     legacy_audio: bool = False, legacy_images: bool = False) -> Reconciled:
    """Point every scene at the files made from its current inputs, and only those.

    `legacy_audio` / `legacy_images` trust a pre-v9 run's files - only ever
    when that run is being resumed, and for the narration only when it was
    read at this speed.
    """
    found = Reconciled()
    for index, scene in enumerate(scenes, 1):
        audio = _reconcile_one(
            scene, "audio_path",
            keyed_path(audio_dir, index, narration_key(cfg, scene.text), ".mp3"),
            audio_dir / f"{index:02d}.mp3" if legacy_audio else None)
        picture = _reconcile_one(
            scene, "image_path",
            keyed_path(image_dir, index, picture_key(cfg, scene, characters), ".png"),
            image_dir / f"{index:02d}.png" if legacy_images else None)
        found.narration_reused += audio in {"reused", "migrated"}
        found.narration_stale += audio == "stale"
        found.pictures_reused += picture in {"reused", "migrated"}
        found.pictures_stale += picture == "stale"
        found.migrated += (audio == "migrated") + (picture == "migrated")
    return found


def render_settings(cfg: Config) -> dict[str, str]:
    """The run-wide settings a manifest records, so a resume can say what changed."""
    return {"voice": cfg.ark_tts_voice_type, "style": cfg.style_preset, "style_key": style_key(cfg)}


def explain_reconciled(previous: dict[str, Any] | None, cfg: Config, found: Reconciled) -> list[str]:
    """One line for each reason something already made is being made again.

    The reason is named when the manifest can name it - a different voice, a
    different style - because "12 frames will be redrawn" with no reason
    attached reads as a bug, and it is a bill.
    """
    lines: list[str] = []
    previous = previous or {}
    was_voice, was_style = previous.get("voice"), previous.get("style")
    if found.narration_stale:
        reason = (f"the voice changed ({was_voice} -> {cfg.ark_tts_voice_type})"
                  if was_voice and was_voice != cfg.ark_tts_voice_type
                  else "their text, voice or speed changed")
        lines.append(f"{found.narration_stale} narration clip(s) will be read again: {reason}.")
    if found.pictures_stale:
        if was_style and was_style != cfg.style_preset:
            reason = f"the style changed ({was_style} -> {cfg.style_preset})"
        elif previous.get("style_key") and previous["style_key"] != style_key(cfg):
            reason = f"the {cfg.style_preset} style's prompt changed"
        else:
            reason = "their prompt, framing, cast, seed or take changed"
        lines.append(f"{found.pictures_stale} frame(s) will be drawn again: {reason}.")
    if found.migrated:
        lines.append(f"{found.migrated} file(s) from an older run were kept and renamed after their inputs.")
    return lines


# ----------------------------------------------------------------- state ----

def save_run_state(asset_root: Path, draft_name: str, title: str, copy: str, scenes: list[Scene],
                   characters: list[Character], status: str, failures: list[dict[str, Any]],
                   speed: float = BASELINE_VIDEO_SPEED,
                   moods: list[str] | None = None,
                   render: dict[str, str] | None = None) -> None:
    state = {
        "version": MANIFEST_VERSION,
        "draft_name": draft_name,
        "title": title,
        "copy": copy,
        "status": status,
        # What the narration on disk was actually read at. Without it a resume
        # at another speed would keep the old clips, cut the new timeline to
        # them, and produce a video that is neither speed while reporting a
        # clean run - which is exactly the silent kind of wrong this tool is
        # full of guards against. The file names now carry the rate as well;
        # this stays so a resume can say that the speed is why.
        "speed": speed,
        # The voice and the look, for the same reason: a resume that re-reads
        # or redraws everything should be able to say which setting moved.
        **(render or {}),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        # How the director read the copy, which is what the music is chosen
        # from. Stored because a --resume never calls the director again, and
        # without it a resumed run would fall back to reading the copy itself
        # and could quietly land on a different track than the first run did.
        "moods": list(moods or []),
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
        # Absent before manifest v9: every frame was a first take.
        values["take"] = int(values.get("take") or 0)
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
    parser.add_argument("--speed", type=float, default=None, metavar="X",
                        help=("How fast the whole video runs: narration, "
                              "picture, camera moves, pauses and subtitles "
                              "together. 1.0 is natural pace. Overrides "
                              f"VIDEO_SPEED (default {DEFAULT_VIDEO_SPEED})."))
    parser.add_argument("--verbose", action="store_true", help="Print a full traceback on failure.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    load_env()
    cfg = Config.load(speed=args.speed,
                      purpose="check" if args.check_config else "plan" if args.plan_only else "build")
    if args.check_config:
        print("Configuration OK")
        describe_configuration(cfg)
        return 0

    failures: list[dict[str, Any]] = []
    characters: list[Character] = []
    # What the narration already on disk was read at. A fresh run has none, so
    # it is whatever this run is about to use.
    previous_speed = cfg.speed
    # The manifest being resumed, if any: what a resume compares itself with
    # when it says why something already made is being made again.
    previous: dict[str, Any] | None = None
    render = render_settings(cfg)

    def save(status: str) -> None:
        save_run_state(asset_root, draft_name, title, copy, scenes, characters,
                       status, failures, cfg.speed, moods, render)

    if args.resume:
        draft_name = args.resume
        asset_root = ROOT / "output" / draft_name
        manifest_path = asset_root / "manifest.json"
        if not manifest_path.is_file():
            raise RuntimeError(f"Resume manifest does not exist: {manifest_path}")
        state = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        previous = state
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
        # Absent before manifest v7; copy_moods() reads the copy instead, so
        # an older run still gets music rather than an error.
        moods = [str(mood) for mood in state.get("moods", []) if str(mood) in BGM_MOODS]
        # Absent before manifest v6, which means the clips were made before
        # this setting existed and were read at the baseline.
        previous_speed = validate_speed(state.get("speed", BASELINE_VIDEO_SPEED))
        # A resume used to overwrite the draft unconditionally, which deletes
        # any edits already made in Jianying. It now needs --replace like any
        # other run. Printing the plan writes no draft, so it needs neither.
        if not args.plan_only:
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
        asset_root = ROOT / "output" / draft_name
        if args.plan_only:
            validate_plan_target(asset_root, draft_name, args.replace)
        else:
            validate_draft_target(cfg, draft_name, args.replace)
        asset_root.mkdir(parents=True, exist_ok=True)
        target_scenes, maximum_scenes = scene_limits(cfg.scene_length_mode, cfg.scene_characters, copy)
        print_image_cost_estimate(cfg, target_scenes, maximum_scenes)
        append_run_log(asset_root, "storyboard_started")
        scenes, characters, moods = plan_scenes(cfg, copy)
        append_run_log(asset_root, "storyboard_completed", scene_count=len(scenes),
                       character_count=len(characters))
        save("planned")

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
    stale_speed = drop_stale_narration(scenes, bool(args.resume),
                                       previous_speed, cfg.speed)
    if stale_speed:
        print(f"Speed changed since this run was made "
              f"({previous_speed:.2f}x -> {cfg.speed:.2f}x); the narration is "
              f"being re-read at the new speed. Images are kept.")
        append_run_log(asset_root, "revoice_for_speed", was=previous_speed,
                       now=cfg.speed)
    # Only a file made from a scene's current inputs is picked up - never one
    # that merely has the right number. A fresh run has no older files to
    # trust; a resumed pre-v9 run trusts its own, as it always did.
    legacy = bool(args.resume) and int((previous or {}).get("version", 0)) < MANIFEST_VERSION
    found = reconcile_assets(cfg, scenes, characters, audio_dir, image_dir,
                             legacy_audio=legacy and not stale_speed, legacy_images=legacy)
    for line in explain_reconciled(previous, cfg, found):
        print(line)
    save("reconciled")
    append_run_log(asset_root, "assets_reconciled", **asdict(found))

    def make_tts(index: int, scene: Scene) -> tuple[int, Path]:
        audio_path = keyed_path(audio_dir, index, narration_key(cfg, scene.text), ".mp3")
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
                    save("failed")
                    append_run_log(asset_root, "tts_failed", **failure)
                    raise
                scenes[index - 1].audio_path = str(audio_path.resolve())
                save("tts_in_progress")
                append_run_log(asset_root, "tts_completed", scene=index)
                completed_tts += 1
                report_progress("Voice-over", completed_tts, len(pending_tts))

    populate_audio_durations(scenes)
    save("audio_durations_ready")

    # The title gets its own voice clip. It is not one of the scenes: the copy
    # is what the scenes narrate, and when --title is given the overlay says
    # something the copy never does - so it was drawn on screen and never read.
    # Cached like the scene clips, so --resume does not pay for it twice.
    if cfg.speak_title and title_language_differs(title, scenes):
        print("Note: the title is not in the same language as the narration, "
              "so it will be read by the narration's voice. Pass a --title in "
              "the same language if that is not what you want.")

    title_audio: Path | None = None
    if cfg.speak_title and title.strip() and title_already_narrated(title, scenes):
        append_run_log(asset_root, "title_tts_skipped", reason="already said in the opening")
        print("Title voice-over skipped: the copy already opens by saying it.")
    elif cfg.speak_title and title.strip():
        # Keyed like every other clip, not a fixed name. On the title's own
        # text: a fixed name meant resuming with a different --title spoke the
        # OLD title over the new one on screen, and the run looked entirely
        # successful. On the rate: a re-run at 1.2x kept a title read at 1.5x
        # over a card sized for 1.2x. And on the voice, which the old key
        # left out, so a new voice still opened on the old one.
        candidate = audio_dir / f"title_{narration_key(cfg, title)}.mp3"
        if not candidate.is_file() or candidate.stat().st_size == 0:
            append_run_log(asset_root, "title_tts_started")
            report_progress("Title voice", 0, 1)
            try:
                synthesize_tts(cfg, title.strip(), candidate)
            except Exception as exc:
                # A silent title is a worse video, not a broken one. The rest of
                # the run is already paid for; do not throw it away over the
                # opening line.
                append_run_log(asset_root, "title_tts_failed", error=str(exc))
                print(f"Title voice-over failed, continuing without it: {exc}")
                candidate = None
            else:
                append_run_log(asset_root, "title_tts_completed")
                report_progress("Title voice", 1, 1)
        title_audio = candidate if candidate and candidate.is_file() else None

    pending_images = [(index, scene) for index, scene in enumerate(scenes, 1)
                      if not scene.image_path or not Path(scene.image_path).is_file()]
    if args.resume:
        print_image_cost_estimate(cfg, len(pending_images), len(pending_images), "Estimated remaining image cost")

    def make_image(index: int, scene: Scene, reference: str | None) -> tuple[int, Path]:
        image_path = keyed_path(image_dir, index, picture_key(cfg, scene, characters), ".png")
        generate_image(cfg, compose_image_prompt(cfg, scene, characters, referenced=reference is not None),
                       image_path, seed=image_seed(cfg, scene), reference=reference)
        return index, image_path

    image_failures: list[dict[str, Any]] = []
    drawn_images = 0

    def draw(batch: list[tuple[int, Scene]], reference: str | None) -> None:
        """Draw a batch in parallel, recording each frame as it lands."""
        nonlocal drawn_images
        if not batch:
            return
        with ThreadPoolExecutor(max_workers=min(cfg.image_concurrency, len(batch)),
                                thread_name_prefix="ark-image") as executor:
            futures = {}
            for index, scene in batch:
                append_run_log(asset_root, "image_started", scene=index)
                futures[executor.submit(make_image, index, scene, reference)] = index

            for future in as_completed(futures):
                index = futures[future]
                try:
                    _, image_path = future.result()
                except Exception as exc:
                    failure = {"stage": "image", "scene": index, "error": str(exc)}
                    failures.append(failure)
                    image_failures.append(failure)
                    save("failed")
                    append_run_log(asset_root, "image_failed", **failure)
                else:
                    scenes[index - 1].image_path = str(image_path.resolve())
                    save("image_in_progress")
                    append_run_log(asset_root, "image_completed", scene=index)
                finally:
                    drawn_images += 1
                    report_progress("Images", drawn_images, len(pending_images))

    if pending_images:
        append_run_log(asset_root, "images_started", count=len(pending_images),
                       workers=min(cfg.image_concurrency, len(pending_images)))
        report_progress("Images", 0, len(pending_images))
        if cfg.image_reference == "anchor":
            # The anchor goes first and alone: every other frame is matched to it.
            anchor = anchor_scene(scenes) + 1
            draw([(index, scene) for index, scene in pending_images if index == anchor], None)
            if image_failures:
                save("failed")
                raise RuntimeError(
                    f"The reference frame (scene {anchor}) failed, so the frames matched to it were not "
                    f"drawn; --resume draws it again. {image_failures[0]['error']}"
                )
            append_run_log(asset_root, "reference_anchor", scene=anchor)
            draw([(index, scene) for index, scene in pending_images if index != anchor],
                 reference_image(Path(scenes[anchor - 1].image_path or "")))
        else:
            draw(pending_images, None)

        if image_failures:
            save("failed")
            first_failure = image_failures[0]
            raise RuntimeError(
                f"{len(image_failures)} image(s) failed; successful images were kept for --resume. "
                f"First failure: scene {first_failure['scene']}: {first_failure['error']}"
            )

    # Chosen here, where both the storyboard and the copy are in hand, and
    # logged: the bed is the one thing in the video nobody reviews, so a run
    # that picked the wrong track should at least say which one it picked.
    bgm_path, bgm_reason = resolve_bgm(cfg, copy, moods)
    append_run_log(asset_root, "bgm_selected",
                   path=str(bgm_path) if bgm_path else None, reason=bgm_reason)
    print(f"BGM: {bgm_reason}")

    report_progress("Draft", 0, 1)
    try:
        draft_path = build_draft(cfg, scenes, draft_name, args.replace, title,
                                 title_audio, cfg.opening_sound_path, bgm_path)
    except Exception as exc:
        failure = {"stage": "draft", "error": str(exc)}
        failures.append(failure)
        save("failed")
        append_run_log(asset_root, "draft_failed", **failure)
        raise
    report_progress("Draft", 1, 1)
    failures = []
    save("completed")
    append_run_log(asset_root, "completed", draft_path=str(draft_path))
    print(f"Done: {draft_path}")
    return 0


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

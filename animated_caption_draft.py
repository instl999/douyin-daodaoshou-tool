"""Generate a Jianying draft from Chinese copy with Volcengine Ark Agent Plan."""

from __future__ import annotations

import argparse
import base64
import hashlib
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
DEFAULT_ARK_TEXT_MODEL = "deepseek-v4-flash"
DEFAULT_ARK_IMAGE_MODEL = "doubao-seedream-5.0-lite"
DEFAULT_ARK_TTS_MODEL = "seed-tts-2.0"
DEFAULT_OPENING_SOUND_PATH = "assets/opening_dong.mp3"

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
        label="深蓝白描",
        prompt=(
            "Single-colour white line illustration on a flat deep midnight-blue ground, the whole panel one "
            "continuous pen drawing: every figure, building and object rendered only as clean white contour "
            "lines of even weight, volume and shadow built from fine parallel hatching and cross-hatching like "
            "a steel engraving, no filled colour areas and no grey wash, the navy itself reading as shadow. "
            "Semi-realistic adults with confident anatomy, simple strongly readable silhouettes and clear body "
            "language, faces drawn in few lines and barely shaded. One subject, held large and central, with "
            "wide empty navy around it and only a horizon or two suggested walls for depth. Exactly one or two "
            "elements carry a flat saturated spot colour -- gold, crimson, amber or magenta -- and everything "
            "else stays white on navy; a soft glow only where there is a light source. Even fine grain across "
            "the whole panel. Editorial, symbolic, calm. "
            "Not photorealistic, no 3D render, no full-colour painting, no watercolour, no black outlines, "
            "no pale or white background, 16:9"
        ),
        medium="a white line drawing on a deep midnight-blue ground",
        avoid="photography, 3D rendering, full-colour painting, or a pale background",
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
SHOT_SIZES = {
    "wide": (
        "Wide establishing shot: the figures are small in the frame, the whole room or street is visible around "
        "them, generous headroom, the setting doing as much work as the people."
    ),
    "medium": (
        "Medium shot from roughly the waist up: one or two figures fill the middle of the frame, enough background "
        "to read the place but not more."
    ),
    "close": (
        "Close-up: the face and shoulders fill the frame, the expression is the subject, the background reduced to "
        "a few simple shapes well out of focus of attention."
    ),
}
DEFAULT_SHOT_SIZE = "medium"

MAX_COPY_CHARACTERS = 1800
DEFAULT_SCENE_CHARACTERS = 22
MIN_SCENE_CHARACTERS = 8
DEFAULT_IMAGE_CONCURRENCY = 3
MAX_IMAGE_CONCURRENCY = 8
MANIFEST_VERSION = 5

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

# Opening-title colourways. The reference title is not one colour: it runs
# warm white into crimson across the block, with a near-black stroke and a hard
# offset shadow under it. Jianying has no per-character colour, so the ramp is
# approximated the way most creators do it by hand -- the first line carries
# the primary, the rest carry the accent -- which is why every title here is
# two colours rather than one.


@dataclass(frozen=True)
class TitleColours:
    """primary = first line, accent = the lines under it, border = the stroke."""

    primary: tuple[float, float, float]
    accent: tuple[float, float, float]
    border: tuple[float, float, float]


TITLE_PRESETS: dict[str, TitleColours] = {
    # The reference: warm white over crimson on a near-black stroke.
    "crimson": TitleColours((0.98, 0.97, 0.95), (0.84, 0.13, 0.11), (0.05, 0.06, 0.09)),
    # Warm off-white over amber on deep brown; sits inside a muted illustrated
    # palette instead of fighting it the way pure red does.
    "paper": TitleColours((0.97, 0.94, 0.88), (0.93, 0.66, 0.18), (0.16, 0.12, 0.10)),
    # Ink on paper, with the vermilion of a seal for the accent.
    "ink": TitleColours((0.09, 0.09, 0.10), (0.78, 0.18, 0.12), (0.98, 0.96, 0.91)),
    # All white on a crimson stroke: the loudest option, and the safest over
    # photography, where a coloured fill has nothing stable to sit against.
    "white": TitleColours((1.0, 1.0, 1.0), (1.0, 1.0, 1.0), (0.86, 0.12, 0.12)),
    "gold": TitleColours((1.0, 0.97, 0.90), (1.0, 0.80, 0.16), (0.09, 0.09, 0.09)),
    # Risograph inks, and the paper is cream, so the type is the two inks and
    # the stroke is the paper: deep teal over fluorescent orange, knocked out
    # in cream. Cream type on cream paper would live entirely on its stroke.
    "poster": TitleColours((0.05, 0.27, 0.29), (0.97, 0.36, 0.16), (0.99, 0.96, 0.89)),
    # Neon: white over cyan on a near-black stroke that reads as the night.
    "electric": TitleColours((1.0, 1.0, 1.0), (0.24, 0.85, 0.95), (0.04, 0.03, 0.10)),
    # Marker on paper: black over red, knocked out with a white stroke.
    "marker": TitleColours((0.11, 0.11, 0.13), (0.86, 0.16, 0.14), (1.0, 1.0, 1.0)),
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
    shot_size: str = DEFAULT_SHOT_SIZE
    pause_after: bool = False
    cast: list[str] = field(default_factory=list)
    audio_path: str | None = None
    image_path: str | None = None
    duration_us: int | None = None


SCENE_FIELDS = ("text", "image_prompt", "shot_size", "pause_after", "cast",
                "audio_path", "image_path", "duration_us")


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
    opening_sound_path: Path
    opening_sound_volume: float
    opening_lead_us: int
    speak_title: bool
    title_lead_us: int
    bgm_path: Path | None
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
    def load(cls) -> Config:
        mode = env_value("SCENE_LENGTH_MODE", "density").lower()
        if mode not in {"density", "quality"}:
            raise RuntimeError("SCENE_LENGTH_MODE must be either density or quality.")

        subtitle_style = env_value("SUBTITLE_STYLE", "plate").lower()
        if subtitle_style not in {"plate", "outline", "box"}:
            raise RuntimeError("SUBTITLE_STYLE must be one of: plate, outline, box.")

        # The art style is resolved first because it supplies the defaults for
        # the colour grade and the title colourway, which .env then overrides.
        style_presets, style_default, styles_source = load_style_presets()

        style_preset = env_value("IMAGE_STYLE_PRESET", style_default).lower()
        if style_preset not in style_presets:
            options = ", ".join(sorted(style_presets))
            raise RuntimeError(
                f"IMAGE_STYLE_PRESET must be one of: {options}. "
                f"(Presets come from {styles_file_path()}; edit it or point "
                f"{STYLES_FILE_SETTING} at another file to add your own.)"
            )
        style = style_presets[style_preset]

        title_style = env_value("TITLE_STYLE", style.title).lower()
        if title_style not in TITLE_PRESETS:
            options = ", ".join(sorted(TITLE_PRESETS))
            raise RuntimeError(f"TITLE_STYLE must be one of: {options}.")

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
            image_style_prompt=env_value("IMAGE_STYLE_PROMPT", style.prompt),
            style_preset=style_preset,
            style=style,
            styles_source=styles_source,

            ark_tts_url=env_value("ARK_TTS_URL", DEFAULT_ARK_TTS_URL),
            ark_tts_model=env_value("ARK_TTS_MODEL", DEFAULT_ARK_TTS_MODEL),
            ark_tts_voice_type=required("ARK_TTS_VOICE_TYPE"),
            ark_tts_speech_rate=bounded_env_int("ARK_TTS_SPEECH_RATE", 0, -50, 100),
            ark_tts_loudness_rate=bounded_env_int("ARK_TTS_LOUDNESS_RATE", 0, -50, 100),
            tts_concurrency=bounded_env_int("TTS_CONCURRENCY", 3, 1, MAX_IMAGE_CONCURRENCY),

            draft_dir=draft_dir,
            # Required. The cue is a specific sound these videos are known
            # by; it ships with the repo, and nothing synthesises a stand-in
            # for it, so a missing one is a broken install rather than a
            # missing option.
            opening_sound_path=_require_asset(
                "OPENING_SOUND_PATH", DEFAULT_OPENING_SOUND_PATH, {".mp3", ".wav"}
            ),
            # Unity by default: the opening cue plays exactly as supplied. It
            # is the user's own file and is meant to sound the way it sounds.
            opening_sound_volume=bounded_env_float("OPENING_SOUND_VOLUME", 1.0, 0.0, 2.0),
            opening_lead_us=round(bounded_env_float("OPENING_LEAD_SECONDS", 0.8, 0.0, 5.0) * 1_000_000),
            speak_title=env_flag("SPEAK_TITLE", True),
            title_lead_us=round(
                bounded_env_float("TITLE_LEAD_SECONDS", DEFAULT_TITLE_LEAD_SECONDS, 0.0, 3.0)
                * 1_000_000
            ),
            bgm_path=_optional_asset("BGM_PATH", {".mp3", ".wav"}),
            bgm_volume=bounded_env_float("BGM_VOLUME", 0.10, 0.0, 1.0),
            bgm_lift_volume=bounded_env_float("BGM_LIFT_VOLUME", 0.20, 0.0, 1.0),
            bgm_ramp_us=round(bounded_env_float("BGM_RAMP_SECONDS", 0.25, 0.05, 2.0) * 1_000_000),
            paragraph_pause_us=round(
                bounded_env_float("PARAGRAPH_PAUSE_SECONDS", 0.5, 0.0, 3.0) * 1_000_000
            ),
            ending_hold_us=round(bounded_env_float("ENDING_HOLD_SECONDS", 1.8, 0.0, 10.0) * 1_000_000),
            watermark_path=_optional_asset("WATERMARK_PATH", {".png", ".jpg", ".jpeg"}),
            color_grade=env_value("COLOR_GRADE", style.grade),
            color_grade_intensity=bounded_env_float("COLOR_GRADE_INTENSITY", 12.0, 0.0, 100.0),

            subtitle_y=layout_y(bounded_env_int(
                "NARRATION_SUBTITLE_Y", DEFAULT_NARRATION_SUBTITLE_Y,
                -LAYOUT_REFERENCE_HALF_HEIGHT, LAYOUT_REFERENCE_HALF_HEIGHT,
            )),
            subtitle_size=bounded_env_float("NARRATION_SUBTITLE_SIZE", DEFAULT_SUBTITLE_SIZE, 1.0, 30.0),
            subtitle_font=env_value("SUBTITLE_FONT", DEFAULT_SUBTITLE_FONT),
            subtitle_style=subtitle_style,
            subtitle_border_width=bounded_env_float(
                "SUBTITLE_BORDER_WIDTH", DEFAULT_SUBTITLE_BORDER_WIDTH, 0.0, 40.0
            ),
            subtitle_letter_spacing=bounded_env_int("SUBTITLE_LETTER_SPACING", 2, 0, 20),
            subtitle_max_line_width=bounded_env_float("SUBTITLE_MAX_LINE_WIDTH", 0.88, 0.4, 1.0),
            subtitle_em_px=bounded_env_float("SUBTITLE_EM_PX", DEFAULT_SUBTITLE_EM_PX, 1.0, 40.0),
            subtitle_animation=env_value("SUBTITLE_ANIMATION", "向上擦除"),
            subtitle_animation_us=round(
                bounded_env_float("SUBTITLE_ANIMATION_SECONDS", 0.3, 0.0, 3.0) * 1_000_000
            ),
            title_style=title_style,
            title_font=env_value("TITLE_FONT", DEFAULT_TITLE_FONT),
            title_y=layout_y(bounded_env_int(
                "TITLE_Y", DEFAULT_TITLE_Y,
                -LAYOUT_REFERENCE_HALF_HEIGHT, LAYOUT_REFERENCE_HALF_HEIGHT,
            )),
            title_size=bounded_env_float("TITLE_SIZE", DEFAULT_TITLE_SIZE, 1.0, 30.0),
            title_border_width=bounded_env_float(
                "TITLE_BORDER_WIDTH", DEFAULT_TITLE_BORDER_WIDTH, 0.0, 40.0
            ),
            title_max_line_width=bounded_env_float(
                "TITLE_MAX_LINE_WIDTH", DEFAULT_TITLE_MAX_LINE_WIDTH, 0.4, 1.0
            ),
            title_us=round(
                bounded_env_float("TITLE_SECONDS", DEFAULT_TITLE_SECONDS, 0.5, 15.0) * 1_000_000
            ),
            title_animation=env_value("TITLE_ANIMATION", "缩小"),
            title_outro=env_value("TITLE_OUTRO", "放大"),
            # 0 disables the camera move entirely; KEN_BURNS=0 still works.
            ken_burns_rate=(
                bounded_env_float("KEN_BURNS_RATE", DEFAULT_KEN_BURNS_RATE, 0.0, 0.2)
                if env_flag("KEN_BURNS", True) else 0.0
            ),
        )


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


def title_already_narrated(title: str, scenes: list[Scene]) -> bool:
    """True when the copy's own narration already opens with the title.

    --title defaults to the first line of the copy, and that line is part of
    the copy the scenes narrate. Speaking the title as well then says the same
    sentence twice in a row - title voice, half a second, scene one saying it
    again. That is the common case, not the corner case: it happens on every
    run that does not pass --title.

    Compared on characters alone. The splitter is free to reflow punctuation
    and whitespace between the copy and a scene, and a stutter is a stutter
    whether or not a comma survived.
    """
    if not scenes:
        return False
    strip = re.compile(r"[\s　]+")
    wanted = strip.sub("", title)
    if not wanted:
        return False
    opening = strip.sub("", scenes[0].text or "")
    return opening.startswith(wanted) or wanted.startswith(opening)


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


def describe_configuration(cfg: Config) -> None:
    """Print the settings whose resolved value is easy to get wrong."""
    subtitle_px = round(cfg.subtitle_y * CANVAS_HALF_HEIGHT)
    title_px = round(cfg.title_y * CANVAS_HALF_HEIGHT)
    print(f"Canvas: {CANVAS_WIDTH}x{CANVAS_HEIGHT} @30fps (landscape)")
    label = f" {cfg.style.label}" if cfg.style.label else ""
    print(f"Art style: {cfg.style_preset}{label}"
          f"{' (overridden by IMAGE_STYLE_PROMPT)' if os.getenv('IMAGE_STYLE_PROMPT', '').strip() else ''}"
          f"  [{cfg.styles_source}]")
    print(f"  rendered as: {cfg.style.medium}")
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
          f"colourway {cfg.title_style}, {cfg.title_us / 1e6:.1f}s")
    print(f"Opening:    {cfg.opening_sound_path} at {cfg.opening_sound_volume:.2f}, "
          f"copy starts at {cfg.opening_lead_us / 1e6:.2f}s or later")
    print(f"Title voice: {'on' if cfg.speak_title else 'off'}"
          + (f", {cfg.title_lead_us / 1e6:.2f}s after the cue" if cfg.speak_title else ""))
    if cfg.bgm_volume > 0:
        lift = max(cfg.bgm_volume, cfg.bgm_lift_volume)
        print(f"BGM volume: {cfg.bgm_volume:.2f} under speech ({20 * math.log10(cfg.bgm_volume):.1f} dB), "
              f"{lift:.2f} in the gaps")
    else:
        print("BGM volume: muted")
    print(f"Pauses:     {cfg.paragraph_pause_us / 1e6:.2f}s after a paragraph, "
          f"{cfg.ending_hold_us / 1e6:.2f}s held at the end")
    print(f"Colour grade: {cfg.color_grade or 'none'} at {cfg.color_grade_intensity:.0f}%")
    for name in ("ARK_API_KEY", "ARK_TTS_VOICE_TYPE", "JIAN_YING_DRAFT_DIR",
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
    framing = SHOT_SIZES.get(scene.shot_size, SHOT_SIZES[DEFAULT_SHOT_SIZE])
    medium = getattr(cfg, "style", None)
    medium = medium.medium if medium else DEFAULT_STYLE_MEDIUM
    return (
        f"{scene.image_prompt.strip().rstrip('.')}. Usage: one 16:9 frame rendered as {medium}, matched directly "
        "to this exact subtitle. "
        f"{framing} "
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
        "You are the storyboard director for a Chinese narration video rendered entirely as "
        f"{cfg.style.medium}. "
        f"Split this part ({batch_number}/{batch_count}) of the copy into about {batch_target} independent subtitle scenes, "
        f"never more than {batch_maximum}. {length_instruction}Preserve the complete meaning and original order. "
        "One subtitle scene must map to exactly one image. For every scene, write image_prompt as one concise, coherent English "
        "natural-language description of the image that best matches only that scene's Chinese subtitle. First follow the people, "
        "objects, action, location, time, mood, and relationship explicitly present in the subtitle. If the subtitle describes a "
        "concrete event, depict that event literally in a believable everyday setting. If it is abstract, use the simplest human "
        "situation that communicates the whole sentence without changing its meaning. The scene content should feel true to life, "
        f"but every frame must remain {cfg.style.medium}, never {cfg.style.avoid}. Do not force a finance theme. "
        "Never add charts, tables, dashboards, graphs, market arrows, coins, banks, office imagery, or decorative business symbols "
        "unless that exact subtitle genuinely calls for them. Do not visually magnify an incidental word at the expense of the full "
        "sentence. Favor a natural human moment and a clear action over abstract icons or infographic composition. Keep screens, "
        "signs, documents, packaging, and interfaces blank; do not request visible text, letters, digits, punctuation, logos, "
        "watermarks, subtitles, speech bubbles, or fake interface copy. "
        f"{cast_instruction}"
        "Set \"cast\" on each scene to the ids of the characters visible in that panel, or [] if nobody recurring appears. "
        "Set \"shot_size\" to vary the framing the way an editor would, never leaving it on one value for long: "
        "\"wide\" to open a section, establish a place, or carry a sentence about society or the world at large; "
        "\"medium\" as the default for describing an event; \"close\" for a feeling, a decision, a turn, or a "
        "conclusion, where the face is the point. Aim for roughly one wide and one close in every four scenes. "
        "Set \"pause_after\" to true on the scene that ends a paragraph or a complete thought, so the video can "
        "take a breath there; leave it false inside a paragraph. "
        'Return JSON only: {"characters":[{"id":"A","desc":"English description"}],'
        '"scenes":[{"text":"Chinese scene copy","image_prompt":"English image prompt",'
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
        scenes.append(Scene(text=text.strip(), image_prompt=image_prompt.strip(),
                            shot_size=shot_size, pause_after=bool(item.get("pause_after")),
                            cast=cast))
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


def build_draft(cfg: Config, scenes: list[Scene], draft_name: str, replace: bool,
                title: str, title_audio: Path | None = None,
                opening_sound: Path | None = None) -> Path:
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
    bgm_material = AudioMaterial(str(cfg.bgm_path)) if cfg.bgm_path else None
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
    if title_material is not None and not title_voice_fits(
            title_material.duration, cfg.title_lead_us):
        # Too long to read before the copy has to start. The type stays; only
        # the voice-over goes. Silently clamping instead would talk the copy
        # over the tail of its own title.
        print(f"Title voice-over skipped: reading it takes "
              f"{title_material.duration / 1e6:.1f}s and the opening may not "
              f"run past {MAX_OPENING_LEAD_US / 1e6:.1f}s. Use a shorter --title.")
        title_material = None
    lead_us = opening_lead(cfg.opening_lead_us,
                           title_material.duration if title_material else 0,
                           cfg.title_lead_us)

    timings, total_duration = plan_timeline(
        [material.duration for material in materials],
        [scene.pause_after for scene in scenes],
        lead_us, cfg.paragraph_pause_us, cfg.ending_hold_us,
    )

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
            move = KEN_BURNS_MOVES[(index - 1) % len(KEN_BURNS_MOVES)]
            apply_ken_burns(video, KeyframeProperty, timing.visual_duration, move, cfg.ken_burns_rate)
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
    """Stack the title lines, first line in the primary colour, rest in accent.

    The reference title runs warm white into crimson across a two-line block.
    Jianying colours a text segment as a whole, so the ramp becomes one segment
    per line -- which also puts the line pitch under our control, and the
    reference sets it tighter than any text default would.
    """
    from pyJianYingDraft import ClipSettings, TextBorder, TextSegment, TextShadow, TextStyle, Timerange

    duration = min(total_duration, cfg.title_us if hold_us is None else hold_us)
    if duration <= 0 or not lines:
        return
    colours = TITLE_PRESETS[cfg.title_style]
    size = fit_title_size(lines, cfg.title_size, cfg.title_max_line_width, cfg.subtitle_em_px)
    offsets = title_line_offsets(len(lines), cfg.title_y, size, cfg.subtitle_em_px)
    for index, (line, track, offset) in enumerate(zip(lines, tracks, offsets, strict=True)):
        segment = TextSegment(
            line, Timerange(0, duration),
            font=font,
            style=TextStyle(size=size, bold=True, align=1,
                            color=colours.primary if index == 0 else colours.accent,
                            letter_spacing=cfg.subtitle_letter_spacing, auto_wrapping=False),
            border=TextBorder(color=colours.border, width=cfg.title_border_width),
            # Hard and offset down-right rather than soft and centred: the
            # reference shadow reads as a second layer of type behind the first.
            shadow=TextShadow(alpha=0.85, diffuse=8.0, distance=14.0, angle=-55.0),
            clip_settings=ClipSettings(transform_x=0.0, transform_y=offset),
        )
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
        append_run_log(asset_root, "title_tts_skipped", reason="already in the copy")
        print("Title voice-over skipped: the copy's first line already says it.")
    elif cfg.speak_title and title.strip():
        # Keyed on the title's own text, not a fixed name. --resume keeps
        # whatever audio is already on disk, so a fixed name meant resuming
        # with a different --title spoke the OLD title over the new one on
        # screen - and the run would look entirely successful.
        digest = hashlib.sha1(title.strip().encode("utf-8")).hexdigest()[:12]
        candidate = audio_dir / f"title_{digest}.mp3"
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
        draft_path = build_draft(cfg, scenes, draft_name, args.replace, title,
                                 title_audio, cfg.opening_sound_path)
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

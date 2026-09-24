"""The art direction: the looks, the framing table, and what every frame must do."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import env
from .env import resolve_asset_path

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

def styles_file_path() -> Path:
    """Where the style presets live. Relative paths resolve against ROOT."""
    raw = os.getenv(STYLES_FILE_SETTING, "").strip()
    return resolve_asset_path(raw) if raw else env.ROOT / DEFAULT_STYLES_FILENAME


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



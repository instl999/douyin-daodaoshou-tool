"""Where the type goes and what colour it is: the canvas, the captions, the title."""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

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

# Opening-title colourways. The reference title is not one colour: it runs
# warm white into crimson across the block, with a near-black stroke and a hard
# offset shadow under it. This code used to say Jianying has no per-character
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



"""The opening title: whether it is read aloud, and how long the head holds for it."""

from __future__ import annotations

from .layout import TITLE_TRAILING_PUNCTUATION
from .models import Scene

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



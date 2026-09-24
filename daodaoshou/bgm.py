"""The music: choosing a bed from the library, and ducking it under the voice."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config



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



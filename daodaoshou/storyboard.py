"""The storyboard director: turning the copy into scenes that can be drawn."""

from __future__ import annotations

import json
import re
import time
from typing import TYPE_CHECKING, Any

import requests

from . import net
from .bgm import BGM_MOOD_LIMIT, BGM_MOODS
from .models import Character, Scene
from .net import ensure_ok
from .report import report_progress
from .styles import DEFAULT_SHOT_SIZE, SHOT_SIZES

if TYPE_CHECKING:
    from .config import Config


MAX_COPY_CHARACTERS = 1800
DEFAULT_SCENE_CHARACTERS = 22
MIN_SCENE_CHARACTERS = 8
# ------------------------------------------------------------ storyboard ----

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
        response = net.post(
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



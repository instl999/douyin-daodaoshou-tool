"""Generate a Jianying draft from Chinese copy with Volcengine Ark Agent Plan."""

from __future__ import annotations

import argparse
import base64
import math
import mimetypes
import json
import os
import re
import random
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parent
DEFAULT_ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/plan/v3"
DEFAULT_ARK_IMAGE_URL = f"{DEFAULT_ARK_BASE_URL}/images/generations"
DEFAULT_ARK_TTS_URL = "https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional"
DEFAULT_ARK_TEXT_MODEL = "deepseek-v4-flash"
DEFAULT_ARK_IMAGE_MODEL = "doubao-seedream-5.0-lite"
DEFAULT_ARK_TTS_MODEL = "seed-tts-2.0"
DEFAULT_OPENING_SOUND_PATH = "assets/opening_dong.mp3"
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
DASHSCOPE_VIDEO_URL = f"{DASHSCOPE_BASE_URL}/services/aigc/video-generation/video-synthesis"
STYLE = (
    "Chinese social-realism manhua panel, mature narrative webcomic aesthetic, contemporary Chinese everyday setting, "
    "semi-realistic adult faces and believable human anatomy, strongly expressive facial emotions and dramatic but natural body "
    "language, bold crisp black ink outlines with varied line weight, flat color fills with hard-edged cel shading, subtle printed-comic "
    "texture on skin and clothing, saturated deep navy blue contrasted with muted warm earth tones, high-contrast cinematic lighting, "
    "simple clean interior or urban background with clear spatial depth, polished 2D illustration, not photorealistic, no 3D render, "
    "no watercolor, no soft pastel anime, no chibi, 16:9"
)
BGM_VOLUME_RATIO = 10 ** (-4.4 / 20)  # fixed -4.4 dB
MAX_COPY_CHARACTERS = 1800
DEFAULT_SCENE_CHARACTERS = 22
DEFAULT_IMAGE_CONCURRENCY = 3
MAX_IMAGE_CONCURRENCY = 8
I2V_SCENE_INDICES = (2, 3, 4, 5, 6)
DEFAULT_I2V_CNY_PER_SECOND = 0.155
NARRATION_SUBTITLE_TRACK = "narration_subtitles"
TITLE_OVERLAY_TRACK = "title_overlay"
NARRATION_SUBTITLE_SIZE = 7
DEFAULT_NARRATION_SUBTITLE_Y = -700
TITLE_SCALE = 1.17
IMAGE_COST_CNY = {
    "Tongyi-MAI/Z-Image-Turbo": 0.10,
    "baidu/ERNIE-Image-Turbo": 0.11,
    "Tongyi-MAI/Z-Image": 0.30,
    "Qwen/Qwen-Image": 0.30,
    "Kwai-Kolors/Kolors": 0.00,
}


@dataclass
class Scene:
    text: str
    image_prompt: str
    audio_path: str | None = None
    image_path: str | None = None
    duration_us: int | None = None
    video_task_id: str | None = None
    video_path: str | None = None


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if raw and not raw.startswith("#") and "=" in raw:
            key, value = raw.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def resolve_asset_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path

def validate_local_configuration(skip_i2v: bool = False) -> None:
    required_names = ["ARK_API_KEY", "ARK_TTS_VOICE_TYPE", "JIAN_YING_DRAFT_DIR"]
    if not skip_i2v:
        required_names.append("DASHSCOPE_API_KEY")
    for name in required_names:
        required(name)
    scene_length_mode()
    positive_env_int("SCENE_CHARACTERS_PER_IMAGE", DEFAULT_SCENE_CHARACTERS, minimum=20)
    positive_env_int("TTS_CONCURRENCY", 3)
    bounded_env_int("IMAGE_CONCURRENCY", DEFAULT_IMAGE_CONCURRENCY, 1, MAX_IMAGE_CONCURRENCY)
    bounded_env_int("ARK_TTS_SPEECH_RATE", 0, -50, 100)
    bounded_env_int("ARK_TTS_LOUDNESS_RATE", 0, -50, 100)
    bounded_env_int("NARRATION_SUBTITLE_Y", DEFAULT_NARRATION_SUBTITLE_Y, -1080, 1080)
    if not Path(required("JIAN_YING_DRAFT_DIR")).expanduser().is_dir():
        raise RuntimeError("JIAN_YING_DRAFT_DIR does not exist.")
    optional_assets = {
        "OPENING_SOUND_PATH": {".mp3", ".wav"},
        "BGM_PATH": {".mp3", ".wav"},
        "WATERMARK_PATH": {".png", ".jpg", ".jpeg"},
    }
    for setting, supported_suffixes in optional_assets.items():
        value = os.getenv(setting, "").strip()
        if setting == "OPENING_SOUND_PATH" and not value:
            value = DEFAULT_OPENING_SOUND_PATH
        if not value:
            continue
        path = resolve_asset_path(value)
        if not path.is_file():
            raise RuntimeError(f"{setting} does not exist: {path}")
        if path.suffix.lower() not in supported_suffixes:
            extensions = ", ".join(sorted(supported_suffixes))
            raise RuntimeError(f"{setting} must use one of: {extensions}.")

def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing {name}; set it in .env.")
    return value


def env_value(name: str, default: str) -> str:
    return os.getenv(name, "").strip() or default


def positive_env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, not {raw!r}.") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be at least {minimum}.")
    return value


def bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, not {raw!r}.") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}.")
    return value


def request_with_retry(method: str, url: str, **kwargs: Any) -> requests.Response:
    kwargs["timeout"] = max(int(kwargs.get("timeout", 0) or 0), 300)
    for attempt in range(3):
        try:
            response = requests.request(method, url, **kwargs)
            if response.status_code != 429 and response.status_code < 500:
                return response
            if attempt == 2:
                return response
            print(f"HTTP {response.status_code}; retrying ({attempt + 1}/3)...", flush=True)
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt == 2:
                raise RuntimeError(f"Network request failed after 3 attempts: {exc}") from exc
            print(f"Network timeout; retrying ({attempt + 1}/3)...", flush=True)
        time.sleep((attempt + 1) * 3)
    raise RuntimeError("Request failed")


def post(url: str, **kwargs: Any) -> requests.Response:
    return request_with_retry("POST", url, **kwargs)


def get(url: str, **kwargs: Any) -> requests.Response:
    return request_with_retry("GET", url, **kwargs)


def compose_image_prompt(scene_prompt: str) -> str:
    style = os.getenv("IMAGE_STYLE_PROMPT", STYLE).strip() or STYLE
    return (
        f"{scene_prompt.strip().rstrip('.')}. Usage: one 16:9 Chinese narrative manhua panel matched directly to this exact subtitle. "
        f"{style.strip().rstrip('.')}. Depict the concrete moment, people, action, setting, and emotion described by this subtitle. "
        "Include only the people, objects, and surroundings needed to communicate the complete subtitle; keep the composition natural "
        "and narrative, and do not visually overemphasize one incidental detail. Keep all screens, signs, documents, packaging, and "
        "interfaces blank. No visible text, letters, digits, punctuation, "
        "logos, watermarks, subtitles, or fake interface copy."
    )


def scene_length_mode() -> str:
    mode = os.getenv("SCENE_LENGTH_MODE", "density").strip().lower()
    if mode not in {"density", "quality"}:
        raise RuntimeError("SCENE_LENGTH_MODE must be either density or quality.")
    return mode


def scene_text_length_bounds(characters_per_scene: int) -> tuple[int, int]:
    """Return configured-density bounds or the fixed quality-first bounds."""
    if scene_length_mode() == "quality":
        return 10, 28
    minimum = max(10, round(characters_per_scene * 0.5))
    maximum = max(minimum + 8, round(characters_per_scene * 1.2))
    return minimum, maximum


def scene_limits(copy: str) -> tuple[int, int]:
    character_count = len(re.sub(r"\s+", "", copy))
    characters_per_scene = positive_env_int("SCENE_CHARACTERS_PER_IMAGE", DEFAULT_SCENE_CHARACTERS, minimum=20)
    minimum_scene_characters, maximum_scene_characters = scene_text_length_bounds(characters_per_scene)
    target_characters = maximum_scene_characters if scene_length_mode() == "quality" else characters_per_scene
    target = max(6, (character_count + target_characters - 1) // target_characters)
    maximum = min(96, max(24, (character_count + minimum_scene_characters - 1) // minimum_scene_characters))
    return target, maximum

def image_model_cost_cny() -> float | None:
    model = env_value("ARK_IMAGE_MODEL", DEFAULT_ARK_IMAGE_MODEL)
    return IMAGE_COST_CNY.get(model)


def print_image_cost_estimate(minimum_images: int, maximum_images: int, label: str = "Estimated image cost") -> None:
    model = env_value("ARK_IMAGE_MODEL", DEFAULT_ARK_IMAGE_MODEL)
    unit_cost = image_model_cost_cny()
    if unit_cost is None:
        print(f"{label}: unknown for model {model}; image count {minimum_images}-{maximum_images}.", flush=True)
        return
    print(
        f"{label}: CNY {minimum_images * unit_cost:.2f}-{maximum_images * unit_cost:.2f} "
        f"({minimum_images}-{maximum_images} images x CNY {unit_cost:.2f}/image; model={model}; image API only, TTS and storyboard-text-model costs excluded)",
        flush=True,
    )


def report_progress(stage: str, completed: int, total: int) -> None:
    total = max(1, total)
    completed = min(max(0, completed), total)
    width = 28
    filled = round(width * completed / total)
    bar = "#" * filled + "-" * (width - filled)
    ending = "\n" if completed == total else "\r"
    print(f"{stage}: [{bar}] {completed}/{total} ({completed / total:.0%})", end=ending, flush=True)


def storyboard_batches(copy: str, batch_size: int = 360) -> list[str]:
    units = [unit.strip() for unit in re.split(r"(?<=[\u3002\uff01\uff1f\uff1b!?])|\n+", copy) if unit.strip()]
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


def plan_scenes(copy: str) -> list[Scene]:
    target, maximum = scene_limits(copy)
    characters_per_scene = positive_env_int("SCENE_CHARACTERS_PER_IMAGE", DEFAULT_SCENE_CHARACTERS, minimum=20)
    minimum_scene_characters, maximum_scene_characters = scene_text_length_bounds(characters_per_scene)
    length_mode = scene_length_mode()
    length_instruction = (
        "Keep each scene 10 to 28 Chinese characters and prioritize clear, focused visuals. "
        if length_mode == "quality"
        else (
            f"Aim for about {characters_per_scene} Chinese characters per scene; keep each scene between "
            f"{minimum_scene_characters} and {maximum_scene_characters} Chinese characters. "
        )
    )
    batches = storyboard_batches(copy)
    total_characters = max(1, len(re.sub(r"\s+", "", copy)))
    scenes: list[Scene] = []
    report_progress("Storyboard", 0, len(batches))

    for batch_number, batch in enumerate(batches, 1):
        batch_characters = len(re.sub(r"\s+", "", batch))
        batch_target = max(1, round(target * batch_characters / total_characters))
        batch_maximum = min(24, max(6, batch_target + 4))
        prompt = (
            "You are the storyboard director for a Chinese narration video rendered entirely as a mature social-realism manhua. "
            f"Split this part ({batch_number}/{len(batches)}) of the copy into about {batch_target} independent subtitle scenes, "
            f"never more than {batch_maximum}. {length_instruction}Preserve the complete meaning and original order. "
            "One subtitle scene must map to exactly one image. For every scene, write image_prompt as one concise, coherent English "
            "natural-language description of the image that best matches only that scene's Chinese subtitle. First follow the people, "
            "objects, action, location, time, mood, and relationship explicitly present in the subtitle. If the subtitle describes a "
            "concrete event, depict that event literally in a believable everyday setting. If it is abstract, use the simplest human "
            "situation that communicates the whole sentence without changing its meaning. The scene content should feel true to life, "
            "but the rendering must remain a polished Chinese manhua panel with bold black outlines and hard-edged cel shading, never photography or 3D. Do not force a finance theme. "
            "Never add charts, tables, dashboards, graphs, market arrows, coins, banks, office imagery, or decorative business symbols "
            "unless that exact subtitle genuinely calls for them. Do not visually magnify an incidental word at the expense of the full "
            "sentence. Favor a natural human moment and a clear action over abstract icons or infographic composition. Keep screens, "
            "signs, documents, packaging, and interfaces blank; do not request visible text, letters, digits, punctuation, logos, "
            "watermarks, subtitles, speech bubbles, or fake interface copy. "
            'Return JSON only: {"scenes":[{"text":"Chinese scene copy","image_prompt":"English image prompt"}]}'
        )
        response = post(
            f"{env_value('ARK_BASE_URL', DEFAULT_ARK_BASE_URL).rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {required('ARK_API_KEY')}", "Content-Type": "application/json"},
            json={
                "model": env_value("ARK_TEXT_MODEL", DEFAULT_ARK_TEXT_MODEL),
                "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": batch}],
                "temperature": 0.55,
                "max_tokens": min(4096, max(1024, batch_target * 120)),
                "response_format": {"type": "json_object"},
            },
            timeout=300,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
        data = json.loads(content)
        batch_scenes = [
            Scene(item["text"].strip(), compose_image_prompt(item["image_prompt"]))
            for item in data.get("scenes", [])
            if item.get("text") and item.get("image_prompt")
        ]
        if not batch_scenes:
            raise RuntimeError(f"Storyboard model returned no scenes for batch {batch_number}.")
        if len(batch_scenes) > batch_maximum:
            raise RuntimeError(f"Storyboard batch {batch_number} returned {len(batch_scenes)} scenes; the safety limit is {batch_maximum}.")
        scenes.extend(batch_scenes)
        report_progress("Storyboard", batch_number, len(batches))

    if not scenes:
        raise RuntimeError("Storyboard model returned no scenes.")
    if len(scenes) > maximum:
        raise RuntimeError(f"Storyboard returned {len(scenes)} scenes; the safety limit for this copy is {maximum}.")
    return scenes

def synthesize_tts(text: str, target: Path) -> None:
    request_id = str(uuid.uuid4())
    payload = {
        "user": {"uid": "copy_to_video_draft_generator"},
        "req_params": {
            "text": text,
            "speaker": required("ARK_TTS_VOICE_TYPE"),
            "audio_params": {
                "format": "mp3",
                "sample_rate": 24000,
                "speech_rate": bounded_env_int("ARK_TTS_SPEECH_RATE", 0, -50, 100),
                "loudness_rate": bounded_env_int("ARK_TTS_LOUDNESS_RATE", 0, -50, 100),
            },
        },
    }
    response = post(
        env_value("ARK_TTS_URL", DEFAULT_ARK_TTS_URL),
        headers={
            "X-Api-Key": required("ARK_API_KEY"),
            "X-Api-Resource-Id": env_value("ARK_TTS_MODEL", DEFAULT_ARK_TTS_MODEL),
            "X-Api-Request-Id": request_id,
            "Content-Type": "application/json",
        },
        json=payload,
        stream=True,
        timeout=120,
    )
    response.raise_for_status()
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


def generate_image(prompt: str, target: Path) -> None:
    response = post(
        env_value("ARK_IMAGE_URL", DEFAULT_ARK_IMAGE_URL),
        headers={"Authorization": f"Bearer {required('ARK_API_KEY')}", "Content-Type": "application/json"},
        json={
            "model": env_value("ARK_IMAGE_MODEL", DEFAULT_ARK_IMAGE_MODEL),
            "prompt": prompt,
            "size": env_value("ARK_IMAGE_SIZE", "2560x1440"),
            "sequential_image_generation": "disabled",
            "response_format": env_value("ARK_IMAGE_RESPONSE_FORMAT", "url"),
            "output_format": env_value("ARK_IMAGE_OUTPUT_FORMAT", "png"),
            "watermark": False,
        },
        timeout=300,
    )
    response.raise_for_status()
    payload = response.json()
    images = payload.get("data") or payload.get("images") or []
    if not images:
        raise RuntimeError(f"Image API returned no image data: {payload}")
    image = images[0]
    if image.get("b64_json"):
        target.write_bytes(base64.b64decode(image["b64_json"]))
        return
    image_url = image.get("url")
    if not image_url:
        raise RuntimeError(f"Image API returned an image without data or URL: {image}")
    download = get(image_url, timeout=300)
    download.raise_for_status()
    target.write_bytes(download.content)

def data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def scene_video_prompt(scene: Scene) -> str:
    return (
        f"{scene.image_prompt}, subtle natural motion, gentle character movement, slow cinematic camera movement, "
        "preserve the first-frame composition, no cuts, no text, no subtitles, no watermark, silent video"
    )


def i2v_duration_seconds(audio_duration_us: int) -> int:
    return min(15, max(2, math.ceil(audio_duration_us / 1_000_000)))


def i2v_headers(async_task: bool = False) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {required('DASHSCOPE_API_KEY')}", "Content-Type": "application/json"}
    if async_task:
        headers["X-DashScope-Async"] = "enable"
    return headers


def submit_scene_video(scene: Scene, image_path: Path, audio_duration_us: int) -> str:
    payload = {
        "model": os.getenv("DASHSCOPE_I2V_MODEL", "wan2.6-i2v-flash"),
        "input": {"prompt": scene_video_prompt(scene), "img_url": data_uri(image_path)},
        "parameters": {
            "resolution": os.getenv("DASHSCOPE_I2V_RESOLUTION", "720P"),
            "duration": i2v_duration_seconds(audio_duration_us),
            "prompt_extend": True,
            "watermark": False,
            "audio": False,
        },
    }
    response = post(DASHSCOPE_VIDEO_URL, headers=i2v_headers(True), json=payload, timeout=300)
    response.raise_for_status()
    output = response.json().get("output", {})
    task_id = output.get("task_id")
    if not task_id:
        raise RuntimeError(f"Image-to-video task was not created: {response.json()}")
    return task_id


def wait_for_scene_video(task_id: str, target: Path) -> None:
    poll_seconds = positive_env_int("DASHSCOPE_I2V_POLL_SECONDS", 15)
    deadline = time.monotonic() + positive_env_int("DASHSCOPE_I2V_TIMEOUT_SECONDS", 900)
    while time.monotonic() < deadline:
        response = get(f"{DASHSCOPE_BASE_URL}/tasks/{task_id}", headers=i2v_headers(), timeout=300)
        response.raise_for_status()
        output = response.json().get("output", {})
        status = output.get("task_status")
        if status == "SUCCEEDED":
            video_url = output.get("video_url")
            if not video_url:
                raise RuntimeError(f"Image-to-video task succeeded without a video URL: {task_id}")
            download = get(video_url, timeout=300)
            download.raise_for_status()
            target.write_bytes(download.content)
            return
        if status in {"FAILED", "CANCELED", "UNKNOWN"}:
            raise RuntimeError(f"Image-to-video task {task_id} ended with {status}: {output.get('message', output)}")
        time.sleep(poll_seconds)
    raise RuntimeError(f"Image-to-video task timed out after polling: {task_id}")


def print_i2v_cost_estimate(scenes: list[Scene]) -> None:
    selected = [scene for index, scene in enumerate(scenes, 1) if index in I2V_SCENE_INDICES]
    estimated_seconds = sum(i2v_duration_seconds(scene.duration_us or 4_000_000) for scene in selected)
    unit_cost = float(os.getenv("DASHSCOPE_I2V_CNY_PER_SECOND", str(DEFAULT_I2V_CNY_PER_SECOND)))
    print(f"Estimated image-to-video cost: CNY {estimated_seconds * unit_cost:.2f} ({len(selected)} scenes, about {estimated_seconds}s x CNY {unit_cost:.3f}/s).", flush=True)


def validate_draft_target(draft_name: str, replace: bool) -> None:
    draft_root = Path(required("JIAN_YING_DRAFT_DIR")).expanduser()
    if not draft_root.is_dir():
        raise RuntimeError(f"JIAN_YING_DRAFT_DIR does not exist: {draft_root}")
    if (draft_root / draft_name).exists() and not replace:
        raise RuntimeError(
            f"Draft already exists: {draft_root / draft_name}. Use a new --draft-name or add --replace before generating."
        )


def build_draft(scenes: list[Scene], draft_name: str, replace: bool, title: str) -> Path:
    from pyJianYingDraft import AudioMaterial, AudioSegment, ClipSettings, DraftFolder, FontType, IntroType, KeyframeProperty, OutroType, TextBackground, TextBorder, TextSegment, TextStyle, Timerange, TrackSpec, TrackType, VideoMaterial, VideoSegment

    draft_root = Path(required("JIAN_YING_DRAFT_DIR")).expanduser()
    if not draft_root.is_dir():
        raise RuntimeError(f"JIAN_YING_DRAFT_DIR does not exist: {draft_root}")
    draft = DraftFolder(str(draft_root)).create_draft(draft_name, 1920, 1080, fps=30, allow_replace=replace)
    video_track = draft.append_track(TrackSpec(TrackType.video, "visuals"))
    audio_track = draft.append_track(TrackSpec(TrackType.audio, "voiceover"))
    opening_sfx_path = resolve_asset_path(env_value("OPENING_SOUND_PATH", DEFAULT_OPENING_SOUND_PATH))
    if not opening_sfx_path.is_file():
        raise RuntimeError(f"Opening sound effect is missing: {opening_sfx_path}")
    opening_sfx_track = draft.append_track(TrackSpec(TrackType.audio, "opening_sfx"))
    watermark_track = None
    watermark_path = os.getenv("WATERMARK_PATH", "").strip()
    if watermark_path:
        resolved_watermark = resolve_asset_path(watermark_path)
        if not resolved_watermark.is_file():
            raise RuntimeError(f"WATERMARK_PATH does not exist: {resolved_watermark}")
        watermark_track = draft.append_track(TrackSpec(TrackType.video, "watermark"))
    narration_subtitle_track = draft.append_track(TrackSpec(TrackType.text, NARRATION_SUBTITLE_TRACK))
    narration_subtitle_y = bounded_env_int("NARRATION_SUBTITLE_Y", DEFAULT_NARRATION_SUBTITLE_Y, -1080, 1080) / 540
    title_overlay_track = draft.append_track(TrackSpec(TrackType.text, TITLE_OVERLAY_TRACK))
    bgm_path = os.getenv("BGM_PATH", "").strip()
    bgm_material = None
    bgm_track = None
    if bgm_path:
        resolved_bgm = resolve_asset_path(bgm_path)
        if not resolved_bgm.is_file():
            raise RuntimeError(f"BGM_PATH does not exist: {resolved_bgm}")
        bgm_material = AudioMaterial(str(resolved_bgm))
        bgm_track = draft.append_track(TrackSpec(TrackType.audio, "BGM"))
    cursor = 0
    for index, scene in enumerate(scenes, 1):
        audio = AudioMaterial(scene.audio_path or "")
        timerange = Timerange(cursor, audio.duration)
        if scene.video_path and Path(scene.video_path).is_file():
            dynamic_material = VideoMaterial(scene.video_path)
            if dynamic_material.duration < audio.duration:
                raise RuntimeError(f"Dynamic video is shorter than its voice-over for scene {index}.")
            video = VideoSegment(dynamic_material, timerange, source_timerange=Timerange(0, audio.duration))
        else:
            video = VideoSegment(scene.image_path or "", timerange)
            start_scale, end_scale = (1.0, 1.05) if index % 2 else (1.05, 1.0)
            video.add_keyframe(KeyframeProperty.uniform_scale, 0, start_scale)
            video.add_keyframe(KeyframeProperty.uniform_scale, audio.duration, end_scale)
            if index == 1:
                video.add_animation(getattr(IntroType, "\u6ed1\u7247\u6ed1\u52a8"), duration=1_400_000)
            elif index == 2:
                video.add_animation(getattr(OutroType, "\u653e\u5927"), duration=1_200_000)
        subtitle = TextSegment(
            scene.text, timerange,
            font=FontType["\u7279\u9ed1\u4f53"],
            style=TextStyle(size=NARRATION_SUBTITLE_SIZE, color=(0.0, 0.0, 0.0), align=1, auto_wrapping=True),
            clip_settings=ClipSettings(transform_x=0.0, transform_y=narration_subtitle_y),
            border=TextBorder(color=(1.0, 1.0, 1.0), width=40),
            background=TextBackground(color="#000000", alpha=0.3, round_radius=0.2, height=0.14, width=0.14, horizontal_offset=0.5, vertical_offset=0.5),
        )
        draft.add_segment(video, video_track)
        draft.add_segment(AudioSegment(audio, timerange), audio_track)
        draft.add_segment(subtitle, narration_subtitle_track)
        scene.duration_us = audio.duration
        cursor += audio.duration
    opening_sfx = AudioMaterial(str(opening_sfx_path))
    opening_sfx_duration = min(opening_sfx.duration, cursor)
    draft.add_segment(
        AudioSegment(opening_sfx, Timerange(0, opening_sfx_duration), source_timerange=Timerange(0, opening_sfx_duration)),
        opening_sfx_track,
    )
    if watermark_track is not None:
        watermark = VideoSegment(str(resolved_watermark), Timerange(0, cursor), clip_settings=ClipSettings(scale_x=1.0, scale_y=1.0, transform_x=0.0, transform_y=0.0))
        draft.add_segment(watermark, watermark_track)
    title_duration = min(cursor, 3_000_000)
    title_presets = [
        (TextStyle(size=12, bold=True, color=(0.92, 0.12, 0.12), align=1, auto_wrapping=True), TextBorder(color=(1.0, 1.0, 1.0), width=40)),
        (TextStyle(size=12, bold=True, color=(1.0, 1.0, 1.0), align=1, auto_wrapping=True), TextBorder(color=(0.92, 0.12, 0.12), width=40)),
        (TextStyle(size=12, bold=True, color=(1.0, 0.82, 0.12), align=1, auto_wrapping=True), TextBorder(color=(0.10, 0.10, 0.10), width=40)),
    ]
    title_style, title_border = random.choice(title_presets)
    title_segment = TextSegment(title, Timerange(0, title_duration), style=title_style, border=title_border, clip_settings=ClipSettings(scale_x=TITLE_SCALE, scale_y=TITLE_SCALE, transform_x=0.0, transform_y=0.0))
    draft.add_segment(title_segment, title_overlay_track)

    if bgm_material is not None and bgm_track is not None:
        bgm_volume = BGM_VOLUME_RATIO
        bgm_cursor = 0
        while bgm_cursor < cursor:
            duration = min(bgm_material.duration, cursor - bgm_cursor)
            segment = AudioSegment(bgm_material, Timerange(bgm_cursor, duration), source_timerange=Timerange(0, duration), volume=bgm_volume)
            has_previous_loop = bgm_cursor > 0
            has_next_loop = bgm_cursor + duration < cursor
            fade_duration = min(400_000, duration // 2)
            if fade_duration and (has_previous_loop or has_next_loop):
                segment.add_fade(fade_duration if has_previous_loop else 0, fade_duration if has_next_loop else 0)
            draft.add_segment(segment, bgm_track)
            bgm_cursor += duration
    draft.save()
    return draft_root / draft_name


def save_run_state(asset_root: Path, draft_name: str, title: str, copy: str, scenes: list[Scene], status: str, failures: list[dict[str, Any]]) -> None:
    state = {
        "version": 2,
        "draft_name": draft_name,
        "title": title,
        "copy": copy,
        "status": status,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scenes": [asdict(scene) for scene in scenes],
        "failures": failures,
    }
    (asset_root / "manifest.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    (asset_root / "failures.json").write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")


def append_run_log(asset_root: Path, event: str, **details: Any) -> None:
    record = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event, **details}
    with (asset_root / "run.log").open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a Jianying draft from copy.")
    source = parser.add_mutually_exclusive_group(required=False)
    source.add_argument("--text")
    source.add_argument("--input", type=Path)
    parser.add_argument("--title", help="Title overlay; defaults to the first line of the copy.")
    parser.add_argument("--draft-name")
    parser.add_argument("--resume", metavar="DRAFT_NAME", help="Resume a failed run stored under output/DRAFT_NAME.")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--check-config", action="store_true", help="Validate local configuration and assets without API calls.")
    parser.add_argument("--plan-only", "--dry-run", dest="plan_only", action="store_true", help="Generate and print a storyboard only; this still calls the storyboard API.")
    parser.add_argument("--skip-i2v", action="store_true", help="Do not call image-to-video; build a static keyframe draft instead.")
    args = parser.parse_args()
    load_env()
    if args.check_config:
        validate_local_configuration(skip_i2v=args.skip_i2v)
        print("Configuration OK")
        return 0
    failures: list[dict[str, Any]] = []

    if args.resume:
        draft_name = args.resume
        asset_root = ROOT / "output" / draft_name
        manifest_path = asset_root / "manifest.json"
        if not manifest_path.is_file():
            raise RuntimeError(f"Resume manifest does not exist: {manifest_path}")
        state = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        copy = state.get("copy", "")
        if not copy:
            raise RuntimeError("Resume manifest has no source copy; start a new run instead.")
        title = args.title or state.get("title") or next((line.strip() for line in copy.splitlines() if line.strip()), copy.strip())
        scenes = [Scene(**{key: item.get(key) for key in ("text", "image_prompt", "audio_path", "image_path", "duration_us", "video_task_id", "video_path")}) for item in state.get("scenes", [])]
        if not scenes:
            raise RuntimeError("Resume manifest has no scenes.")
        failures = state.get("failures", [])
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
        validate_draft_target(draft_name, args.replace)
        asset_root = ROOT / "output" / draft_name
        asset_root.mkdir(parents=True, exist_ok=True)
        target_scenes, maximum_scenes = scene_limits(copy)
        print_image_cost_estimate(target_scenes, maximum_scenes)
        append_run_log(asset_root, "storyboard_started")
        scenes = plan_scenes(copy)
        append_run_log(asset_root, "storyboard_completed", scene_count=len(scenes))
        save_run_state(asset_root, draft_name, title, copy, scenes, "planned", failures)

    if args.plan_only:
        print(json.dumps({"draft_name": draft_name, "scenes": [asdict(scene) for scene in scenes]}, ensure_ascii=False, indent=2))
        return 0

    audio_dir, image_dir = asset_root / "audio", asset_root / "images"
    audio_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)
    for index, scene in enumerate(scenes, 1):
        discovered_audio = audio_dir / f"{index:02d}.mp3"
        discovered_image = image_dir / f"{index:02d}.png"
        if not scene.audio_path and discovered_audio.is_file() and discovered_audio.stat().st_size > 0:
            scene.audio_path = str(discovered_audio.resolve())
        if not scene.image_path and discovered_image.is_file() and discovered_image.stat().st_size > 0:
            scene.image_path = str(discovered_image.resolve())
    save_run_state(asset_root, draft_name, title, copy, scenes, "reconciled", failures)
    append_run_log(asset_root, "assets_reconciled")

    tts_workers = positive_env_int("TTS_CONCURRENCY", 3)

    def make_tts(index: int, scene: Scene) -> tuple[int, Path]:
        audio_path = audio_dir / f"{index:02d}.mp3"
        synthesize_tts(scene.text, audio_path)
        return index, audio_path

    pending_tts = [(index, scene) for index, scene in enumerate(scenes, 1) if not scene.audio_path or not Path(scene.audio_path).is_file()]
    if pending_tts:
        append_run_log(asset_root, "tts_started", count=len(pending_tts), workers=tts_workers)
        completed_tts = 0
        report_progress("Voice-over", 0, len(pending_tts))
        with ThreadPoolExecutor(max_workers=tts_workers) as executor:
            futures = {executor.submit(make_tts, index, scene): index for index, scene in pending_tts}
            for future in as_completed(futures):
                index = futures[future]
                try:
                    _, audio_path = future.result()
                except Exception as exc:
                    failure = {"stage": "tts", "scene": index, "error": str(exc)}
                    failures.append(failure)
                    save_run_state(asset_root, draft_name, title, copy, scenes, "failed", failures)
                    append_run_log(asset_root, "tts_failed", **failure)
                    raise
                scenes[index - 1].audio_path = str(audio_path.resolve())
                save_run_state(asset_root, draft_name, title, copy, scenes, "tts_in_progress", failures)
                append_run_log(asset_root, "tts_completed", scene=index)
                completed_tts += 1
                report_progress("Voice-over", completed_tts, len(pending_tts))

    populate_audio_durations(scenes)
    save_run_state(asset_root, draft_name, title, copy, scenes, "audio_durations_ready", failures)

    pending_images = [(index, scene) for index, scene in enumerate(scenes, 1) if not scene.image_path or not Path(scene.image_path).is_file()]
    if args.resume:
        print_image_cost_estimate(len(pending_images), len(pending_images), "Estimated remaining image cost")
    image_workers = bounded_env_int("IMAGE_CONCURRENCY", DEFAULT_IMAGE_CONCURRENCY, 1, MAX_IMAGE_CONCURRENCY)

    def make_image(index: int, scene: Scene) -> tuple[int, Path]:
        image_path = image_dir / f"{index:02d}.png"
        generate_image(scene.image_prompt, image_path)
        return index, image_path

    if pending_images:
        active_image_workers = min(image_workers, len(pending_images))
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
                    save_run_state(asset_root, draft_name, title, copy, scenes, "failed", failures)
                    append_run_log(asset_root, "image_failed", **failure)
                else:
                    scenes[index - 1].image_path = str(image_path.resolve())
                    save_run_state(asset_root, draft_name, title, copy, scenes, "image_in_progress", failures)
                    append_run_log(asset_root, "image_completed", scene=index)
                finally:
                    finished_images += 1
                    report_progress("Images", finished_images, len(pending_images))

        if current_image_failures:
            save_run_state(asset_root, draft_name, title, copy, scenes, "failed", failures)
            first_failure = current_image_failures[0]
            raise RuntimeError(
                f"{len(current_image_failures)} image(s) failed; successful images were kept for --resume. "
                f"First failure: scene {first_failure['scene']}: {first_failure['error']}"
            )

    if args.skip_i2v:
        print("Dynamic videos: skipped (--skip-i2v); all scenes will use static-image keyframes.", flush=True)
        append_run_log(asset_root, "image_to_video_skipped")
    else:
        video_dir = asset_root / "videos"
        video_dir.mkdir(parents=True, exist_ok=True)
        selected_video_scenes = [(index, scene) for index, scene in enumerate(scenes, 1) if index in I2V_SCENE_INDICES]
        if selected_video_scenes:
            print_i2v_cost_estimate(scenes)
            report_progress("Dynamic videos", 0, len(selected_video_scenes))
        completed_videos = 0
        for index, scene in selected_video_scenes:
            video_path = video_dir / f"{index:02d}.mp4"
            if not scene.video_path and video_path.is_file() and video_path.stat().st_size > 0:
                scene.video_path = str(video_path.resolve())
            if scene.video_path and Path(scene.video_path).is_file():
                completed_videos += 1
                report_progress("Dynamic videos", completed_videos, len(selected_video_scenes))
                continue
            try:
                if not scene.video_task_id:
                    scene.video_task_id = submit_scene_video(scene, Path(scene.image_path or ""), scene.duration_us or 4_000_000)
                    save_run_state(asset_root, draft_name, title, copy, scenes, "video_submitted", failures)
                    append_run_log(asset_root, "video_submitted", scene=index, task_id=scene.video_task_id)
                wait_for_scene_video(scene.video_task_id, video_path)
            except Exception as exc:
                failure = {"stage": "image_to_video", "scene": index, "task_id": scene.video_task_id, "error": str(exc)}
                failures.append(failure)
                save_run_state(asset_root, draft_name, title, copy, scenes, "failed", failures)
                append_run_log(asset_root, "image_to_video_failed", **failure)
                raise
            scene.video_path = str(video_path.resolve())
            save_run_state(asset_root, draft_name, title, copy, scenes, "video_in_progress", failures)
            append_run_log(asset_root, "video_completed", scene=index, task_id=scene.video_task_id)
            completed_videos += 1
            report_progress("Dynamic videos", completed_videos, len(selected_video_scenes))

    report_progress("Draft", 0, 1)
    try:
        draft_path = build_draft(scenes, draft_name, args.replace or bool(args.resume), title)
    except Exception as exc:
        failure = {"stage": "draft", "error": str(exc)}
        failures.append(failure)
        save_run_state(asset_root, draft_name, title, copy, scenes, "failed", failures)
        append_run_log(asset_root, "draft_failed", **failure)
        raise
    report_progress("Draft", 1, 1)
    failures = []
    save_run_state(asset_root, draft_name, title, copy, scenes, "completed", failures)
    append_run_log(asset_root, "completed", draft_path=str(draft_path))
    print(f"Done: {draft_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

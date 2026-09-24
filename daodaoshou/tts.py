"""Narration: reading each line aloud."""

from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from . import net
from .assets import write_atomically
from .models import Scene
from .net import ensure_ok

if TYPE_CHECKING:
    from .config import Config



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
    response = net.post(
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



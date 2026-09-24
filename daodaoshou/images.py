"""Pictures: the prompt each frame is drawn from, and the request that draws it."""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import net
from .assets import write_atomically
from .models import Character, Scene
from .net import ensure_ok
from .styles import COMPOSITION, DEFAULT_SHOT_SIZE, DEFAULT_STYLE_MEDIUM, DEFAULT_SUBJECT, SHOT_SIZES

if TYPE_CHECKING:
    from .config import Config



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
# images, which varies by model and plan, and unlike the rest of this tool it
# has not been measured against the reference video. An endpoint that
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
    response = net.post(
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
    download = ensure_ok(net.get(image_url, timeout=300), "Image download")
    write_atomically(target, download.content)



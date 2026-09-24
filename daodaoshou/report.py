"""What the terminal says along the way: progress, the bill, and the summary at the end."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .models import Scene

if TYPE_CHECKING:
    from .config import Config




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


def describe_image_budget(cfg: Config, to_draw: int, reused: int) -> str:
    """The exact number of frames a run is about to pay for, and their price if known."""
    line = f"Images: {to_draw} to draw, {reused} reused"
    if to_draw and cfg.ark_image_cny_per_image is not None:
        line += (f" - about CNY {to_draw * cfg.ark_image_cny_per_image:.2f} "
                 f"(CNY {cfg.ark_image_cny_per_image:.2f} each on {cfg.ark_image_model}; pictures only)")
    elif to_draw:
        line += f" on {cfg.ark_image_model} (set ARK_IMAGE_CNY_PER_IMAGE for a price)"
    if cfg.max_images is not None:
        line += f"; MAX_IMAGES={cfg.max_images}"
    return line + "."


# --------------------------------------------------------------- summary ----

def format_length(microseconds: int) -> str:
    seconds = microseconds / 1_000_000
    return f"{seconds:.1f}s" if seconds < 60 else f"{int(seconds // 60)}:{seconds % 60:04.1f}"


def run_summary(cfg: Config, scenes: list[Scene], *, duration_us: int, narration_read: int,
                pictures_drawn: int, title_voice: str, music: str) -> list[str]:
    """What a finished run made, what it reused, and what it cost - in one place.

    Everything here was printed somewhere along the way, between progress bars
    and retries. The end is where somebody - or an agent reading the output -
    looks to see what they got.
    """
    total = len(scenes)
    paragraphs = 1 + sum(scene.pause_after for scene in scenes[:-1])
    cost = ""
    if pictures_drawn and cfg.ark_image_cny_per_image is not None:
        cost = f" (about CNY {pictures_drawn * cfg.ark_image_cny_per_image:.2f})"
    label = f" {cfg.style.label}" if cfg.style.label else ""
    colouring = "ramp" if cfg.title_ramp else "one colour per line"
    return [
        f"  Scenes      {total}, in {paragraphs} paragraph(s)",
        f"  Length      {format_length(duration_us)} at {cfg.speed:.2f}x",
        f"  Narration   {total} line(s) - {narration_read} read this run, {total - narration_read} reused; "
        f"title voice {title_voice}",
        f"  Pictures    {total} frame(s) - {pictures_drawn} drawn this run{cost}, {total - pictures_drawn} reused",
        f"  Music       {music}",
        f"  Look        {cfg.style_preset}{label}, grade {cfg.color_grade or 'none'}, "
        f"title {cfg.title_style} ({colouring})",
    ]



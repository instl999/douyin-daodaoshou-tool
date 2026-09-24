"""What a storyboard is made of: its cast, its scenes, and where they sit."""

from __future__ import annotations

from dataclasses import dataclass, field

from .styles import DEFAULT_SHOT_SIZE


@dataclass
class Character:
    """A recurring person, described once and reused verbatim in every prompt."""

    id: str
    desc: str


@dataclass
class Scene:
    text: str
    image_prompt: str
    # The one thing the frame is about, named by the director as a thing that
    # can be drawn. It is what COMPOSITION builds its hierarchy around, and
    # asking for it separately is what stops the image prompt from being a
    # list of everything in the sentence.
    subject: str = ""
    shot_size: str = DEFAULT_SHOT_SIZE
    pause_after: bool = False
    cast: list[str] = field(default_factory=list)
    audio_path: str | None = None
    image_path: str | None = None
    duration_us: int | None = None
    # How many times the frame has been redrawn with --redo. Part of the
    # frame's key, so a redraw is a new file rather than the old one found
    # again, and it moves a fixed ARK_IMAGE_SEED on so the redraw differs.
    take: int = 0


SCENE_FIELDS = ("text", "image_prompt", "subject", "shot_size", "pause_after",
                "cast", "audio_path", "image_path", "duration_us", "take")


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



"""Whole runs of the command, with the paid services replaced by fakes.

The unit tests cover the arithmetic and the integration test covers the draft;
this covers what `main()` decides between them - which files a run reuses,
which it pays for again, and what it says about it. Every regression here was
a run that reported success over the wrong narration or the wrong pictures.
"""

from __future__ import annotations

import io
import json
import struct
import sys
import wave
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import animated_caption_draft as acd  # noqa: E402
from daodaoshou import env, images, jianying, storyboard, tts  # noqa: E402

pytest.importorskip("PIL", reason="Pillow is required to synthesise test images")
pytest.importorskip("pymediainfo", reason="pyJianYingDraft needs pymediainfo to probe media")

# How fast the fake voice reads, so every clip has a length of its own.
CHARACTERS_PER_SECOND = 6

# Settings a developer's shell could carry into a run and change its outcome.
ISOLATED_SETTINGS = (
    "IMAGE_STYLE_PRESET", "IMAGE_STYLE_PROMPT", "IMAGE_STYLES_FILE", "VIDEO_SPEED",
    "ARK_IMAGE_SEED", "ARK_IMAGE_CNY_PER_IMAGE", "DEEPSEEK_API_KEY", "BGM_PATH", "BGM_LIBRARY",
    "WATERMARK_PATH", "SPEAK_TITLE", "ARK_TTS_SPEECH_RATE", "ARK_TTS_LOUDNESS_RATE",
    "COLOR_GRADE", "TITLE_STYLE", "MAX_IMAGES", "IMAGE_REFERENCE", "TITLE_COLOR_MODE",
)

FIRST_SPLIT = ["楼下那家旧书店，我路过了三年", "一次也没进去过", "店主是个头发花白的老人"]
# The same copy split again, the way a second storyboard call at temperature
# 0.55 does: the lines move, so line N is no longer what clip N says.
SECOND_SPLIT = ["楼下那家旧书店，我路过了三年，一次也没进去过", "店主是个头发花白的老人", "总坐在门口的藤椅上"]
COPY = "楼下那家旧书店，我路过了三年，一次也没进去过。店主是个头发花白的老人，总坐在门口的藤椅上。"


def _wav(seconds: float) -> bytes:
    frames = max(1, int(24000 * seconds))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(struct.pack(f"<{frames}h", *([0] * frames)))
    return buffer.getvalue()


def _png() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (320, 180), (20, 30, 70)).save(buffer, format="PNG")
    return buffer.getvalue()


@dataclass
class Studio:
    """A repository, a Jianying drafts folder, and fake paid services."""

    root: Path
    drafts: Path
    lines: list[str] = field(default_factory=lambda: list(FIRST_SPLIT))
    # What each file on disk was made from, as the fakes were asked for it.
    spoken: dict[str, str] = field(default_factory=dict)
    drawn: dict[str, str] = field(default_factory=dict)
    # This run's calls, cleared by run().
    tts_calls: list[str] = field(default_factory=list)
    image_calls: list[tuple[str, int | None]] = field(default_factory=list)
    # (file name, the reference sent with it or None), in the order drawn.
    references: list[tuple[str, str | None]] = field(default_factory=list)
    fail_images: set[int] = field(default_factory=set)

    def run(self, *argv: str) -> int:
        self.tts_calls.clear()
        self.image_calls.clear()
        self.references.clear()
        return acd.main(list(argv))

    def draft(self, name: str) -> dict:
        return json.loads((self.drafts / name / "draft_content.json").read_text(encoding="utf-8"))

    def pairs(self, name: str) -> list[tuple[str, str]]:
        """(subtitle, the file narrated under it) for every scene, in order."""
        content = self.draft(name)
        texts = {m["id"]: json.loads(m["content"])["text"] for m in content["materials"]["texts"]}
        audios = {m["id"]: Path(m["path"]).name for m in content["materials"]["audios"]}
        tracks = {track["name"]: track for track in content["tracks"]}
        voice = {seg["target_timerange"]["start"]: audios[seg["material_id"]]
                 for seg in tracks["voiceover"]["segments"]}
        return [(texts[seg["material_id"]], voice[seg["target_timerange"]["start"]])
                for seg in sorted(tracks[acd.NARRATION_SUBTITLE_TRACK]["segments"],
                                  key=lambda seg: seg["target_timerange"]["start"])]

    def frames(self, name: str) -> list[str]:
        content = self.draft(name)
        videos = {m["id"]: Path(m["path"]).name for m in content["materials"]["videos"]}
        visuals = next(track for track in content["tracks"] if track["name"] == "visuals")
        return [videos[seg["material_id"]]
                for seg in sorted(visuals["segments"], key=lambda seg: seg["target_timerange"]["start"])]


@pytest.fixture
def studio(tmp_path, monkeypatch):
    root, drafts = tmp_path / "repo", tmp_path / "drafts"
    (root / "assets").mkdir(parents=True)
    drafts.mkdir()
    (root / "assets" / "cue.wav").write_bytes(_wav(0.6))
    made = Studio(root, drafts)

    for name in ISOLATED_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    for name, value in {
        "ARK_API_KEY": "test-key",
        "ARK_TTS_VOICE_TYPE": "voice-a",
        "JIAN_YING_DRAFT_DIR": str(drafts),
        "OPENING_SOUND_PATH": str(root / "assets" / "cue.wav"),
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(env, "ROOT", root)

    def plan(cfg, copy):
        scenes = [acd.Scene(text=line, image_prompt=f"a quiet bookshop, moment {index}",
                            subject="an old bookshop", shot_size=("wide", "medium", "close")[index % 3],
                            cast=["A"] if index == 2 else [])
                  for index, line in enumerate(made.lines, 1)]
        return scenes, [acd.Character("A", "an old man with white hair")], []

    def speak(cfg, text, target):
        made.tts_calls.append(text)
        made.spoken[target.name] = text
        acd.write_atomically(target, _wav(max(0.4, len(text) / CHARACTERS_PER_SECOND)))

    def draw(cfg, prompt, target, seed=None, reference=None):
        if int(target.name[:2]) in made.fail_images:
            raise RuntimeError("the image service fell over")
        made.image_calls.append((target.name, seed))
        made.references.append((target.name, reference))
        made.drawn[target.name] = prompt
        acd.write_atomically(target, _png())

    monkeypatch.setattr(storyboard, "plan_scenes", plan)
    monkeypatch.setattr(tts, "synthesize_tts", speak)
    monkeypatch.setattr(images, "generate_image", draw)
    return made


def _config() -> acd.Config:
    acd.load_env()
    return acd.Config.load()


# ------------------------------------------------ what a fresh run may reuse --

def test_a_fresh_rerun_never_narrates_one_line_under_another(studio):
    """The failed-run-then-same-command case, which used to report success.

    The first run fails on its images after every clip is read. Re-running
    the command re-splits the copy; clip 1 was read for the first run's line 1
    and the new line 1 is longer. Adopting by number put every subtitle over a
    clip saying something else, and made no API call at all to notice.
    """
    studio.fail_images = {3}
    with pytest.raises(RuntimeError, match="image"):
        studio.run("--draft-name", "story", "--text", COPY)
    assert not (studio.drafts / "story").exists(), "a failed run leaves no draft, so no --replace is needed"

    studio.lines, studio.fail_images = list(SECOND_SPLIT), set()
    assert studio.run("--draft-name", "story", "--text", COPY) == 0

    for subtitle, clip in studio.pairs("story"):
        assert studio.spoken[clip] == subtitle, f"{subtitle!r} is narrated by a clip reading {studio.spoken[clip]!r}"
    # No scene of the new split has the old one's text at its number, so each
    # was read afresh - which is the API call the old run never made.
    assert sorted(studio.tts_calls) == sorted(SECOND_SPLIT)


def test_a_fresh_rerun_of_the_same_storyboard_pays_for_nothing_twice(studio):
    """Keys make reuse safe, which is what lets it still happen."""
    assert studio.run("--draft-name", "story", "--text", COPY) == 0
    assert studio.run("--draft-name", "story", "--text", COPY, "--replace") == 0
    assert studio.tts_calls == [] and studio.image_calls == []


def test_a_resume_with_nothing_changed_pays_for_nothing(studio):
    assert studio.run("--draft-name", "story", "--text", COPY) == 0
    assert studio.run("--resume", "story", "--replace") == 0
    assert studio.tts_calls == [] and studio.image_calls == []


# --------------------------------------------- what a changed setting redoes --

def test_changing_the_style_redraws_every_frame_on_resume(studio, monkeypatch, capsys):
    """The old frames under the new style's grade and title was the bug.

    A resume after IMAGE_STYLE_PRESET=noir drew nothing, kept the navy
    paintings and laid a black-and-white filter and a white title over them.
    """
    assert studio.run("--draft-name", "story", "--text", COPY) == 0
    before = studio.frames("story")

    monkeypatch.setenv("IMAGE_STYLE_PRESET", "noir")
    assert studio.run("--resume", "story", "--replace") == 0
    after = studio.frames("story")

    assert len(studio.image_calls) == len(FIRST_SPLIT)
    assert studio.tts_calls == [], "the narration does not depend on the look"
    assert not set(before) & set(after), "an old frame survived the change of style"
    assert "style changed (midnight -> noir)" in capsys.readouterr().out


def test_changing_the_voice_rereads_every_line_and_the_title(studio, monkeypatch, capsys):
    """A new voice used to read only the missing lines - two voices in one video."""
    title = "一个正文里没有的标题"
    assert studio.run("--draft-name", "story", "--text", COPY, "--title", title) == 0

    monkeypatch.setenv("ARK_TTS_VOICE_TYPE", "voice-b")
    assert studio.run("--resume", "story", "--replace") == 0

    assert sorted(studio.tts_calls) == sorted([*FIRST_SPLIT, title])
    assert studio.image_calls == [], "the pictures do not depend on the voice"
    assert "voice changed (voice-a -> voice-b)" in capsys.readouterr().out


def test_a_different_speed_rereads_the_narration_through_its_key(studio):
    assert studio.run("--draft-name", "story", "--text", COPY) == 0
    assert studio.run("--resume", "story", "--replace", "--speed", "1.5") == 0
    assert sorted(studio.tts_calls) == sorted(FIRST_SPLIT)
    assert studio.image_calls == []


def test_an_edited_prompt_redraws_only_its_own_frame(studio):
    """Editing the manifest between runs is the documented way to fix a frame."""
    assert studio.run("--draft-name", "story", "--text", COPY) == 0
    manifest = studio.root / "output" / "story" / "manifest.json"
    state = json.loads(manifest.read_text(encoding="utf-8"))
    state["scenes"][1]["image_prompt"] = "the old man reading by the window"
    manifest.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    assert studio.run("--resume", "story", "--replace") == 0
    assert [name[:2] for name, _ in studio.image_calls] == ["02"]


# ---------------------------------------------------- a run from before v9 --

def test_an_older_runs_files_are_trusted_once_and_renamed(studio):
    """A v8 run resumed after the upgrade keeps what it paid for."""
    output = studio.root / "output" / "story"
    (output / "audio").mkdir(parents=True)
    (output / "images").mkdir()
    scenes = []
    for index, line in enumerate(FIRST_SPLIT, 1):
        (output / "audio" / f"{index:02d}.mp3").write_bytes(_wav(1.0))
        (output / "images" / f"{index:02d}.png").write_bytes(_png())
        scenes.append({"text": line, "image_prompt": f"frame {index}", "subject": "", "shot_size": "medium",
                       "pause_after": False, "cast": [], "duration_us": None,
                       "audio_path": str(output / "audio" / f"{index:02d}.mp3"),
                       "image_path": str(output / "images" / f"{index:02d}.png")})
    (output / "manifest.json").write_text(json.dumps({
        "version": 8, "draft_name": "story", "title": FIRST_SPLIT[0], "copy": COPY, "status": "failed",
        "speed": acd.DEFAULT_VIDEO_SPEED, "moods": [], "characters": [], "scenes": scenes, "failures": [],
    }, ensure_ascii=False), encoding="utf-8")

    assert studio.run("--resume", "story") == 0
    assert studio.tts_calls == [] and studio.image_calls == []
    assert not list((output / "images").glob("[0-9][0-9].png")), "the old names were not migrated"
    assert studio.run("--resume", "story", "--replace") == 0
    assert studio.tts_calls == [] and studio.image_calls == [], "migrated files must be found by key next time"


def test_a_fresh_run_never_trusts_an_older_runs_files(studio):
    """Only a resume of that same run may vouch for unkeyed files."""
    output = studio.root / "output" / "story"
    (output / "audio").mkdir(parents=True)
    (output / "images").mkdir()
    for index in range(1, 4):
        (output / "audio" / f"{index:02d}.mp3").write_bytes(_wav(1.0))
        (output / "images" / f"{index:02d}.png").write_bytes(_png())
    assert studio.run("--draft-name", "story", "--text", COPY) == 0
    assert sorted(studio.tts_calls) == sorted(FIRST_SPLIT)
    assert len(studio.image_calls) == len(FIRST_SPLIT)


# ----------------------------------------------------------------- the keys --

def test_a_narration_key_is_the_text_the_voice_and_the_rate(studio):
    cfg = _config()
    key = acd.narration_key(cfg, "同一句话")
    assert acd.narration_key(cfg, "  同一句话  ") == key
    assert acd.narration_key(cfg, "另一句话") != key
    assert acd.narration_key(replace(cfg, ark_tts_voice_type="voice-b"), "同一句话") != key
    assert acd.narration_key(replace(cfg, ark_tts_speech_rate=cfg.ark_tts_speech_rate + 10), "同一句话") != key
    assert acd.narration_key(replace(cfg, ark_tts_loudness_rate=10), "同一句话") != key


def test_a_title_change_is_a_different_clip(studio):
    """--resume with another --title used to speak the old one."""
    cfg = _config()
    assert acd.narration_key(cfg, "男人不能为女人做的3件事") != acd.narration_key(cfg, "女人不能为男人做的3件事")


def test_a_picture_key_is_everything_that_draws_the_frame_and_nothing_else(studio):
    cfg = _config()
    cast = [acd.Character("A", "an old man with white hair")]
    scene = acd.Scene(text="一句话", image_prompt="a shop at dusk", subject="an old shop", cast=["A"])
    key = acd.picture_key(cfg, scene, cast)

    assert acd.picture_key(cfg, replace(scene, text="改过错字的一句话"), cast) == key, \
        "fixing a subtitle must not redraw its picture"
    for changed in (replace(scene, image_prompt="a shop at dawn"), replace(scene, subject="a bell"),
                    replace(scene, shot_size="close"), replace(scene, take=1)):
        assert acd.picture_key(cfg, changed, cast) != key
    assert acd.picture_key(cfg, scene, [acd.Character("A", "a young woman")]) != key
    assert acd.picture_key(replace(cfg, image_style_prompt="ink on paper"), scene, cast) != key
    assert acd.picture_key(replace(cfg, ark_image_seed=7), scene, cast) != key


def test_a_take_moves_a_fixed_seed_on(studio):
    cfg = replace(_config(), ark_image_seed=100)
    assert acd.image_seed(cfg, acd.Scene(text="x", image_prompt="y")) == 100
    assert acd.image_seed(cfg, acd.Scene(text="x", image_prompt="y", take=2)) == 102
    assert acd.image_seed(replace(cfg, ark_image_seed=None), acd.Scene(text="x", image_prompt="y")) is None


def test_a_file_is_written_whole_or_not_at_all(tmp_path, monkeypatch):
    target = tmp_path / "07_abc.mp3"
    acd.write_atomically(target, b"audio")
    assert target.read_bytes() == b"audio"
    assert not list(tmp_path.glob("*.part"))

    def crash(self, other):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", crash)
    with pytest.raises(OSError):
        acd.write_atomically(tmp_path / "08_def.mp3", b"half")
    assert not (tmp_path / "08_def.mp3").exists(), "half a clip under a good name would be adopted"


# ------------------------------------------------ one frame the rest match --

def test_the_anchor_is_drawn_first_and_every_other_frame_is_matched_to_it(studio, monkeypatch):
    """Scene 2 is the first to show the recurring cast, so it anchors the look."""
    monkeypatch.setenv("IMAGE_REFERENCE", "anchor")
    assert studio.run("--draft-name", "story", "--text", COPY) == 0

    (anchor, none), *rest = studio.references
    assert anchor.startswith("02_") and none is None, "the anchor is drawn first, alone, from words"
    assert rest and all(reference.startswith("data:image/jpeg;base64,") for _, reference in rest)
    assert acd.REFERENCE_NOTE not in studio.drawn[anchor]
    assert all(acd.REFERENCE_NOTE in studio.drawn[name] for name, _ in rest), \
        "a reference without the note is read as 'draw this again'"


def test_a_frame_drawn_later_is_matched_to_the_anchor_already_there(studio, monkeypatch):
    monkeypatch.setenv("IMAGE_REFERENCE", "anchor")
    assert studio.run("--draft-name", "story", "--text", COPY) == 0
    manifest = studio.root / "output" / "story" / "manifest.json"
    state = json.loads(manifest.read_text(encoding="utf-8"))
    state["scenes"][2]["image_prompt"] = "the shop door closing"
    manifest.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    assert studio.run("--resume", "story", "--replace") == 0
    [(name, reference)] = studio.references
    assert name.startswith("03_") and reference is not None


def test_a_failed_anchor_stops_before_the_frames_matched_to_it(studio, monkeypatch):
    monkeypatch.setenv("IMAGE_REFERENCE", "anchor")
    studio.fail_images = {2}
    with pytest.raises(RuntimeError, match=r"reference frame \(scene 2\)"):
        studio.run("--draft-name", "story", "--text", COPY)
    assert studio.references == [], "nothing may be drawn against a reference that does not exist"


def test_without_the_setting_no_reference_is_sent(studio):
    assert studio.run("--draft-name", "story", "--text", COPY) == 0
    assert [reference for _, reference in studio.references] == [None] * len(FIRST_SPLIT)


# ------------------------------------------------ plan, edit, then resume --

def test_a_storyboard_can_be_planned_without_jianying_a_voice_or_the_cue(studio, monkeypatch):
    for name in ("JIAN_YING_DRAFT_DIR", "ARK_TTS_VOICE_TYPE"):
        monkeypatch.delenv(name)
    monkeypatch.setenv("OPENING_SOUND_PATH", str(studio.root / "missing.mp3"))
    monkeypatch.setattr(jianying, "jianying_app_roots", list)
    assert studio.run("--draft-name", "plan", "--text", COPY, "--plan-only") == 0
    assert (studio.root / "output" / "plan" / "manifest.json").is_file()
    assert studio.tts_calls == [] and studio.image_calls == []


def test_planning_never_replaces_a_storyboard_unasked(studio):
    assert studio.run("--draft-name", "plan", "--text", COPY, "--plan-only") == 0
    with pytest.raises(RuntimeError, match="already holds a storyboard"):
        studio.run("--draft-name", "plan", "--text", COPY, "--plan-only")
    assert studio.run("--draft-name", "plan", "--text", COPY, "--plan-only", "--replace") == 0


def test_the_reviewed_storyboard_is_the_one_that_gets_made(studio, monkeypatch):
    """Plan, edit the manifest, resume: nothing is re-planned under you."""
    assert studio.run("--draft-name", "plan", "--text", COPY, "--plan-only") == 0
    manifest = studio.root / "output" / "plan" / "manifest.json"
    state = json.loads(manifest.read_text(encoding="utf-8"))
    state["scenes"][0]["text"] = "楼下那家旧书店，我整整路过了三年"
    manifest.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(storyboard, "plan_scenes", lambda *a: pytest.fail("a resume must not plan again"))
    assert studio.run("--resume", "plan") == 0
    assert studio.pairs("plan")[0][0] == "楼下那家旧书店，我整整路过了三年"


# ------------------------------------------------------------------ --redo --

def test_redo_draws_only_the_named_scenes_again(studio):
    assert studio.run("--draft-name", "story", "--text", COPY) == 0
    before = studio.frames("story")

    assert studio.run("--resume", "story", "--replace", "--redo", "2") == 0
    after = studio.frames("story")
    assert [name[:2] for name, _ in studio.image_calls] == ["02"]
    assert studio.tts_calls == []
    assert after[0] == before[0] and after[2] == before[2]
    assert after[1] != before[1], "the draft must show the new take"
    assert (studio.root / "output" / "story" / "images" / before[1]).is_file(), "the earlier take stays on disk"
    state = json.loads((studio.root / "output" / "story" / "manifest.json").read_text(encoding="utf-8"))
    assert [scene["take"] for scene in state["scenes"]] == [0, 1, 0]


def test_a_redo_with_a_fixed_seed_draws_with_the_next_one(studio, monkeypatch):
    """The same prompt and the same seed would return the same picture."""
    monkeypatch.setenv("ARK_IMAGE_SEED", "100")
    assert studio.run("--draft-name", "story", "--text", COPY) == 0
    assert {seed for _, seed in studio.image_calls} == {100}
    assert studio.run("--resume", "story", "--replace", "--redo", "1-2") == 0
    assert sorted(seed for _, seed in studio.image_calls) == [101, 101]
    assert studio.run("--resume", "story", "--replace", "--redo", "1") == 0
    assert [seed for _, seed in studio.image_calls] == [102]


def test_redo_belongs_to_a_resume(studio):
    with pytest.raises(SystemExit):
        studio.run("--draft-name", "story", "--text", COPY, "--redo", "2")


def test_redo_names_scenes_the_storyboard_has(studio):
    assert studio.run("--draft-name", "story", "--text", COPY) == 0
    with pytest.raises(RuntimeError, match="scenes 1 to 3"):
        studio.run("--resume", "story", "--replace", "--redo", "4")


@pytest.mark.parametrize("text, expected", [
    ("3,7", [3, 7]), ("3-5,9", [3, 4, 5, 9]), ("5-3", [3, 4, 5]), (" 2 ，4 ", [2, 4]), ("2,2", [2]),
])
def test_scene_numbers_are_read_the_way_people_write_them(text, expected):
    assert acd.parse_scene_numbers(text, 10) == expected


@pytest.mark.parametrize("text", ["0", "11", "x", "3-", ",", ""])
def test_a_scene_number_that_is_not_one_is_refused(text):
    with pytest.raises(RuntimeError, match="--redo"):
        acd.parse_scene_numbers(text, 10)


# ------------------------------------------------------------ the bill ----

def test_the_exact_number_of_frames_is_said_before_anything_is_paid_for(studio, monkeypatch, capsys):
    monkeypatch.setenv("ARK_IMAGE_CNY_PER_IMAGE", "0.25")
    assert studio.run("--draft-name", "story", "--text", COPY) == 0
    assert "Images: 3 to draw, 0 reused - about CNY 0.75" in capsys.readouterr().out
    assert studio.run("--resume", "story", "--replace", "--redo", "2") == 0
    assert "Images: 1 to draw, 2 reused - about CNY 0.25" in capsys.readouterr().out


def test_a_run_over_max_images_stops_before_paying_for_media(studio, monkeypatch):
    monkeypatch.setenv("MAX_IMAGES", "2")
    with pytest.raises(RuntimeError, match="MAX_IMAGES is 2"):
        studio.run("--draft-name", "story", "--text", COPY)
    assert studio.tts_calls == [] and studio.image_calls == [], "nothing but the storyboard may be spent"

    monkeypatch.setenv("MAX_IMAGES", "3")
    assert studio.run("--resume", "story") == 0, "the saved storyboard carries on once the cap allows it"
    assert len(studio.image_calls) == 3


def test_the_cap_counts_only_what_a_run_will_draw(studio, monkeypatch):
    assert studio.run("--draft-name", "story", "--text", COPY) == 0
    monkeypatch.setenv("MAX_IMAGES", "1")
    assert studio.run("--resume", "story", "--replace", "--redo", "3") == 0


def test_a_plan_says_what_making_it_would_cost(studio, monkeypatch, capsys):
    monkeypatch.setenv("ARK_IMAGE_CNY_PER_IMAGE", "0.25")
    monkeypatch.setenv("MAX_IMAGES", "2")
    assert studio.run("--draft-name", "plan", "--text", COPY, "--plan-only") == 0
    out = capsys.readouterr().out
    assert "Plan: 3 scene(s). Images: 3 to draw, 0 reused - about CNY 0.75" in out
    assert "over MAX_IMAGES=2" in out


# ------------------------------------------------------------ the summary --

def _summary(out: str) -> str:
    return out[out.index("Done: "):]


def test_a_finished_run_ends_on_what_it_made_and_paid_for(studio, monkeypatch, capsys):
    monkeypatch.setenv("ARK_IMAGE_CNY_PER_IMAGE", "0.25")
    assert studio.run("--draft-name", "story", "--text", COPY, "--title", "一个正文里没有的标题") == 0
    summary = _summary(capsys.readouterr().out)
    assert "Scenes      3, in 1 paragraph(s)" in summary
    assert "Narration   3 line(s) - 3 read this run, 0 reused; title voice read this run" in summary
    assert "Pictures    3 frame(s) - 3 drawn this run (about CNY 0.75), 0 reused" in summary
    assert "Music       no music" in summary
    assert "Look        midnight 深蓝彩漫, grade 深蓝电影感, title crimson (ramp)" in summary
    content = studio.draft("story")
    assert f"Length      {acd.format_length(content['duration'])} at 1.20x" in summary


def test_a_resume_says_what_it_reused(studio, capsys):
    assert studio.run("--draft-name", "story", "--text", COPY, "--title", "一个正文里没有的标题") == 0
    capsys.readouterr()
    assert studio.run("--resume", "story", "--replace") == 0
    summary = _summary(capsys.readouterr().out)
    assert "0 read this run, 3 reused; title voice reused" in summary
    assert "0 drawn this run, 3 reused" in summary


def test_the_summary_is_in_the_run_log_too(studio):
    assert studio.run("--draft-name", "story", "--text", COPY) == 0
    events = [json.loads(line) for line in
              (studio.root / "output" / "story" / "run.log").read_text(encoding="utf-8").splitlines()]
    summary = next(event for event in events if event["event"] == "summary")
    assert any(line.startswith("Pictures") for line in summary["lines"])


@pytest.mark.parametrize("microseconds, shown", [(42_300_000, "42.3s"), (72_400_000, "1:12.4"),
                                                 (600_000_000, "10:00.0")])
def test_lengths_read_the_way_an_editor_writes_them(microseconds, shown):
    assert acd.format_length(microseconds) == shown

# Copy to Manhua Video Jianying Draft Generator

Turns Chinese copy into an editable Jianying (CapCut China) draft: AI storyboard, per-scene voice-over, one manhua panel per subtitle, live captions, a title card, an opening sound effect, plus optional BGM and watermark.

中文文档：[README.md](README.md)

## What it does

- Plans the storyboard, generates stills and synthesises speech through Volcengine Ark Agent Plan.
- One complete subtitle maps to one image. The panel follows only that subtitle; no charts, tickers or finance symbols are forced in.
- **Style presets**: ships with the short-video emotional-story look by default — thick even ink lines, flat muted colour, soft even light. Switch to a realistic Korean webtoon or a high-contrast cinematic look with one setting, or write your own.
- **Character consistency**: the storyboard step extracts a cast shared by the whole video and injects each description verbatim into every prompt, so the protagonist does not change face every few seconds.
- **One image, one whole shot**: `01.png` runs to its end and `02.png` follows; a single image is never cut into two segments.
- **Camera movement**: five Ken Burns moves (push, pull, pan left, pan right, push with a slight tilt) cycle one per scene. The starting scale is always above 1.0 so a pan never exposes the frame edge.
- **Every cut is hard** — no transitions, no intro animations. Motion comes entirely from the camera move.
- Stills are generated 3-way concurrent by default. One failure does not discard the successful images; `--resume` retries only what is missing.
- Builds the draft with separate tracks for visuals, voice-over, captions, title, opening SFX, BGM and watermark.

## Requirements

- Windows 10/11
- Python 3.10+
- Jianying Pro
- A Volcengine Ark Agent Plan API key

## Install

```powershell
git clone <repository-url>
Set-Location .\copy-to-manhua-jianying-draft
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` locally only. Never put real keys in `.env.example`, source files or docs.

## Local assets

The repository ships no personal or potentially licensed media. Add your own under `assets`:

```text
assets/
  opening_dong.mp3       # required: opening sound effect
  background_music.mp3   # optional: BGM
  watermark.png          # optional: watermark
```

Point `.env` at relative or absolute paths. BGM and watermark may be left empty; the opening sound effect is currently required.

## Art direction

Pick one of three with `IMAGE_STYLE_PRESET`:

| Preset | Look | Suits |
| --- | --- | --- |
| `story` (default) | Thick even ink lines, flat low-saturation colour, soft even light, rosy cheeks, sparse backgrounds | The dominant look on Chinese emotional-story short video; high recognition and the easiest to keep consistent |
| `webtoon` | Thinner lines, softer rendering, restrained colour | Reads as higher production value; suits career, finance and self-improvement topics |
| `cinematic` | Saturated deep navy with high-contrast cinematic lighting | The project's previous default; more dramatic but harder to keep consistent |

Set `IMAGE_STYLE_PROMPT` to override the preset with your own text. `--check-config` reports which one is in effect.

When changing style, also set a fixed `ARK_IMAGE_SEED`: the look holds together better and reruns become reproducible.

## Configuration

Every setting is documented inline in [.env.example](.env.example). The ones you will actually touch:

| Variable | Default | Meaning |
| --- | --- | --- |
| `ARK_API_KEY` | empty | Ark Agent Plan key, shared by storyboard, image and TTS. |
| `JIAN_YING_DRAFT_DIR` | empty | Local Jianying draft root. |
| `IMAGE_STYLE_PRESET` | `story` | Whole-video art direction; see above. |
| `SCENE_CHARACTERS_PER_IMAGE` | `22` | Chinese characters per shot, floor of 8. This is the only pacing control: lower means faster cuts, more images and higher cost. |
| `NARRATION_SUBTITLE_Y` | `-700` | Caption position; see the coordinate note below. |
| `BGM_VOLUME` | `0.10` | Linear BGM gain (about -20 dB). |
| `TITLE_STYLE` | `red` | Title colours: `red` / `white` / `gold`. |

### Layout coordinate units

`NARRATION_SUBTITLE_Y` and `TITLE_Y` are **pixels against a 1920-tall reference frame**, independent of the actual canvas height (positive is up):

```text
transform_y = value / 960          Jianying's visible range is -1 to 1
-700  ->  -0.729   about 146 px above the bottom edge of a 1080-tall canvas
+520  ->  +0.542   upper third, where the title sits
```

Out-of-range values are clamped into the safe area. `--check-config` prints where each layer actually lands:

```powershell
python animated_caption_draft.py --check-config
```

It also reports the **source** of each key setting (`.env`, process environment, or default). A stale shell variable silently shadowing `.env` is otherwise hard to spot.

## Usage

From a UTF-8 text file:

```powershell
python animated_caption_draft.py `
  --draft-name my_story `
  --title "Example title" `
  --input copy.txt
```

Short copy inline:

```powershell
python animated_caption_draft.py --draft-name demo --title "Example title" --text "Copy goes here."
```

Print the storyboard (with cast) without generating media:

```powershell
python animated_caption_draft.py --draft-name plan_preview --input copy.txt --plan-only
```

`--plan-only` still calls the Ark text endpoint. Add `--verbose` for a full traceback on failure.

## Timeline layout

```text
0s        OPENING_LEAD_SECONDS                                          end
|---------|--------------------------------------------------------------|
 SFX+title  line 1 narration   line 2 narration    ...      last line
[visuals]   one image per line, each running whole; every cut is hard
[captions]           one segment per line, with an intro animation
[BGM]       looped, 0.6s fade in at the head, 0.9s fade out at the tail
```

## Resuming

Each run is stored under:

```text
output/<draft-name>/
  manifest.json
  failures.json
  run.log
  audio/
  images/
```

Successful assets are reused.

```powershell
python animated_caption_draft.py --resume my_story
```

> **Overwriting an existing draft requires `--replace`, including with `--resume`.** Overwriting deletes the whole draft folder, so any edits you made in Jianying go with it. Close the draft in Jianying first.

## Development

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
```

The suite covers everything that does not call a paid API: layout coordinate conversion, camera-move parameters, BGM looping and fades, storyboard JSON tolerance, style presets, plus an integration test that builds a real draft from synthetic media and inspects the timeline.

## Troubleshooting

- **HTTP 429**: lower `IMAGE_CONCURRENCY`. Error messages include the API's response body, so rate limiting and an empty balance are distinguishable.
- **SSL EOF or a dropped connection**: retry later with `--resume`; drop to 1 worker if needed.
- **Opening sound effect not found**: check `OPENING_SOUND_PATH` points at an existing MP3 or WAV.
- **Jianying folder not found**: check `JIAN_YING_DRAFT_DIR` is the Jianying Pro draft root.
- **Caption sits wrong**: adjust `NARRATION_SUBTITLE_Y` and confirm with `--check-config`.
- **`.env` edits have no effect**: a process environment variable takes priority; `--check-config` shows the source.
- **The art style is off**: run `--plan-only` first and read the model's `image_prompt`. The style text is only appended after it, so a storyboard description that already went astray will not be fixed by changing preset.

## Privacy and release safety

`.gitignore` already excludes:

- `.env` and machine-specific configuration
- generated media, logs and run state under `output/`
- personal watermarks, sound effects and BGM under `assets/`
- Python caches, virtual environments and editor state

Run a secret scan before publishing anyway. If a key was ever committed or sent somewhere untrusted, revoke and reissue it immediately.

## Current limits

- Roughly 1800 non-whitespace characters of copy per run.
- The canvas is fixed at landscape 1920x1080.
- Stills only; there is no image-to-video stage.
- Output targets the Jianying Pro draft format; other editors are not guaranteed.
- Drafts are written with [pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft).

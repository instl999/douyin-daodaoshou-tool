# Copy to Manhua Video Jianying Draft Generator

Turns Chinese copy into an editable Jianying (CapCut China) draft: AI storyboard, per-scene voice-over, one manhua panel per subtitle, live captions, keyword highlights, a title card, an opening sound effect, plus optional BGM, watermark and image-to-video.

中文文档：[README.md](README.md)

## What it does

- Plans the storyboard, generates stills and synthesises speech through Volcengine Ark Agent Plan.
- One complete subtitle maps to one image. The panel follows only that subtitle; no charts, tickers or finance symbols are forced in.
- Ships a realistic narrative manhua look by default: bold black ink outlines, hard-edged cel shading, believable anatomy, strong expressions, deep navy against warm earth tones.
- **Character consistency**: the storyboard step extracts a cast shared by the whole video and injects each description verbatim into every prompt, so the protagonist does not change face every few seconds.
- **Cutting rhythm**: any shot longer than `MAX_SHOT_SECONDS` is split into a wide framing plus a punch-in off the same still, doubling the visual pace at no extra image cost.
- **Camera movement**: five Ken Burns moves (push, pull, pan left, pan right, push with a slight tilt) cycle across shots. The starting scale is always above 1.0 so a pan never exposes the frame edge.
- **Keyword highlight**: the storyboard model picks the load-bearing word in each line and it is shown enlarged above the subtitle.
- Stills are generated 3-way concurrent by default. One failure does not discard the successful images; `--resume` retries only what is missing.
- Builds the draft with separate tracks for visuals, voice-over, captions, keywords, title, opening SFX, BGM and watermark.
- Image-to-video is distributed across the whole video by default (hook, middle, closing beat) rather than bunched at the start. `--skip-i2v` turns it off.

## Requirements

- Windows 10/11
- Python 3.10+
- Jianying Pro
- A Volcengine Ark Agent Plan API key
- An Aliyun Bailian API key (only for image-to-video)

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

## Configuration

Every setting is documented inline in [.env.example](.env.example). The ones you will actually touch:

| Variable | Default | Meaning |
| --- | --- | --- |
| `ARK_API_KEY` | empty | Ark Agent Plan key, shared by storyboard, image and TTS. |
| `JIAN_YING_DRAFT_DIR` | empty | Local Jianying draft root. |
| `DASHSCOPE_API_KEY` | empty | Bailian key; image-to-video only. |
| `SCENE_CHARACTERS_PER_IMAGE` | `22` | Chinese characters per shot, floor of 8. Lower means faster cuts and higher cost. |
| `MAX_SHOT_SECONDS` | `3` | Longest single shot before it is split in two. **Reach for this first to speed up pacing; it is free.** |
| `NARRATION_SUBTITLE_Y` | `-700` | Caption position; see the coordinate note below. |
| `BGM_VOLUME` | `0.10` | Linear BGM gain (about -20 dB). |
| `KEYWORD_HIGHLIGHT` | `1` | Keyword overlay track. |
| `TITLE_STYLE` | `red` | Title colours: `red` / `white` / `gold`. |
| `I2V_SCENES` | `auto` | Which scenes become video; see below. |
| `IMAGE_STYLE_PROMPT` | manhua style | Whole-video art direction. |

### Layout coordinate units

`NARRATION_SUBTITLE_Y`, `KEYWORD_Y` and `TITLE_Y` are **pixels against a 1920-tall reference frame**, independent of the actual canvas height (positive is up):

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

Skip the image-to-video key check when working with stills only:

```powershell
python animated_caption_draft.py --check-config --skip-i2v
```

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

Stills only:

```powershell
python animated_caption_draft.py `
  --draft-name static_demo `
  --title "Example title" `
  --input copy.txt `
  --skip-i2v
```

Print the storyboard (with cast and keywords) without generating media:

```powershell
python animated_caption_draft.py --draft-name plan_preview --input copy.txt --plan-only
```

`--plan-only` still calls the Ark text endpoint. Add `--verbose` for a full traceback on failure.

## Timeline layout

```text
0s        OPENING_LEAD_SECONDS                                          end
|---------|--------------------------------------------------------------|
 SFX+title  line 1 narration   line 2 narration    ...      last line
[visuals]   shot 1 is on screen from 0s, so the video never opens on black
[captions]           one segment per line, with an intro animation
[keywords]           only on lines where the model found a load-bearing word
[BGM]       looped, 0.6s fade in at the head, 0.9s fade out at the tail
```

## Image-to-video strategy

`I2V_SCENES=auto` (default) spreads `I2V_MAX_COUNT` clips evenly across the video and **always includes the first and last scene**, so motion runs through the whole piece instead of stopping partway.

Pick scenes by hand, or turn it off:

```dotenv
I2V_SCENES=1,5,9,14
I2V_SCENES=none
```

Remaining scenes use stills with keyframed camera movement. Dynamic clips stay silent so they never fight the narration or BGM. When a returned clip is slightly shorter than its narration (frame rounding), the still covers the remainder rather than aborting the build.

## Resuming

Each run is stored under:

```text
output/<draft-name>/
  manifest.json
  failures.json
  run.log
  audio/
  images/
  videos/          # only when image-to-video is enabled
```

Successful assets are reused, including videos already paid for that are no longer in the current `I2V_SCENES` selection.

```powershell
python animated_caption_draft.py --resume my_story --skip-i2v
```

If the original run used image-to-video, do not add `--skip-i2v` when resuming.

> **Overwriting an existing draft requires `--replace`, including with `--resume`.** Overwriting deletes the whole draft folder, so any edits you made in Jianying go with it. Close the draft in Jianying first.

## Development

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
```

The suite covers everything that does not call a paid API: layout coordinate conversion, shot splitting, camera-move parameters, BGM looping and fades, storyboard JSON tolerance, image-to-video scene selection, plus an integration test that builds a real draft from synthetic media and inspects the timeline.

## Troubleshooting

- **HTTP 429**: lower `IMAGE_CONCURRENCY`. Error messages now include the API's response body, so rate limiting and an empty balance are distinguishable.
- **SSL EOF or a dropped connection**: retry later with `--resume`; drop to 1 worker if needed.
- **Opening sound effect not found**: check `OPENING_SOUND_PATH` points at an existing MP3 or WAV.
- **Jianying folder not found**: check `JIAN_YING_DRAFT_DIR` is the Jianying Pro draft root.
- **Caption sits wrong**: adjust `NARRATION_SUBTITLE_Y` and confirm with `--check-config`.
- **`.env` edits have no effect**: a process environment variable takes priority; `--check-config` shows the source.
- **Image-to-video submission fails on large images**: make sure Pillow is installed; the first frame is downscaled to a 720P JPEG before upload.

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
- Output targets the Jianying Pro draft format; other editors are not guaranteed.
- Drafts are written with [pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft).

# Script-to-Manhua Jianying Draft Generator

Turn Chinese narration into an editable Jianying Pro draft with AI storyboarding, per-scene voice-over, subtitle-matched manhua artwork, live captions, a title overlay, an opening sound, and optional BGM, watermark, and image-to-video clips.

[中文说明](README.md)

## Highlights

- Uses Volcengine Ark Agent Plan for storyboard planning, still-image generation, and TTS.
- Maps one complete subtitle scene to one directly relevant image. Unrelated charts, dashboards, or finance symbols are not forced into scenes.
- Uses a Chinese social-realism manhua look by default: bold black linework, hard-edged cel shading, semi-realistic anatomy, expressive acting, and deep-navy/warm-earth contrast.
- Generates still images with three concurrent workers by default. Successful assets survive individual request failures and are reused by `--resume`.
- Creates separate Jianying tracks for visuals, voice-over, live captions, title, opening sound, optional BGM, and optional watermark.
- Converts only storyboard scenes 2–6 to silent image-to-video clips by default. `--skip-i2v` disables image-to-video completely.
- Places narration captions at `x=0, y=-700` by default; the Y position is configurable in `.env`.

## Requirements

- Windows 10/11
- Python 3.10+
- Jianying Pro
- A Volcengine Ark Agent Plan API key
- An Alibaba Cloud Model Studio API key only when image-to-video is enabled

## Installation

```powershell
git clone <repository-url>
Set-Location .\copy-to-video-draft-generator
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` locally. Never place real credentials in `.env.example`, source files, or documentation.

## Local assets

Personal or potentially copyrighted media is not included. Add your own files locally:

```text
assets/
  opening_dong.mp3       # required opening sound
  background_music.mp3   # optional BGM
  watermark.png          # optional watermark
```

Set relative or absolute paths in `.env`. BGM and watermark may be empty; the opening sound is currently required.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `ARK_API_KEY` | empty | Shared Ark Agent Plan key for storyboards, images, and TTS. |
| `ARK_TEXT_MODEL` | `deepseek-v4-flash` | Storyboard model. |
| `ARK_IMAGE_MODEL` | `doubao-seedream-5.0-lite` | Still-image model. |
| `ARK_TTS_MODEL` | `seed-tts-2.0` | Speech model. |
| `ARK_TTS_VOICE_TYPE` | example voice | Seed TTS 2.0 voice ID. |
| `JIAN_YING_DRAFT_DIR` | empty | Local Jianying Pro draft root. |
| `IMAGE_CONCURRENCY` | `3` | Concurrent image requests; local range 1–8. Lower after HTTP 429 or SSL interruptions. |
| `TTS_CONCURRENCY` | `3` | Concurrent TTS requests. |
| `SCENE_CHARACTERS_PER_IMAGE` | `22` | Approximate Chinese characters per scene; 20–28 is recommended. |
| `SCENE_LENGTH_MODE` | `density` | `density` balances cost and count; `quality` favors short, focused scenes. |
| `IMAGE_STYLE_PROMPT` | manhua prompt | Shared visual direction for every image. |
| `NARRATION_SUBTITLE_Y` | `-700` | Shared Y position for narration captions. |
| `OPENING_SOUND_PATH` | `assets/opening_dong.mp3` | Opening sound path. |
| `BGM_PATH` | empty | Optional BGM path. |
| `WATERMARK_PATH` | empty | Optional watermark path. |
| `DASHSCOPE_API_KEY` | empty | Model Studio key; required only for image-to-video. |
| `DASHSCOPE_I2V_MODEL` | `wan2.6-i2v-flash` | Image-to-video model. |
| `DASHSCOPE_I2V_CNY_PER_SECOND` | `0.155` | Terminal estimate only, not a live price. |

Validate full local configuration without calling generation APIs:

```powershell
python animated_caption_draft.py --check-config
```

For a static-only workflow, skip the image-to-video key check:

```powershell
python animated_caption_draft.py --check-config --skip-i2v
```

## Usage

Generate from a UTF-8 text file:

```powershell
python animated_caption_draft.py `
  --draft-name my_story `
  --title "Example title" `
  --input copy.txt
```

Pass short copy directly:

```powershell
python animated_caption_draft.py --draft-name demo --title "Example title" --text "Your copy goes here."
```

Disable image-to-video:

```powershell
python animated_caption_draft.py `
  --draft-name static_demo `
  --title "Example title" `
  --input copy.txt `
  --skip-i2v
```

Generate and print only the storyboard plan:

```powershell
python animated_caption_draft.py --draft-name plan_preview --input copy.txt --plan-only
```

`--plan-only` still calls the Ark text endpoint.

## Image-to-video policy

Unless `--skip-i2v` is used:

| Storyboard position | Draft content |
| --- | --- |
| Scene 1 | Still image plus opening animation |
| Scenes 2–6 | Silent image-to-video clip plus matching voice-over and caption |
| Scene 7 onward | Still image plus zoom keyframes |

These are AI storyboard indices, not source-sentence indices. Dynamic clips remain silent to avoid colliding with voice-over or BGM.

## Resume

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

Successful assets are reused. Resume a failed static run with:

```powershell
python animated_caption_draft.py --resume my_story --skip-i2v
```

Do not add `--skip-i2v` when the original run used image-to-video. Use `--replace` to overwrite a same-name Jianying draft, and close that draft in Jianying first.

## Privacy and publishing safety

The repository `.gitignore` excludes:

- `.env` and machine-specific environment files
- generated media, logs, and task state under `output/`
- personal watermark, sound, and BGM files under `assets/`
- Python caches, virtual environments, and editor state

Run a credential scan before publishing. If a credential was committed or shared with an untrusted party, revoke and rotate it immediately.

## Troubleshooting

- **HTTP 429:** reduce `IMAGE_CONCURRENCY`.
- **SSL EOF or network interruption:** retry with `--resume`; optionally use one worker temporarily.
- **Opening sound missing:** point `OPENING_SOUND_PATH` to an existing MP3 or WAV file.
- **Draft directory missing:** make sure `JIAN_YING_DRAFT_DIR` is the Jianying Pro draft root.
- **Caption placement:** change `NARRATION_SUBTITLE_Y`; the default is `-700`.

## Current limitations

- A run accepts approximately 1,800 non-whitespace characters.
- Image-to-video is fixed to storyboard scenes 2–6.
- Output targets Jianying Pro draft format and is not guaranteed to work in other editors.
- Draft writing uses [pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft).

# Douyin "Xinli Daodaoshou" Same-Style Video Maker · Jianying Draft Generator

Recreates the video format of the Douyin blogger "Xinli Daodaoshou" ([creator page](https://v.douyin.com/AYhnYiaH0uo/)): it turns a piece of Chinese copy into an editable Jianying (CapCut China) draft — an AI storyboard, per-line voice-over, one panel per line, then a timeline with visuals, narration, captions, a title, sound effects, BGM and a colour grade already laid out.

> **Ships with Volcengine Ark Agent Plan API support** — storyboard, image
> generation and voice-over share one `ARK_API_KEY`, all routed through the
> Agent Plan. Under an Agent Plan subscription, **image generation costs
> nothing extra**.

It produces a **draft, not a finished video** — every asset and keyframe sits on the timeline, so you can adjust anything by hand and export it yourself.

中文文档：[README.md](README.md)

---

## Sixty-second start

```powershell
git clone https://github.com/instl999/douyin-daodaoshou-tool.git
Set-Location .\douyin-daodaoshou-tool
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Fill in three things in `.env` — `ARK_API_KEY`, `ARK_TTS_VOICE_TYPE`, `JIAN_YING_DRAFT_DIR` — and drop an opening sound effect into `assets/`. Then:

```powershell
python animated_caption_draft.py --check-config
```

If that passes, make a video:

```powershell
python animated_caption_draft.py --draft-name my_story --title "Example title" --input copy.txt
```

Open Jianying and `my_story` is waiting in the draft list.

---

## How it works

```text
copy ──▶ storyboard ──▶ voice-over ──▶ images ──▶ Jianying draft
        (text model)      (TTS)      (image model)
          │                 │             │
          │                 │             └─ one panel per line, with the shared
          │                 │                art direction and cast injected
          │                 └─ sets each shot's real length
          └─ splits the copy, extracts the cast, tags framing and paragraph ends
```

Every stage writes to `output/<draft-name>/`, so a failure at any step can be picked up with `--resume` without paying for the assets that already succeeded.

---

## The editing decisions it makes for you

This is what separates it from a batch image slideshow.

| | Behaviour |
| --- | --- |
| **Consistent art direction** | One style prompt for the whole video: seven presets (see `styles.json`, editable and extensible), or write your own |
| **Consistent cast** | The storyboard step extracts a cast shared by the whole video and injects each description verbatim into every prompt, so the protagonist does not change face every few seconds |
| **Varied framing** | Wide / medium / close chosen per line — wide to open a section and establish a place, close for a feeling, a turn or a conclusion |
| **One image, one whole shot** | `01.png` runs to its end and `02.png` follows; an image is never cut into two segments |
| **Constant camera speed** | Five camera moves cycle per scene, and how far each travels is derived from the shot's length, so a 1.6s shot and a 6.2s shot move at the same perceived speed; a pan stays inside the headroom the scale provides and never exposes the frame edge |
| **Room to breathe** | A beat of BGM only after each paragraph (the outgoing picture holds through it), and the last picture held for 1.8s after the final word |
| **Ducked music** | 0.10 under speech, lifted to 0.20 in the gaps — and not lifted at all where the gap is too short, so it never pumps between sentences |
| **Stable caption baseline** | A wrapped caption is raised by half a line so its bottom line stays put, instead of the block jumping as the line count changes |
| **Unified colour** | One filter across the whole video, pulling independently generated panels into the same look |
| **Hard cuts throughout** | No transitions, no intro animations; all the motion comes from the camera move |

---

## Requirements

- Windows 10/11
- Python 3.10+
- Jianying Pro
- A Volcengine Ark Agent Plan API key (shared by the storyboard, image and speech models; under an Agent Plan subscription, image generation costs nothing extra)

## Local assets

The repository ships no personal or potentially licensed media. Add your own under `assets/`:

```text
assets/
  opening_dong.mp3       # required: opening sound effect
  background_music.mp3   # optional: BGM
  watermark.png          # optional: watermark
```

Point `.env` at relative or absolute paths. Leave BGM and watermark empty to skip them; the opening sound effect is currently required.

---

## Where the art direction lives

There are **7 built-in presets**, all kept in **`styles.json`** (repository root) — no code changes needed:

| Preset | Look |
| --- | --- |
| `story` (default) | Emotional-story manhua: thick even ink lines, flat low-saturation colour, rosy cheeks, sparse backgrounds — the easiest to keep consistent |
| `webtoon` | Korean-style realistic webtoon: thinner lines, softer rendering, restrained colour |
| `cinematic` | Saturated deep navy with high-contrast cinematic lighting (the old default) |
| `ghibli` | Hand-painted Ghibli-style animation: floating islands above cloud seas, watercolour textures, warm golden light |
| `noir` | Dark cinematic photoreal: red/teal neon, smoke and rain, high-contrast chiaroscuro, lonely big-city mood |
| `fantasy` | Magical-realism night: giant banyan and lanterns, fireflies, deep-blue mist, Chinese folk-tale atmosphere |
| `documentary` | Photojournalism: misty harbour dawn, natural light, 35mm film grain |

Four entry points, ordered from the smallest change to the largest:

| What you want | Where |
| --- | --- |
| Switch to another preset | `IMAGE_STYLE_PRESET` in `.env` — the value is any key in [styles.json](styles.json) |
| Tweak a preset or add your own | Edit [styles.json](styles.json): change an existing entry under `presets`, or add a new key and set `IMAGE_STYLE_PRESET` to it; change `"default"` and even `.env` can stay untouched |
| Keep the file elsewhere / rotate several palettes | `IMAGE_STYLES_FILE` in `.env` points at any JSON file (relative paths resolve against the repo root) |
| One-off complete description, without touching files | `IMAGE_STYLE_PROMPT` in `.env` overrides the preset entirely (English descriptions work best) |

Notes:

- If `styles.json` is missing it is created from the built-ins on first run; a broken file (invalid JSON, `"default"` pointing at an unknown key) **fails with an error locating the problem** instead of silently falling back
- A copy of the built-ins stays in the code (the `STYLE_PRESETS` dict at [`animated_caption_draft.py`](animated_caption_draft.py) line 55); presets of the same name in `styles.json` override it — **upgrading the code will not wipe your edits**
- `ARK_IMAGE_SEED` ([`.env.example`](.env.example) line 22): a fixed seed keeps the look stable and reruns reproducible
- `COLOR_GRADE`: one filter across the whole video. The final look comes from the **style prompt plus the filter**; set to `none` to disable
- The style prompt is appended after each scene description by `compose_image_prompt()` ([`animated_caption_draft.py`](animated_caption_draft.py) line 726) — when the pictures go wrong, run `--plan-only` first: if the scene description itself went astray, changing the style will not help
- `--check-config` prints which style is in effect and where it came from (which file / `.env` / default)

---

## Usage

From a UTF-8 text file:

```powershell
python animated_caption_draft.py --draft-name my_story --title "Example title" --input copy.txt
```

Short copy inline:

```powershell
python animated_caption_draft.py --draft-name demo --title "Example title" --text "Copy goes here."
```

Inspect the storyboard without generating media (this still calls the text model):

```powershell
python animated_caption_draft.py --draft-name preview --input copy.txt --plan-only
```

The output shows how each line was split, its framing, which lines end a paragraph, and the cast that was extracted. **Start here when the pictures are wrong** — the style prompt is appended after the scene description, so if the description itself went astray, changing the preset will not help.

| Flag | Purpose |
| --- | --- |
| `--input` / `--text` | Where the copy comes from; pick one |
| `--draft-name` | Draft name in Jianying; defaults to a timestamp |
| `--title` | Title card; defaults to the first line of the copy |
| `--resume DRAFT_NAME` | Continue a run, reusing assets that already succeeded |
| `--replace` | Allow overwriting an existing draft (**deletes the whole draft folder**) |
| `--check-config` | Validate configuration and assets without calling any API |
| `--plan-only` | Generate and print the storyboard only |
| `--verbose` | Print a full traceback on failure |

---

## Timeline layout

```text
0s     lead                          paragraph beat              last line  hold
|------|-------|-------|-------|~~~~~~~|-------|  ...  |-------|~~~~~~~~~~|
 SFX+title  line 1  line 2  line 3          line 4              last line

[visuals]  one image per line, running whole; the outgoing picture covers the
           beats and the ending hold, so a pause never shows as black
[narration] one segment per line, separated only by the paragraph beats
[captions] aligned to the narration, with an intro animation; silent in the beats
[SFX]      once, at the open
[BGM]      0.10 under speech, 0.20 in the gaps; looped, faded at both ends
[grade]    one filter across the whole video
```

Beats appear only after lines the storyboard model marks as **ending a paragraph** — eight paragraphs give you seven places to breathe, not a stop after every sentence.

---

## Configuration

Every setting is documented inline in [.env.example](.env.example). The ones you will actually touch:

| Variable | Default | Meaning |
| --- | --- | --- |
| `ARK_API_KEY` | empty | Ark Agent Plan key, shared by all three models |
| `ARK_TTS_VOICE_TYPE` | sample voice | Voice ID |
| `JIAN_YING_DRAFT_DIR` | empty | Local Jianying draft root |
| `IMAGE_STYLE_PRESET` | `story` | Whole-video art direction; see [styles.json](styles.json) |
| `IMAGE_STYLES_FILE` | empty | Path to the styles JSON; empty uses `styles.json` in the repo root |
| `SCENE_CHARACTERS_PER_IMAGE` | `22` | Chinese characters per shot, floor of 8. **The only pacing control**: lower means faster cuts, more images, higher cost |
| `KEN_BURNS_RATE` | `0.035` | Camera speed as a fraction of frame per second |
| `PARAGRAPH_PAUSE_SECONDS` | `0.5` | Beat inserted after a paragraph |
| `ENDING_HOLD_SECONDS` | `1.8` | How long the last picture holds |
| `BGM_VOLUME` / `BGM_LIFT_VOLUME` | `0.10` / `0.20` | Music level under speech / in the gaps |
| `COLOR_GRADE` | `灰调中性` | Whole-video filter; `none` disables |
| `NARRATION_SUBTITLE_Y` | `-700` | Caption position; see below |
| `TITLE_STYLE` | `paper` | Title colours: `paper` / `red` / `white` / `gold` |
| `IMAGE_CONCURRENCY` | `3` | Image workers; lower it on HTTP 429 |

### Layout coordinate units

`NARRATION_SUBTITLE_Y` and `TITLE_Y` are **pixels against a 1920-tall reference frame**, independent of the actual canvas height (positive is up):

```text
transform_y = value / 960          Jianying's visible range is -1 to 1
-700  ->  -0.729   about 146 px above the bottom edge of a 1080-tall canvas
+520  ->  +0.542   upper third, where the title sits
```

Out-of-range values are clamped into the safe area. `--check-config` prints where each layer actually lands, and the **source** of each key setting (`.env`, process environment, or default) — a stale shell variable silently shadowing `.env` is otherwise very hard to spot.

---

## Resuming

Every run keeps its full state under:

```text
output/<draft-name>/
  manifest.json    scenes, cast, asset paths
  failures.json    what went wrong
  run.log          per-event log
  audio/           01.mp3, 02.mp3 ...
  images/          01.png, 02.png ...
```

Assets that already succeeded are reused; only what is missing is retried:

```powershell
python animated_caption_draft.py --resume my_story
```

> **Overwriting an existing draft requires `--replace`, including with `--resume`.** Overwriting deletes the whole draft folder, so any edits you made in Jianying go with it. Close the draft in Jianying first.

---

## Development

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
```

The suite covers every piece of logic that does not call a paid API:

- Layout coordinate conversion and safe-area clamping
- Camera rate normalisation, upscale ceiling, and pans that never expose the edge
- Timeline layout: gapless shots, beat placement, ending hold
- BGM looping, head/tail fades, and the ducking envelope (including "do not lift when the gap is too short")
- Caption wrap estimation and baseline compensation
- Storyboard JSON tolerance, framing and paragraph-mark parsing
- One integration test that builds a real draft from synthetic media and asserts against the parsed `draft_content.json`

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| HTTP 429 | Lower `IMAGE_CONCURRENCY`. Errors carry the API's response body, so rate limiting and an empty balance are distinguishable |
| SSL EOF / dropped connection | Retry later with `--resume`; drop to one worker if needed |
| Opening sound effect not found | Check `OPENING_SOUND_PATH` points at an existing MP3 or WAV |
| Jianying folder not found | Check `JIAN_YING_DRAFT_DIR` is the Jianying Pro draft root |
| Caption sits wrong | Adjust `NARRATION_SUBTITLE_Y`, confirming with `--check-config` |
| Wrapped captions drift | Calibrate `SUBTITLE_EM_PX`; it is the only number feeding the wrap estimate |
| `.env` edits do nothing | A process environment variable takes priority; `--check-config` shows the source |
| Wrong art style | Run `--plan-only` first — the style prompt is appended after the scene description |

---

## Privacy and release safety

`.gitignore` excludes `.env`, everything generated under `output/`, personal media under `assets/`, and Python and editor state.

Scan for secrets before publishing anyway. If a key was ever committed or sent somewhere untrusted, revoke and reissue it in the console immediately.

## Current limits

- Roughly 1800 non-whitespace characters of copy per run
- The canvas is fixed at landscape 1920×1080
- Stills only; there is no image-to-video stage
- Output targets the Jianying Pro draft format; other editors are not guaranteed

## Licence

[MIT](LICENSE). Free to use, modify and sell, as long as the copyright notice travels with it.

Note that this covers the code only. Sound effects, music and watermarks you place in `assets/`, and the model services the tool calls, carry their own terms.

## Dependencies

Drafts are written with [pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft).

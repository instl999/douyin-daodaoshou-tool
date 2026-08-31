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
| **Consistent art direction** | One style prompt for the whole video: nine presets (see `styles.json`, editable and extensible), or write your own |
| **A style brings its own fittings** | Switching style also switches what the storyboard director is told it is drawing, the whole-video filter, and the title colourway — no more picking film noir and getting a director who still writes flat comic panels |
| **Measured title typography** | The title block is centred, broken onto two or three lines and coloured line by line, at the size and line pitch measured off the reference video; the break point is chosen by Chinese line-breaking rules rather than left to Jianying's auto-wrap |
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

## The nine styles

All of them live in **`styles.json`** at the repo root, so changing the look never means touching code.

| Preset | Look | Filter / title colourway |
| --- | --- | --- |
| `midnight` (default) | **White line on midnight blue**: white contour lines and engraving hatching on flat navy, with one or two things allowed a saturated spot colour. This is the reference video's look | 深蓝电影感 / crimson |
| `manhua` | **Chinese webtoon**: fine ink lines, flat fills, soft daylight, low-saturation warm palette. The faces act, so it carries feeling and dialogue best | 灰调中性 / paper |
| `ink` | **Ink and wash**: wet brush on rice paper, vast empty space, a single vermilion accent | 水墨意境 / ink |
| `papercut` | **Cut-paper collage**: layered paper with real drop shadows, five paper colours, no lines and no rendering — shape and shadow only | 牛皮纸 / paper |
| `riso` | **Two-colour risograph**: fluorescent orange over deep teal, coarse halftone, deliberate misregistration, paper speckle | 复古工业 / poster |
| `gouache` | **Painted storybook**: opaque gouache, visible brush strokes, warm faded palette, long afternoon shadows. The softest of the set | 奶油 / paper |
| `noir` | **Film noir**: black-and-white photography, one hard side key, blind shadows, rain and smoke, silver grain | 高清黑白 / white |
| `neon` | **Neon night city**: photoreal rain-soaked city, magenta and cyan signage, wet reflections, heavy bokeh. The exact opposite of noir | 赛博朋克 / electric |
| `notebook` | **Marker sketch**: black felt-tip line work on off-white paper, one yellow highlighter swipe, sparse red-pen annotation | 高清明亮 / marker |

`midnight` is the default because it is both the reference video's look and the most stable of the
nine: every panel is the same white line on the same navy, so images generated hours apart still cut
together.

### A style is more than a prompt

Each preset carries five fields, and four of them exist because they have to move with the look:

| Field | What it does |
| --- | --- |
| `prompt` | The art direction appended to every scene description |
| `medium` | What the picture *is*. Goes to the storyboard director and into every image prompt — this sentence used to be hard-coded to "social-realism manhua", so picking `noir` still got a director briefing flat comic panels |
| `avoid` | What that medium must never collapse into, worded to match |
| `grade` | The matching Jianying filter. A neutral grey pass flattens the navy out of `midnight` and does nothing at all for `noir` |
| `title` | The matching title colourway |
| `label` | A Chinese short name, used only in `--check-config` output |

`grade` and `title` are defaults: `COLOR_GRADE` / `TITLE_STYLE` in `.env` win once they are set.

### How to change it

Smallest change first:

| What you want | Where |
| --- | --- |
| Switch to another preset | `IMAGE_STYLE_PRESET` in `.env` — the value is any key in [styles.json](styles.json) |
| Tweak a preset or add your own | Edit [styles.json](styles.json): change an existing entry under `presets`, or add a new key and set `IMAGE_STYLE_PRESET` to it; change `"default"` and even `.env` can stay untouched |
| Keep the file elsewhere / rotate several palettes | `IMAGE_STYLES_FILE` in `.env` points at any JSON file (relative paths resolve against the repo root) |
| One-off complete description, without touching files | `IMAGE_STYLE_PROMPT` in `.env` overrides the preset's `prompt` (its other four fields still apply) |

Notes:

- A value under `presets` may be the full object or just a prompt string. A string replaces only
  `prompt`; `medium` / `avoid` / `grade` / `title` still come from the built-in of the same name, so
  rewording one preset never silently drops its filter and title colours
- A missing `styles.json` is generated from the built-ins on first run. A broken one (invalid JSON,
  a `"default"` pointing at a key that does not exist, a preset with no `prompt`) **fails loudly with
  the location**, never falls back silently
- The built-ins also live in code (`STYLE_PRESETS` in
  [`animated_caption_draft.py`](animated_caption_draft.py)); same-named presets in `styles.json`
  override them, so **upgrading the code will not overwrite your edits**
- `ARK_IMAGE_SEED` in [.env.example](.env.example) fixes the seed for a steadier look and reproducible reruns
- The style prompt is appended after each scene description by `compose_image_prompt()` — if the
  pictures are wrong, run `--plan-only` first and check the descriptions themselves; changing the
  style will not fix a bad storyboard
- `--check-config` prints the active style, its `medium`, where it was loaded from, and the resolved
  filter and title settings

---

## Title and caption typography

These numbers were measured frame by frame off the reference video (1920x1080), not estimated:

| | Reference video | Default here |
| --- | --- | --- |
| Caption size | ~66 px | `NARRATION_SUBTITLE_SIZE=6.8` → ~67 px |
| Caption position | block centre at 86% of frame height | `NARRATION_SUBTITLE_Y=-700` → 86.5% |
| Caption plate | translucent dark plate, ~1.5 line heights | `SUBTITLE_STYLE=plate` |
| Title size | ~191 px (18% of frame height) | `TITLE_SIZE=19.5` → ~191 px |
| Title position | block centred on the frame | `TITLE_Y=0` |
| Title line pitch | 182 px (0.95 em) | `TITLE_LINE_PITCH=0.95` |
| Title duration | ~2.4 s | `TITLE_SECONDS=2.4` |

Two things work differently from before and are worth spelling out.

**The title is one text layer per line.** The reference title ramps from warm white to crimson, and
Jianying cannot colour part of a text segment, so each line becomes its own segment on its own text
track: the first line takes the primary colour, the rest take the accent. That also puts the line
pitch under our control — 0.95 em is tighter than any text default, and without it the two halves
stop reading as one block.

**The break point is chosen here, not by auto-wrap.** Where a title breaks decides the shape of the
whole opening frame, and auto-wrap picks it purely on width. Two Chinese line-breaking rules are
applied instead: a line may not end on a character that binds to the word after it (coverbs,
negations, numerals, modals), and the next may not start with one that binds to the word before it
(的, 了, 着, 吗 …). The same title:

```text
auto-wrap (width only)     this tool
男人不能为女               男人不能为女人
人做的3件事                做的3件事
```

Write a newline into `--title` to set the break yourself; it is used verbatim. A title too long for
three lines shrinks the type instead of letting Jianying wrap a fourth line, which would otherwise
fall outside the pitch computed above.

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
[title]    first 2.4s, one text track per line (title_overlay, title_overlay_2, ...),
           coloured line by line
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
| `IMAGE_STYLE_PRESET` | `midnight` | Whole-video art direction, one of nine; see [styles.json](styles.json) |
| `IMAGE_STYLES_FILE` | empty | Path to the styles JSON; empty uses `styles.json` in the repo root |
| `SCENE_CHARACTERS_PER_IMAGE` | `22` | Chinese characters per shot, floor of 8. **The only pacing control**: lower means faster cuts, more images, higher cost |
| `KEN_BURNS_RATE` | `0.035` | Camera speed as a fraction of frame per second |
| `PARAGRAPH_PAUSE_SECONDS` | `0.5` | Beat inserted after a paragraph |
| `ENDING_HOLD_SECONDS` | `1.8` | How long the last picture holds |
| `BGM_VOLUME` / `BGM_LIFT_VOLUME` | `0.10` / `0.20` | Music level under speech / in the gaps |
| `COLOR_GRADE` | empty = follows the style | Whole-video filter; `none` disables |
| `NARRATION_SUBTITLE_Y` | `-700` | Caption position; see below |
| `NARRATION_SUBTITLE_SIZE` | `6.8` | Caption size, about 67 px |
| `SUBTITLE_STYLE` | `plate` | `plate` white on a dark plate / `outline` white with a stroke / `box` the old style |
| `SUBTITLE_FONT` | `SourceHanSansCN_Medium` | Caption font; any Jianying font name |
| `TITLE_STYLE` | empty = follows the style | `crimson` / `paper` / `ink` / `white` / `gold` / `poster` / `electric` / `marker` |
| `TITLE_FONT` | `优设标题黑` | Title font |
| `TITLE_SIZE` / `TITLE_Y` | `19.5` / `0` | Title size and position; defaults match the reference video |
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
- Title line breaking: the reference title's real break point, the particle rules, digit runs kept whole, explicit newlines, the line ceiling
- The stacked title: block centring, line pitch, per-line colour, and shrinking rather than wrapping when it is too long
- Style completeness: nine presets each carrying `medium` / `avoid` / `grade` / `title`, and the backward-compatible string form
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
| Wrong title or caption font | Jianying falls back to a default when the font is not downloaded. Change `TITLE_FONT` / `SUBTITLE_FONT`, or use that font once inside Jianying first |
| Title breaks in an odd place | Write the newline yourself in `--title`; it is used verbatim |
| Title covers the subject | Raise it with a positive `TITLE_Y` (`+300` is roughly the upper third) |

---

## Privacy and release safety

`.gitignore` excludes `.env`, everything generated under `output/`, personal media under `assets/`, and Python and editor state.

Scan for secrets before publishing anyway. If a key was ever committed or sent somewhere untrusted, revoke and reissue it in the console immediately.

## Current limits

- Roughly 1800 non-whitespace characters of copy per run
- The canvas is fixed at landscape 1920×1080
- The title is capped at three lines; font availability depends on what Jianying has downloaded locally
- Stills only; there is no image-to-video stage
- Output targets the Jianying Pro draft format; other editors are not guaranteed

## Licence

[MIT](LICENSE). Free to use, modify and sell, as long as the copyright notice travels with it.

Note that this covers the code only. Sound effects, music and watermarks you place in `assets/`, and the model services the tool calls, carry their own terms.

## Dependencies

Drafts are written with [pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft).

# Douyin "Xinli Daodaoshou" Same-Style Video Maker · Jianying Draft Generator

Recreates the video format of the Douyin blogger "Xinli Daodaoshou" ([creator page](https://v.douyin.com/AYhnYiaH0uo/)): it turns a piece of Chinese copy into an editable Jianying (CapCut China) draft — an AI storyboard, per-line voice-over, one panel per line, then a timeline with visuals, narration, captions, a title, sound effects, BGM and a colour grade already laid out.

> **Ships with Volcengine Ark Agent Plan API support** — storyboard, image
> generation and voice-over share one `ARK_API_KEY`, all routed through the
> Agent Plan API. **Image generation is still billed on Agent Plan**, although
> it is usually cheaper than standard pay-as-you-go calls. Check the current
> price and your actual bill in the Volcengine Ark console.

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

Fill in two things in `.env` — `ARK_API_KEY` and `ARK_TTS_VOICE_TYPE` — plus
`DEEPSEEK_API_KEY` if you have one, which makes the storyboard much faster. The
draft folder is found for you, so the draft lands in Jianying with nothing to
copy by hand; set `JIAN_YING_DRAFT_DIR` only if that lookup fails. Then:

```powershell
python animated_caption_draft.py --check-config
```

Every problem is listed at once (no drafts folder, no key, no voice… numbered), rather than one per run. If it passes, make a video:

```powershell
python animated_caption_draft.py --draft-name my_story --title "Example title" --input copy.txt
```

Open Jianying and `my_story` is waiting in the draft list. The run ends on a summary: scenes, paragraphs and length; how many lines were read and frames drawn this run, how many were reused and roughly what they cost; the music chosen, and the style and title colouring used — written to `run.log` as well.

---

## Recommended: run it directly from Codex or Claude Code

This repository is designed to be driven directly by a coding agent. Open the
repository in Codex or Claude Code and describe the copy, title, style and draft
name in plain language; no extra wrapper or workflow is required. Ask the agent
to read this README first, report the expected shot count before any billed API
work, and use `--resume` after a failure so completed assets are not generated
and billed again.

Copy-paste examples:

**Set up and run only the free checks**

```text
Read README.en.md and .env.example, install the dependencies, and run --check-config. Do not call any paid API. List the missing keys, voice setting, or local assets so I can fill them in myself.
```

**Review the storyboard before generating images**

```text
Read copy.txt and prepare a midnight-style storyboard for “Why being busy makes us anxious”, with the draft name anxiety_story. Run --plan-only, review the subject, framing, and character consistency of every shot, and tell me how many images the full run would generate. Do not generate media yet. Once I approve, make it with --resume anxiety_story from that storyboard instead of planning again.
```

**Build an editable Jianying draft**

```text
Use copy.txt with the title “Busy Is Not What Breaks You” and create a Jianying draft named anxiety_story. Check the configuration and report the expected shot count first. When it finishes, inspect the output and log. If a stage fails, resume it instead of paying to regenerate completed assets.
```

**Change the look or repair a shot**

```text
Inspect the existing output/anxiety_story run. Switch it to the noir preset (which redraws every frame), or redraw only the shots with unsuitable artwork using --redo. Reuse narration and successful assets wherever possible, and tell me how many frames will be redrawn before spending anything.
```

> Cost note: `--check-config` makes no API calls; `--plan-only` still calls the
> text model; a full run uses text, TTS and image APIs. Agent Plan is not a free
> image allowance, so check current pricing and balance in the Ark console.
> Once the storyboard exists, and before any narration or picture is paid for,
> the run prints the **exact** number of frames it will draw and reuse (with a
> price when `ARK_IMAGE_CNY_PER_IMAGE` is set); `--plan-only` ends on the same
> line. For unattended runs, set `MAX_IMAGES`: over it, the run stops before
> spending, with the storyboard saved — raise the cap or trim the storyboard,
> then `--resume`.

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
| **One subject per frame** | Each shot names a drawable subject first, then composes around it: subject largest and sharpest, support visibly subordinate, a third of the frame left quiet. Still readable an inch wide |
| **Framing sets the budget** | 4 supporting elements wide, 2 medium, 1 close — the same cap given to the director and to the image model |
| **The subject is not always a person** | A place, an object and a person rank equally; a sentence about feeling is often carried by the room rather than by a face, and every batch must include frames whose subject is a place or an object |
| **Rooms somebody lives in** | Every frame is set somewhere specific and furnished, with the two or three props that say whose room it is and what time it is, and one light source |
| **Composed for a general audience** | At most one symbolic element per frame and it has to live in the room — a silhouette in a doorway reads, a row of floating icons does not; no collages or split screens |
| **Consistent art direction** | One style prompt for the whole video: nine presets (see `styles.json`, editable and extensible), or write your own |
| **A style brings its own fittings** | Switching style also switches what the storyboard director is told it is drawing, the whole-video filter, and the title colourway — no more picking film noir and getting a director who still writes flat comic panels |
| **Measured title typography** | The title block is centred and broken onto two or three lines, its colour running character by character from warm white into crimson, at the size and line pitch measured off the reference video; the break point is chosen by Chinese line-breaking rules rather than left to Jianying's auto-wrap |
| **Consistent cast** | The storyboard step extracts a cast shared by the whole video and injects each description verbatim into every prompt, so the protagonist does not change face every few seconds |
| **An anchor frame (optional)** | `IMAGE_REFERENCE=anchor`: one frame is drawn first and every other frame is drawn with it as a reference, so faces and palette are held by a picture, not only by words; experimental, off by default |
| **Varied framing** | Wide / medium / close chosen per line — wide to open a section and establish a place, close for a feeling, a turn or a conclusion |
| **One image, one whole shot** | Picture 1 runs to its end and picture 2 follows; an image is never cut into two segments |
| **The camera follows the shot** | A close-up pushes in; a medium shot pushes in with a drift; a wide shot pulls out or pans so the place reveals itself; the last shot of a paragraph (and of the video) pulls out, stepping back into the beat that follows. Each framing takes its moves in turn and no two shots in a row move the same way. The moves used to rotate in order, so a pull-out could land on the close-up a paragraph builds to |
| **Constant camera speed** | How far each move travels is derived from the shot's length, so a 1.6s shot and a 6.2s shot move at the same perceived speed; a pan stays inside the headroom the scale provides and never exposes the frame edge |
| **Room to breathe** | A beat of BGM only after each paragraph (the outgoing picture holds through it); the video ends on the last subtitle, with no dead air after it |
| **Music matched to the copy** | One bed picked from the library by the mood label on each filename, matched to how the director read the script; nothing found means no music |
| **Ducked music** | 0.056 under speech, lifted to 0.10 in the gaps — and not lifted at all where the gap is too short, so it never pumps between sentences |
| **Stable caption baseline** | A wrapped caption is raised by half a line so its bottom line stays put, instead of the block jumping as the line count changes |
| **Unified colour** | One filter across the whole video, pulling independently generated panels into the same look |
| **Hard cuts throughout** | No transitions, no intro animations; all the motion comes from the camera move |

---

## Requirements

- Windows 10/11
- Python 3.10+
- Jianying Pro
- A Volcengine Ark Agent Plan API key (shared by the storyboard, image and speech models; image generation is billed, usually at a lower cost, with actual charges shown in the Ark console)

## Local assets

The repository ships no personal or potentially licensed media. Add your own under `assets/`:

```text
assets/
  opening_dong.mp3       # optional: one is synthesised if absent
  background_music.mp3   # optional: BGM
  watermark.png          # optional: watermark
```

Point `.env` at relative or absolute paths. Leave BGM and watermark empty to skip them.

**The opening cue ships, and is never synthesised.** `assets/opening_dong.mp3` is the 咚 you hear in the finished video, played exactly as supplied at unity gain. There is no fallback that builds a stand-in: this sound is what these videos are recognised by, and something that merely resembles it is worse than an error. Point `OPENING_SOUND_PATH` at your own file to change it; a path that does not exist fails loudly rather than being quietly replaced.

---

## The nine styles

All of them live in **`styles.json`** at the repo root, so changing the look never means touching code.

| Preset | Look | Filter / title colourway |
| --- | --- | --- |
| `midnight` (default) | **Painted, cool navy, one warm lamp**: a fully painted comic scene in black ink outlines and flat fills, a cool navy palette lit by one warm source, rooms furnished to the frame edges. This is the reference video's look | 深蓝电影感 / crimson |
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
  [`daodaoshou/styles.py`](daodaoshou/styles.py)); same-named presets in `styles.json`
  override them, so **upgrading the code will not overwrite your edits**
- `ARK_IMAGE_SEED` in [.env.example](.env.example) fixes the seed for a steadier look and reproducible reruns
- The style prompt is appended after each scene description by `compose_image_prompt()` — if the
  pictures are wrong, run `--plan-only` first and check the descriptions themselves; changing the
  style will not fix a bad storyboard
- `--check-config` prints the active style, its `medium`, where it was loaded from, and the resolved
  filter and title settings

### Matching every frame to one (experimental, off by default)

`IMAGE_REFERENCE=anchor` draws one frame first, on its own — the first shot to show the recurring
cast, or the first shot if there is none — and sends every other frame with a copy of it, shrunk to a
1280-wide JPEG, as a reference image (the images API's `image` field). The prompt says what the
reference is for: **only the drawing style, the palette and the recurring people's faces, hair and
clothes — not its composition, framing, subject or setting**. Without that line a reference reads as
"draw this again", and every panel comes back as the same room.

Why it is worth trying: by default the cast and the look are held together by words alone — the same
character description and style prompt in every request — with a light grade over whatever drift gets
through. A picture holds a face and a palette far better than a sentence does.

Why it is off: it is only as good as the model's and the plan's support for reference images, and it
has not been measured against the reference video the way everything else here has. An endpoint that
rejects it fails with a message naming this setting (a rejected request is not billed); set it back to
`off` and `--resume`. Check the console for whether a reference changes the price. It applies to
frames drawn from then on — frames already drawn are not redrawn because it changed.

---

## What the frame is of

One picture, one subject, everything else in support. Neither end used to say
so, and the image prompt said the **opposite** in as many words:

```text
do not visually overemphasize one incidental detail
```

That was written against a real failure - seizing on a passing noun and
drawing that - but read as written it forbids emphasising anything, and that
is what came back. The person, the table, the window and the clock on the wall
all drawn at one size, one line weight and one level of detail, with nothing
for the eye to land on. On a phone that reads as texture, not as a picture.

**The director now names the subject before it writes the description.** Each
scene carries a `subject`: two to five English words for something that can be
drawn.

```json
{"text": "她把手机扣在桌上", "subject": "a woman placing a phone face-down",
 "image_prompt": "...", "shot_size": "close"}
```

Never an abstraction (`pressure`, `regret`), never a whole scene, never two
things joined by `and`. **A separate field is the point**: a field holds one
answer where a description can quietly hold four, which is exactly how "one
image per sentence" turned into "everything in the sentence, in one image".
`--plan-only` prints it, so it is the first thing to read when a frame is
wrong.

**The image prompt states a hierarchy** instead of forbidding one:

| | |
| --- | --- |
| Subject | largest, sharpest, most detailed, most contrast against what is behind it; dead centre or on a thirds intersection |
| Support | visibly subordinate - smaller, flatter, fewer marks, less contrast - and never crowding the subject's outline |
| Ground | at least a third of the frame left quiet. **A frame filled edge to edge has no subject** |
| Thumbnail | the subject stays identifiable an inch wide, carried by silhouette and tone rather than by an outline |
| One picture | never a collage, a split screen, a before-and-after pair, a grid of panels, an inset or a row of icons |

**Framing decides how much may share the frame.** The same number goes to the
director and to the image model, so a brief cannot ask for more than the
picture is allowed to draw:

| Shot | Subject fills | Supporting elements |
| --- | --- | --- |
| `wide` | ~1/3 of frame height | 4 |
| `medium` | ~2/3 | 2 |
| `close` | at least 3/4 | 1 |

**And the reading is chosen for a general audience, not for cleverness.** The
director is told: these are watched on a phone and judged in the first
half-second, so take the reading a general viewer finds legible over the
cleverest one.

**It must not default to a person.** A place, an object and a person are
equally valid subjects, and a sentence about how somebody feels is often better
carried by the room they feel it in - an unmade bed, a cold meal, a door that
stays shut - than by a face. A face is for when the expression *is* the
information. Every batch has to include frames whose subject is a place or an
object.

**And the room has to be furnished.** The director names the room, puts the two
or three ordinary props in it that say whose room it is and what time it is, and
gives it one light source. An empty backdrop reads as a cut-out.

At most one symbolic element per frame, and it has to live in the room: a
silhouette standing in a doorway reads; a row of icons on a blank ground does
not. No diagrams, charts or floating clusters of objects.

### The default style: fully painted, cool navy, one warm lamp

Five of the seven reference frames are **fully painted interiors** — a café, a
living room, a bedroom, a banquet table, a desk. Black ink outlines, flat fills
with soft gradients where light falls, walls and shadow and furniture in a cool
navy palette, and one warm practical source — a lamp, a window, a screen —
laying amber across the subject. **That warm-against-cool contrast is the
look.** Rooms are real and furnished; distant walls may be left as pale line,
but the subject never is.

The previous version read the references as "white line room, painted subject",
which is true of two of them. Here is what it actually drew:

| | previous | now |
| --- | --- | --- |
| the same "dim apartment stairwell" frame | a line-drawn staircase in a small box, low in the frame, ~65% bare navy | a fully painted stairwell filling the frame: cool blue walls and worn steps, one warm lamp lighting the door |

Three rules were compounding into the emptiness, and all three changed:

- composition's "leave at least a third of the frame empty" → clear space around the subject, but **the frame is a whole scene**
- the style's "large areas of unbroken navy" → gone, and "no flat vector icons, no plain empty background" added
- a wide shot drew its subject at a third of the frame height — right for a person, and it shrank a **place** to an icon. A place now fills the frame

> The change lands in the built-in default *and* in the repository's
> `styles.json`. The file wins at runtime, so editing only the code would have
> changed nothing — there is a test watching for exactly that.

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
| Title duration | ~2.4 s (stretched when the voice runs longer) | `TITLE_SECONDS=2.4` |

### The opening: cue, spoken title, copy

The opening title is read aloud now. It used to be drawn and never spoken —
and a `--title` says something the copy does not, so every video opened on a
silent piece of large type.

```
0.00s  the opening cue lands, the title appears
0.45s  the title's voice, into the cue's decay rather than over its impact
~2.1s  the first line of the copy
```

`OPENING_LEAD_SECONDS` (0.8 s) is now a **floor, not the answer**: the head
holds for as long as the title takes to read, plus a 0.25 s breath. 0.8 s fits
the cue landing on its own and about half a spoken title — left alone, the copy
talks over the title's own voice. The overlay is held to match, so the title
never vanishes mid-sentence.

The title gets no caption of its own: the large type in the middle of the frame
is its caption.

**A title the opening already says is not spoken twice.** It defaults to the
copy's first line, and that line is part of the copy the scenes narrate — so
speaking it as well would say the same sentence twice in a row. The title only
gets its own voice when it says something the copy does not, which is also the
only case where the timeline gets longer.

The check is on **meaning**, across the **first two sentences**, not on an
exact prefix of the first:

- The director rewrites as it splits, so a repeat is rarely a prefix. The title
  `男人不能为女人做的3件事` comes back as `有3件事，男人不要为女人做。` — a
  prefix of nothing, and the same sentence twice to anyone watching.
- Sentence one is usually a hook, and the line the title was taken from lands in
  sentence two. Both count.

The thresholds are deliberately strict. Dropping the voice from a title the copy
never says loses the opening line outright, which is a worse video than the
stutter this prevents — so a near miss is left spoken.

**Speaking the title is on by default** (`SPEAK_TITLE=1`). Setting
`SPEAK_TITLE=0` turns it off, and the timeline returns exactly to what it was
when the title was drawn but never spoken.

Two things work differently from before and are worth spelling out.

**The title is one text layer per line, and its colour runs character by character.** Each line is
its own segment on its own text track, which puts the line pitch under our control — 0.95 em is
tighter than any text default, and without it the two halves stop reading as one block.

The reference title ramps from warm white to crimson. This used to say Jianying cannot colour part of
a text, so the first line took the primary and the rest the accent. That limit is pyJianYingDraft's,
not the draft format's: a text's style is already a list of runs over character ranges, and the
library writes one. Now every character gets its own fill, from the primary at the first character to
the accent at the last, with the font, stroke and shadow unchanged — only the colour moves.

The ramp runs in reading order, but each line keeps the colour it reads as — the first warm white,
the last crimson — and drifts part of the way towards its neighbour. Spread evenly over every
character, compared on the rendered title, it turned the end of the first line pink and took the
punch out of the crimson. Colours are mixed in the perceptual OKLab space, so every character moves
the same visible distance.

`TITLE_COLOR_MODE=lines` brings back one colour per line. The `poster`, `marker` and `ink` colourways
default to it: a risograph's two drums, a marker and a red pen, ink and a seal never blend on the real
thing.

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

Inspect the storyboard without generating media (this still calls the text model). It needs only the text model's key — no Jianying install, voice or opening cue; if `output/<draft-name>/` already holds a storyboard, add `--replace` to plan it afresh:

```powershell
python animated_caption_draft.py --draft-name preview --input copy.txt --plan-only
```

The output shows how each line was split, its framing, which lines end a paragraph, and the cast that was extracted. **Start here when the pictures are wrong** — the style prompt is appended after the scene description, so if the description itself went astray, changing the preset will not help.

### Review the storyboard, edit it, then make the video

The storyboard `--plan-only` makes is saved in `output/<draft-name>/manifest.json`. Once you have
reviewed it, carry on with `--resume`, and **the video is made from the storyboard you reviewed** —
nothing is planned again, and the storyboard is not paid for twice:

```powershell
python animated_caption_draft.py --draft-name my_story --input copy.txt --plan-only
# read the output; if needed, edit a scene's text / subject / image_prompt /
# shot_size / pause_after directly in output/my_story/manifest.json
python animated_caption_draft.py --resume my_story
```

Running the command again from scratch instead (without `--resume`) plans a new storyboard — the
model splits the copy differently every time, so what you reviewed would not be what gets made.

The same works after the video is made: edit a scene's `image_prompt` and `--resume`, and only that
frame is redrawn; edit its `text`, and only that line is re-read (see [Resuming](#resuming)).

### Redraw only some frames: `--redo`

When the prompt is fine and a frame simply came out badly, nothing needs editing:

```powershell
python animated_caption_draft.py --resume my_story --redo 3,7 --replace
```

`--redo` takes `3,7` or `3-5`, redraws only those frames and reuses all the narration and every other
frame. Each redraw is a new take: the new frame is a new file and **the previous one stays on disk** —
set `take` back in the manifest to return to it. With `ARK_IMAGE_SEED` set, each take moves the seed on
by one; the same prompt with the same seed only draws the same picture again. As with any resume, a
draft already in Jianying needs `--replace`.

| Flag | Purpose |
| --- | --- |
| `--input` / `--text` | Where the copy comes from; pick one |
| `--draft-name` | Draft name in Jianying; defaults to a timestamp |
| `--title` | Title card; defaults to the first line of the copy |
| `--resume DRAFT_NAME` | Continue a run, reusing assets that already succeeded; also makes a reviewed storyboard |
| `--redo 3,7` | With `--resume`: redraw only these scenes' frames (a new take; the previous one is kept) |
| `--replace` | Allow overwriting an existing draft (**deletes the whole draft folder**) |
| `--check-config` | Validate configuration and assets without calling any API; every problem is listed at once |
| `--plan-only` | Generate and print the storyboard only; needs only the text model's key |
| `--speed X` | Global video speed; overrides `VIDEO_SPEED` in `.env` |
| `--verbose` | Print a full traceback on failure |

---

## Speed

**1.2× by default.** One number for the whole video: `VIDEO_SPEED` in `.env`,
or `--speed` for a single run.

```powershell
python animated_caption_draft.py --draft-name my_story --input copy.txt --speed 1.5
```

1.0 is the baseline. Every duration in `.env`, and every duration constant in
the code, is written at 1.0 and means what it says there.

It is deliberately **not** a voice setting, which is the whole point. Speeding
the narration alone (`ARK_TTS_SPEECH_RATE`) does not produce a faster video: the
voice finishes early over pictures still holding their old length and a camera
move still crawling. That reads as a dubbing error, not as pace.

What 1.2× actually does:

| | at 1.2× |
|---|---|
| narration | **re-read** faster, not resampled, so there is no pitch shift |
| shot lengths | measured from the audio that came back, so they follow on their own |
| captions | cut from the same narration spans, so they cannot drift off it |
| caption intro animation | ÷1.2 |
| title card, opening lead, the wait before the title is read | ÷1.2 |
| paragraph beats, ending hold, the music's duck ramp | ÷1.2 |
| camera move | rate **×1.2** over a shot ÷1.2 — the two cancel, so the push crosses the same ground, just quicker |
| music and the opening cue | **unchanged**. They are cues, not a clock, and the stinger played 1.2× is a different sound |

The rule is one line: **a duration divides by speed, a per-second rate
multiplies by it, and anything measured in pixels does not move.**

The range is 0.5–2.0, which is the speech API's own (`speech_rate` is a
percentage offset in [-50, 100]). Outside it the picture would be cut to a pace
its own narration could not be spoken at — the very mismatch this setting
exists to prevent — so it is refused rather than quietly clamped.

Changing the speed and then using `--resume` **re-reads the narration and keeps
every image**. Narration read at another speed would put the new timeline on
the old audio, giving a video at neither speed while every stage reported
success.

---

## Which model writes the storyboard

Set `DEEPSEEK_API_KEY` and the director uses DeepSeek's own API; leave it empty
and it stays on Ark. **Images and narration always use the Ark Agent Plan.**

Measured on one six-scene script, same brief:

| | thinking off | thinking on |
| --- | --- | --- |
| DeepSeek API `deepseek-chat` | **6s** | — |
| Ark `deepseek-v4-flash` (default) | 15s | 40,000+ characters of reasoning and no storyboard in 240s |
| Ark `doubao-seed-2.0-lite` | 27s | 98s |

**The storyboard request now always turns the model's thinking off.** It used
not to: the model reasoned for minutes, sent nothing while it did, and this
network path drops a connection silent for about 69s — which surfaced as
"Network request failed after 3 attempts", a network error that was really a
model still thinking.

The call also **streams** now, so the connection stays busy while the answer is
written. A stream cut midway is started again, since half a JSON object is not
a storyboard. And if a model reasons without ever answering — some providers
ignore the flag — the error names the model and how much it reasoned, instead
of reporting a JSON parse failure.

---

## Music picked from the copy

Put a folder of music at `assets/bgm/` (or point `BGM_LIBRARY` anywhere else)
with a **Chinese mood label at the start of each filename**, and one bed is
chosen for the video:

```text
紧张Kill Drill - Robert Ruth.mp3
紧张危机Dismantle - Peter Sandberg.mp3
舒缓Keep on the Sunny Side - 岩崎太整.mp3
```

The label is the run of Chinese characters the filename opens with, up to the
first character that is not Chinese — so a Chinese artist name at the END is not
mistaken for one. The labels available:

```text
紧张 tense    危机 crisis     焦虑 anxious   疑问 questioning
疑惑 puzzled  失落 downcast   转机 a turn    升华 uplift
欢乐 joyful   舒缓 calm       平淡 plain     解释 explaining
讲解 walking through          措施 remedies  解决 resolving
```

**The mood comes from the storyboard call that already runs**, as one extra
field on it — not a second request per video. When the model returns none, and
on a `--resume` from a manifest written before this existed, the words in the
copy itself answer instead, so a run never fails for want of a label.

| | how it picks |
| --- | --- |
| more labels win | a copy read as 紧张 + 危机 takes `紧张危机…` over a plain `紧张…` |
| positional labels rank down | `开头` / `结尾` / `后期` / `提出` are written for one stretch of a video; under a whole one they sit below an unprefixed track of the same mood. **Ranked down, not excluded** — a library with nothing else still supplies music |
| no match means no music | the wrong bed is more distracting than none, and this is the one choice in the video nobody reviews |
| ties break by filename | two tracks labelled alike always resolve the same way, so a rebuild or a `--resume` keeps the same music |
| an explicit track wins | `BGM_PATH` is somebody naming a track; the library is not consulted |

The line `BGM: 紧张/危机 -> 紧张危机Dismantle - Peter Sandberg.mp3` is printed at
the end of a run and recorded in `run.log`. `--check-config` reports where the
library is, how many tracks are in it, and how many carry a label.

**The bed sits 20–25 dB under the voice.** `0.056` (-25 dB) under speech,
lifting to `0.10` (-20 dB) in the gaps. The lift used to be 0.20 — 14 dB down,
which is music rather than atmosphere, and the only thing in the mix loud enough
to compete with the line that follows the gap it fills.

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
[title]    first 2.4s at 1.0x, scaled by VIDEO_SPEED; one text track per line
           (title_overlay, title_overlay_2, ...), colour ramping character by character
[SFX]      once, at the open
[BGM]      picked from the library by mood; 0.056 under speech, 0.10 in the
           gaps; looped, faded at both ends
[grade]    one filter across the whole video
```

Beats appear only after lines the storyboard model marks as **ending a paragraph** — eight paragraphs give you seven places to breathe, not a stop after every sentence.

**A paragraph is a blank line in your copy.** The director sees them and is told that a blank line ends a paragraph; when long copy is sent in parts and a part is cut at a blank line, that part's last scene is marked as a paragraph end directly. Every run of newlines used to be collapsed into one before the director saw the copy, so the five paragraphs of the bundled `copy.txt` arrived as a single block and the breaths were a guess. Copy without blank lines is still judged by meaning.

---

## Configuration

Every setting is documented inline in [.env.example](.env.example). The ones you will actually touch:

| Variable | Default | Meaning |
| --- | --- | --- |
| `ARK_API_KEY` | empty | Ark Agent Plan key: images, narration, and the storyboard when no DeepSeek key is set |
| `DEEPSEEK_API_KEY` | empty | Send the storyboard to DeepSeek's own API (fastest); images and narration stay on Ark |
| `DEEPSEEK_MODEL` | `deepseek-chat` | DeepSeek storyboard model |
| `ARK_TEXT_MODEL` | `deepseek-v4-flash` | Storyboard model on Ark when no DeepSeek key is set |
| `ARK_TTS_VOICE_TYPE` | sample voice | Voice ID |
| `JIAN_YING_DRAFT_DIR` | empty = auto | Local Jianying draft root. Found automatically when empty, including a relocated library |
| `IMAGE_STYLE_PRESET` | `midnight` | Whole-video art direction, one of nine; see [styles.json](styles.json) |
| `IMAGE_STYLES_FILE` | empty | Path to the styles JSON; empty uses `styles.json` in the repo root |
| `SCENE_CHARACTERS_PER_IMAGE` | `22` | Chinese characters per shot, floor of 8. **The only pacing control**: lower means faster cuts, more images, higher cost |
| `SPEAK_TITLE` | `1` (**on by default**) | Whether the opening title is read aloud. `0` turns it off and the title is drawn but never spoken |
| `TITLE_LEAD_SECONDS` | `0.45` | How long after the opening cue the title's voice starts (at 1.0×) |
| `VIDEO_SPEED` | `1.2` | **Global speed**, 1.0 is the baseline; narration, picture, camera and captions move together — see above |
| `KEN_BURNS_RATE` | `0.035` | Camera speed as a fraction of frame per second (multiplied by `VIDEO_SPEED`) |
| `PARAGRAPH_PAUSE_SECONDS` | `0.5` | Beat inserted after a paragraph (at 1.0×) |
| `ENDING_HOLD_SECONDS` | `0` | How long the last picture holds after the final word (at 1.0×). Nothing, by default |
| `BGM_PATH` | empty | One named track; set it and the library is not consulted |
| `BGM_LIBRARY` | empty = `assets/bgm` | Folder of music, matched to the copy by the mood label on each filename |
| `BGM_VOLUME` / `BGM_LIFT_VOLUME` | `0.056` / `0.10` | Music level under speech / in the gaps (-25 / -20 dB) |
| `COLOR_GRADE` | empty = follows the style | Whole-video filter; `none` disables |
| `NARRATION_SUBTITLE_Y` | `-700` | Caption position; see below |
| `NARRATION_SUBTITLE_SIZE` | `6.8` | Caption size, about 67 px |
| `SUBTITLE_STYLE` | `plate` | `plate` white on a dark plate / `outline` white with a stroke / `box` the old style |
| `SUBTITLE_FONT` | `SourceHanSansCN_Medium` | Caption font; any Jianying font name |
| `TITLE_STYLE` | empty = follows the style | `crimson` / `paper` / `ink` / `white` / `gold` / `poster` / `electric` / `marker` |
| `TITLE_COLOR_MODE` | empty = follows the colourway | `ramp` runs from the primary to the accent character by character / `lines` first line primary, the rest accent |
| `TITLE_FONT` | `优设标题黑` | Title font |
| `TITLE_SIZE` / `TITLE_Y` | `19.5` / `0` | Title size and position; defaults match the reference video |
| `IMAGE_CONCURRENCY` | `3` | Image workers; lower it on HTTP 429 |
| `ARK_IMAGE_CNY_PER_IMAGE` | empty | Price per image (CNY), used only for the estimate the terminal prints |
| `MAX_IMAGES` | empty = no cap | Most frames one run may draw; over it the run stops before narration and images (the storyboard is kept) |
| `IMAGE_REFERENCE` | `off` | `anchor` draws one frame first and sends it with every other frame as a reference (experimental; see [Matching every frame to one](#matching-every-frame-to-one-experimental-off-by-default)) |

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
  manifest.json    scenes, cast, asset paths, the speed, voice and style used
  failures.json    what went wrong
  run.log          per-event log
  audio/           01_3fa9c2e1d0.mp3, 02_…  (scene number + a digest of its inputs)
  images/          01_b41f09aa2c.png, 02_…
```

Assets that already succeeded are reused; only what is missing is retried:

```powershell
python animated_caption_draft.py --resume my_story
```

**Every file is named after what made it.** The digest after the scene number
covers everything that decides the file's content — for a clip: the text, the
voice, the speech rate and loudness; for a frame: `image_prompt`, `subject`,
the shot size, the cast descriptions, the style, the model, size and seed.
A file is reused only when all of that still matches, so:

| You changed | What happens on the next run |
| --- | --- |
| Nothing (`--resume` after a failure) | Everything that finished is reused; only the rest is made |
| The speed | Narration is re-read at the new speed; every image is kept |
| `ARK_TTS_VOICE_TYPE` | Every line and the title are re-read in the new voice |
| `IMAGE_STYLE_PRESET` / `IMAGE_STYLE_PROMPT` | Every frame is redrawn in the new style (billed); narration is kept |
| One scene's `image_prompt` in `manifest.json` | Only that frame is redrawn |
| One scene's `text` in `manifest.json` | Only that line is re-read; its picture is kept |
| The same `--draft-name` for a fresh run | Only files whose inputs are identical are reused; nothing is taken on its number alone |

Before spending anything the run says what it is making again and why, e.g.
`3 frame(s) will be drawn again: the style changed (midnight -> noir).`

This is what stops a re-run from quietly reusing the wrong file. Files used to
be called `01.mp3` and picked up by that number alone: re-running the same
command after a failure re-splits the copy, and every subtitle ended up over a
clip reading a different sentence; changing the style and resuming kept the old
frames under the new style's grade and title. Both reported success.

A run from an older version (manifest before v9, files named `01.mp3`) is
trusted once when it is resumed, the way it always was, and its files are
renamed to their keys. A crashed run's finished files are picked up too —
every file is written to a temporary name and renamed only when complete, so a
half-written file never sits under a valid name.

> **Overwriting an existing draft requires `--replace`, including with `--resume`.** Overwriting deletes the whole draft folder, so any edits you made in Jianying go with it. Close the draft in Jianying first.

---

## Development

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
```


The code lives in the `daodaoshou/` package, one module per job; `animated_caption_draft.py` is only
the command (it re-exports the package's names, so `import animated_caption_draft` still works):

| Module | What it does |
| --- | --- |
| `config.py` | Every setting, parsed once with every problem reported at once; the global speed |
| `storyboard.py` | The director: splitting the copy, subjects, framing, paragraphs, mood |
| `images.py` / `tts.py` | Pictures (including the anchor frame) / narration |
| `assets.py` | Files named after their inputs, and what a run may reuse |
| `draft.py` | The Jianying draft: timeline, captions, title, camera, music, grade |
| `styles.py` / `layout.py` / `camera.py` | Looks and composition / caption and title type and colour / camera moves |
| `bgm.py` / `title.py` / `jianying.py` | The music library and ducking / the opening title / finding the drafts folder |
| `state.py` / `report.py` / `net.py` / `env.py` | Manifest and log / terminal output / HTTP with retries / `.env` |
| `cli.py` | The command: one run from copy to draft |

The suite covers every piece of logic that does not call a paid API:

- Layout coordinate conversion and safe-area clamping
- Camera rate normalisation, upscale ceiling, and pans that never expose the edge
- Timeline layout: gapless shots, beat placement, ending on the last subtitle
- Music library: label parsing, compound labels winning, positional labels ranked down, no match meaning no music, reproducible ties
- Finding Jianying's drafts: the default location, a relocated library, an explicit setting winning, the error when nothing is found
- The title-echo check: a reworded repeat, one landing in sentence two, a near miss left spoken
- BGM looping, head/tail fades, and the ducking envelope (including "do not lift when the gap is too short")
- Caption wrap estimation and baseline compensation
- Title line breaking: the reference title's real break point, the particle rules, digit runs kept whole, explicit newlines, the line ceiling
- The stacked title: block centring, line pitch, and shrinking rather than wrapping when it is too long
- The anchor frame: drawn first and alone, every other frame sent with a shrunk copy and the note on what it is for, nothing drawn against a failed anchor, and a refusing endpoint naming the setting
- The title ramp: primary at the first character, accent at the last, each line holding its own end, every split run keeping the font, stroke and shadow, and `lines` mode and the two-ink colourways staying flat
- The frame's subject: it reaches the prompt, it falls back when absent, the element budget and subject size track the framing, and neither end may ask for a collage
- What the director is held to: naming a drawable thing, no symbol piles, and the same element budget the picture is drawn to
- The default style paints the subject and draws the room, and the old "single-colour, no filled colour areas" clauses are gone rather than contradicted
- The director: places and objects rank with people, no defaulting to a person, a furnished room is required, and at most one symbol that lives in it
- `styles.json` agrees with the built-in default
- Style completeness: nine presets each carrying `medium` / `avoid` / `grade` / `title`, and the backward-compatible string form
- Storyboard JSON tolerance, framing and paragraph-mark parsing
- One integration test that builds a real draft from synthetic media and asserts against the parsed `draft_content.json`
- Whole runs of the command with the paid services faked: a re-run after a failure never narrates one line under another; a new style, voice or speed redoes exactly what depends on it; an unchanged resume pays for nothing; an older run's files are trusted once and renamed

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| HTTP 429 | Lower `IMAGE_CONCURRENCY`. Errors carry the API's response body, so rate limiting and an empty balance are distinguishable |
| SSL EOF / dropped connection | Retry later with `--resume`; drop to one worker if needed. An image request cut off after the server may already have taken it is **not resent automatically** — a second attempt could bill the same frame twice; `--resume` draws only the missing ones |
| `Network request failed after 3 attempts` at the storyboard | The old symptom of a model still thinking while the connection timed out. Thinking is now off and the call streams; if it persists, set `DEEPSEEK_API_KEY` |
| Opening sound effect not found | Check `OPENING_SOUND_PATH` points at an existing MP3 or WAV |
| Jianying folder not found | Only needed when the lookup fails: 全局设置 → 草稿位置 in Jianying shows the path to put in `JIAN_YING_DRAFT_DIR` |
| No music | `--check-config` prints the library path and how many tracks carry a mood label; a filename must START with one to be matched |
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

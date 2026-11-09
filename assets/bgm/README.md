# BGM library

**The music in this folder is committed with the repository.** Add a track, rename it by the rules below, and `git add` will include it.

The audio itself is copyright of its respective owners — the repository's MIT license covers only the code, not the music here. If you redistribute or use it commercially elsewhere, confirm you hold the relevant license.

## Naming

A filename **starts with a mood label**, followed by the track name, and it will be matched automatically:

```text
tenseKill Drill - Robert Ruth.mp3
tensecrisisDismantle - Peter Sandberg.mp3
calmKeep on the Sunny Side - Taisei Iwasaki.mp3
openingLost IV - AK,Mapps.mp3
```

A label is the **contiguous run of label characters at the start of the filename**, up to the first character that is not part of a label. So a singer name at the end is never mistaken for a label.

Accepted extensions: `.aac .flac .m4a .mp3 .ogg .wav`

## Labels

`tense` `crisis` `anxious` `questioning` `puzzled` `downcast` `a turn` `uplift` `joyful` `calm` `plain` `explaining` `walking through` `remedies` `resolving`

Labels combine: `tensecrisis...` outranks a plain `tense...` for a script read as both.

### Positional labels

`opening` `ending` `late` `proposal`

These are written for one stretch of a video. Only one bed is laid under the whole video, so under the same mood they rank below unprefixed tracks — **ranked down, not excluded**: if it is the only match, it is still used.

## How a track is chosen

| Rule | Detail |
|---|---|
| Where the mood comes from | Ask for one more field during storyboarding/directing, **without an extra request**; if the model does not provide it, fall back to reading the words in the script |
| Closer labels win | `tensecrisis...` beats `tense...` for a script read as both |
| Ties | The earlier filename wins; **a rerun picks the same track** |
| No match | **No BGM.** Wrong music is worse than none, and this is the least-reviewed part of a video |
| Fix a track | Point `BGM_PATH` in `.env` at it; when set, the library is not consulted |

## Keeping the library elsewhere

Point `BGM_LIBRARY` in `.env` at any directory; leave it empty to use this folder.
`python animated_caption_draft.py --check-config` prints the library path, the number of tracks, and how many carry labels.

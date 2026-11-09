# Local assets

Media files are not uploaded to Git. Put your own assets on this machine and configure the paths in `.env`.

Suggested layout:

```text
opening_dong.mp3       # shipped: opening sound (required, never synthesized)
bgm/                   # optional: BGM library, auto-picked by the mood label at the start of each filename
    tenseKill Drill - Robert Ruth.mp3
    calmKeep on the Sunny Side - Taisei Iwasaki.mp3
watermark.png          # optional: watermark
```

BGM matches the **contiguous label text at the start of the filename**, up to the first character that is not part of a label.
To keep the library outside the repository, point `BGM_LIBRARY` in `.env` at it.

Do not commit API keys, personal watermarks, music of unclear licensing, generated assets or editing drafts.

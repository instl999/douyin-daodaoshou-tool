# Local assets / 本地素材

Media files are intentionally excluded from Git. Add your own assets locally and configure their paths in `.env`.

媒体文件不会上传到 Git。请在本机放入自己的素材，并在 `.env` 中配置路径。

Suggested layout / 建议结构：

```text
assets/
  opening_dong.mp3       # shipped / 随仓库提供：开场音效（必需，不自动合成）
  bgm/                   # optional / 可选：BGM 曲库，按文件名开头的中文情绪标签自动挑
    紧张Kill Drill - Robert Ruth.mp3
    舒缓Keep on the Sunny Side - 岩崎太整.mp3
  watermark.png          # optional / 可选：水印
```

Tracks are matched by the Chinese mood label at the START of the filename, up
to the first non-Chinese character. Point `BGM_LIBRARY` elsewhere to keep the
library outside the repository.

BGM 按**文件名开头**那一段连续中文来匹配，到第一个非中文字符为止。
曲库想放在仓库外面，把 `.env` 的 `BGM_LIBRARY` 指过去即可。

Do not commit credentials, personal watermarks, licensed music, generated media, or Jianying drafts.

不要提交 API 密钥、个人水印、授权不明的音乐、生成素材或剪映草稿。

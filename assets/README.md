# Local assets / 本地素材

Media files are intentionally excluded from Git. Add your own assets locally and configure their paths in `.env`.

媒体文件不会上传到 Git。请在本机放入自己的素材，并在 `.env` 中配置路径。

Suggested layout / 建议结构：

```text
assets/
  opening_dong.mp3       # required / 必需：开场音效
  background_music.mp3   # optional / 可选：背景音乐
  watermark.png          # optional / 可选：水印
```

Do not commit credentials, personal watermarks, licensed music, generated media, or Jianying drafts.

不要提交 API 密钥、个人水印、授权不明的音乐、生成素材或剪映草稿。

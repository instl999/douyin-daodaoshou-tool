# 文案转国漫视频剪映草稿生成器

把中文文案自动转换为可继续编辑的剪映专业版草稿：AI 分镜、逐段配音、逐字幕漫画画面、实时字幕、标题、开场音效，以及可选的 BGM、水印和图生视频。

English documentation: [README.en.md](README.en.md)

## 核心能力

- 使用火山方舟 Agent Plan 规划中文分镜、生成静态图片和合成语音。
- 一条完整字幕对应一张图片；画面只服从当前字幕，不强制添加图表、行情面板或财经符号。
- 默认采用写实叙事型国漫画风：醒目的黑色线稿、硬边赛璐璐阴影、较真实的人体比例、强表情与深蓝/暖色高对比配色。
- 静态图片默认 3 路并发生成；单张失败不会丢失其他成功结果，可通过 `--resume` 精确补跑。
- 自动创建剪映草稿并分离视觉、旁白、实时字幕、标题、开场音效、BGM 和水印轨道。
- 默认仅将第 2–6 个 AI 分镜转换为无声动态视频；使用 `--skip-i2v` 可完全关闭图转视频。
- 所有旁白字幕默认位于 `x=0, y=-700`，可在 `.env` 中统一调整。

## 环境要求

- Windows 10/11
- Python 3.10 或更高版本
- 剪映专业版
- 火山方舟 Agent Plan API Key
- 阿里云百炼 API Key（仅启用图生视频时需要）

## 安装

```powershell
git clone <repository-url>
Set-Location .\copy-to-video-draft-generator
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

随后只在本机编辑 `.env`。不要把真实密钥写入 `.env.example`、源代码或说明文档。

## 准备本地素材

仓库不包含个人或可能受版权保护的媒体文件。请按需要自行放入 `assets`：

```text
assets/
  opening_dong.mp3       # 必需：开场音效
  background_music.mp3   # 可选：BGM
  watermark.png          # 可选：水印
```

在 `.env` 中填写相对路径或完整路径。水印和 BGM 可以留空；开场音效目前为必需素材。

## 配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ARK_API_KEY` | 空 | 方舟 Agent Plan 密钥；分镜、生图和 TTS 共用。 |
| `ARK_TEXT_MODEL` | `deepseek-v4-flash` | 分镜规划模型。 |
| `ARK_IMAGE_MODEL` | `doubao-seedream-5.0-lite` | 静态生图模型。 |
| `ARK_TTS_MODEL` | `seed-tts-2.0` | 语音合成模型。 |
| `ARK_TTS_VOICE_TYPE` | 示例音色 | Seed TTS 2.0 音色 ID。 |
| `JIAN_YING_DRAFT_DIR` | 空 | 本机剪映草稿根目录。 |
| `IMAGE_CONCURRENCY` | `3` | 静态生图并发数，允许 1–8。遇到 429 或 SSL 中断时可降至 2 或 1。 |
| `TTS_CONCURRENCY` | `3` | 语音合成并发数。 |
| `SCENE_CHARACTERS_PER_IMAGE` | `22` | 每镜头大致承载的中文字符数，建议 20–28。 |
| `SCENE_LENGTH_MODE` | `density` | `density` 控制成本与密度；`quality` 优先短而清晰的镜头。 |
| `IMAGE_STYLE_PROMPT` | 国漫画风 | 全片统一画风，可自行替换。 |
| `NARRATION_SUBTITLE_Y` | `-700` | 所有旁白字幕的统一 Y 轴位置。 |
| `OPENING_SOUND_PATH` | `assets/opening_dong.mp3` | 开场音效路径。 |
| `BGM_PATH` | 空 | 可选 BGM 路径。 |
| `WATERMARK_PATH` | 空 | 可选水印路径。 |
| `DASHSCOPE_API_KEY` | 空 | 阿里云百炼密钥；仅图生视频需要。 |
| `DASHSCOPE_I2V_MODEL` | `wan2.6-i2v-flash` | 图生视频模型。 |
| `DASHSCOPE_I2V_CNY_PER_SECOND` | `0.155` | 仅用于终端成本估算，不代表实时价格。 |

检查完整配置和素材，不会调用生成 API：

```powershell
python animated_caption_draft.py --check-config
```

只使用静态图片时，可跳过图生视频密钥检查：

```powershell
python animated_caption_draft.py --check-config --skip-i2v
```

## 使用方法

从 UTF-8 文本文件生成：

```powershell
python animated_caption_draft.py `
  --draft-name my_story `
  --title "示例标题" `
  --input copy.txt
```

短文案可直接传入：

```powershell
python animated_caption_draft.py --draft-name demo --title "示例标题" --text "这里是文案。"
```

完全关闭图转视频：

```powershell
python animated_caption_draft.py `
  --draft-name static_demo `
  --title "示例标题" `
  --input copy.txt `
  --skip-i2v
```

只生成并打印分镜方案：

```powershell
python animated_caption_draft.py --draft-name plan_preview --input copy.txt --plan-only
```

`--plan-only` 仍会调用方舟文本接口。

## 图生视频策略

未使用 `--skip-i2v` 时：

| 分镜位置 | 草稿内容 |
| --- | --- |
| 第 1 个分镜 | 静态图片 + 开场动画 |
| 第 2–6 个分镜 | 无声图生视频 + 同段旁白 + 同段字幕 |
| 第 7 个及以后 | 静态图片 + 缩放关键帧 |

这里的编号是 AI 分镜编号，不是原文句子编号。动态视频保持无声，避免与旁白和 BGM 重叠。

## 断点恢复

每次任务保存在：

```text
output/<draft-name>/
  manifest.json
  failures.json
  run.log
  audio/
  images/
  videos/          # 仅启用图生视频时出现
```

成功素材会被复用。恢复静态任务：

```powershell
python animated_caption_draft.py --resume my_story --skip-i2v
```

如果原任务启用了图生视频，恢复时不要添加 `--skip-i2v`。需要覆盖同名剪映草稿时使用 `--replace`，并先在剪映中关闭该草稿。

## 隐私与发布安全

`.gitignore` 已排除：

- `.env` 及本机环境配置
- `output/` 中的生成媒体、日志和任务状态
- `assets/` 中的个人水印、音效和 BGM
- Python 缓存、虚拟环境和编辑器状态

提交前仍建议执行密钥扫描。若密钥曾被提交或发送到不可信位置，应立即撤销并重新创建。

## 常见问题

- **HTTP 429**：降低 `IMAGE_CONCURRENCY`。
- **SSL EOF 或网络中断**：稍后使用 `--resume`；必要时临时降为 1 路。
- **找不到开场音效**：确认 `OPENING_SOUND_PATH` 指向存在的 MP3 或 WAV。
- **找不到剪映目录**：确认 `JIAN_YING_DRAFT_DIR` 是剪映专业版草稿根目录。
- **字幕位置不合适**：修改 `NARRATION_SUBTITLE_Y`；默认值为 `-700`。

## 当前限制

- 单次文案最多约 1800 个非空白字符。
- 图生视频只固定处理第 2–6 个分镜。
- 输出面向剪映专业版草稿格式，不保证兼容其他剪辑软件。
- 使用 [pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft) 写入剪映草稿。

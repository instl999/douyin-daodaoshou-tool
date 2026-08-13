# 文案转国漫视频剪映草稿生成器

把中文文案自动转换为可继续编辑的剪映专业版草稿：AI 分镜、逐段配音、逐字幕漫画画面、实时字幕、关键词高亮、标题、开场音效，以及可选的 BGM、水印和图生视频。

English documentation: [README.en.md](README.en.md)

## 核心能力

- 使用火山方舟 Agent Plan 规划中文分镜、生成静态图片和合成语音。
- 一条完整字幕对应一张图片；画面只服从当前字幕，不强制添加图表、行情面板或财经符号。
- 默认采用写实叙事型国漫画风：醒目的黑色线稿、硬边赛璐璐阴影、较真实的人体比例、强表情与深蓝/暖色高对比配色。
- **角色一致性**：分镜阶段抽取全片共用的角色设定，逐字注入每张图的提示词，避免主角每隔几秒换一张脸。
- **画面节奏**：超过 `MAX_SHOT_SECONDS` 的镜头会自动切成“全景 + 推近特写”两刀，用同一张图翻倍画面节奏而不增加出图成本。
- **运镜**：五种关键帧运镜（推、拉、左移、右移、推+微摇）轮换，起始缩放恒大于 1.0，横移不会露边。
- **关键词高亮**：分镜模型逐句挑出最重要的词，单独放大显示在字幕上方。
- 静态图片默认 3 路并发生成；单张失败不会丢失其他成功结果，可通过 `--resume` 精确补跑。
- 自动创建剪映草稿并分离视觉、旁白、实时字幕、关键词、标题、开场音效、BGM 和水印轨道。
- 图生视频默认自动分布在全片（钩子、中段、落点），而不是集中在开头；使用 `--skip-i2v` 可完全关闭。

## 环境要求

- Windows 10/11
- Python 3.10 或更高版本
- 剪映专业版
- 火山方舟 Agent Plan API Key
- 阿里云百炼 API Key（仅启用图生视频时需要）

## 安装

```powershell
git clone <repository-url>
Set-Location .\copy-to-manhua-jianying-draft
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

完整配置项和说明都在 [.env.example](.env.example) 里，每一项都有注释。最常调的几项：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ARK_API_KEY` | 空 | 方舟 Agent Plan 密钥；分镜、生图和 TTS 共用。 |
| `JIAN_YING_DRAFT_DIR` | 空 | 本机剪映草稿根目录。 |
| `DASHSCOPE_API_KEY` | 空 | 阿里云百炼密钥；仅图生视频需要。 |
| `SCENE_CHARACTERS_PER_IMAGE` | `22` | 每镜头承载的中文字符数，下限 8。调小 = 节奏更快、成本更高。 |
| `MAX_SHOT_SECONDS` | `3` | 单镜头最长秒数，超过则切成两刀。**想加快节奏优先调这个，它不花钱。** |
| `NARRATION_SUBTITLE_Y` | `-700` | 字幕位置，详见下方坐标说明。 |
| `BGM_VOLUME` | `0.10` | BGM 线性增益（约 -20 dB）。 |
| `KEYWORD_HIGHLIGHT` | `1` | 关键词高亮层开关。 |
| `TITLE_STYLE` | `red` | 标题配色：`red` / `white` / `gold`。 |
| `I2V_SCENES` | `auto` | 图生视频的分镜选择，见下方策略说明。 |
| `IMAGE_STYLE_PROMPT` | 国漫画风 | 全片统一画风，可自行替换。 |

### 布局坐标的单位

`NARRATION_SUBTITLE_Y`、`KEYWORD_Y`、`TITLE_Y` 的单位是**以 1920 高为基准的像素**，与实际画布高度无关（正数向上，负数向下）：

```text
transform_y = 配置值 / 960          剪映的可见范围是 -1 到 1
-700  ->  -0.729   横屏 1080 画布上离底边约 146 px，标准字幕位置
+520  ->  +0.542   上三分之一，标题位置
```

超出可见范围的值会被自动收进安全区。用 `--check-config` 可以直接看到每一层的实际落点：

```powershell
python animated_caption_draft.py --check-config
```

它同时会打印每个关键配置的**来源**（`.env` / 系统环境变量 / 默认值）—— 系统里有同名变量时会覆盖 `.env`，这一行能省很多排查时间。

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

只生成并打印分镜方案（含角色设定与关键词）：

```powershell
python animated_caption_draft.py --draft-name plan_preview --input copy.txt --plan-only
```

`--plan-only` 仍会调用方舟文本接口。失败时加 `--verbose` 可以打印完整调用栈。

## 时间轴结构

```text
0s        OPENING_LEAD_SECONDS                                        结束
|---------|--------------------------------------------------------------|
 音效+标题  第 1 句旁白      第 2 句旁白        ...        最后一句
[视觉]     第 1 镜画面从 0s 就在，不会出现开头黑屏
[字幕]              每句一段，带入场动画
[关键词]            仅在模型挑出关键词的句子上出现
[BGM]      整轨循环，开头淡入 0.6s，结尾淡出 0.9s
```

## 图生视频策略

`I2V_SCENES=auto`（默认）会把 `I2V_MAX_COUNT` 段动态视频均匀铺在全片上，并且**一定包含第 1 镜和最后一镜**。这样运动感贯穿始终，不会出现前几镜在动、后面突然全静止的断层。

也可以手动指定分镜序号，或者整体关闭：

```dotenv
I2V_SCENES=1,5,9,14
I2V_SCENES=none
```

其余分镜使用静态图片 + 关键帧运镜。动态视频保持无声，避免与旁白和 BGM 重叠。视频比对应旁白短时（帧率取整导致），画面会用同镜静图补齐尾部，不会中断草稿生成。

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

成功素材会被复用，包括已经付过费但不在当前 `I2V_SCENES` 选择里的视频。恢复静态任务：

```powershell
python animated_caption_draft.py --resume my_story --skip-i2v
```

如果原任务启用了图生视频，恢复时不要添加 `--skip-i2v`。

> **覆盖同名草稿需要显式加 `--replace`，包括 `--resume` 的时候。** 覆盖会删除整个草稿文件夹，你在剪映里做过的手工修改会一并消失。请先在剪映中关闭该草稿。

## 开发

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
```

测试覆盖不调用付费接口的部分：布局坐标换算、镜头切分、运镜参数、BGM 循环与淡入淡出、分镜 JSON 容错、图生视频分镜选择，以及一个用合成素材真正生成草稿并检查时间轴的集成测试。

## 常见问题

- **HTTP 429**：降低 `IMAGE_CONCURRENCY`。错误信息里现在会带上接口返回的原文，可据此区分限流和欠费。
- **SSL EOF 或网络中断**：稍后使用 `--resume`；必要时临时降为 1 路。
- **找不到开场音效**：确认 `OPENING_SOUND_PATH` 指向存在的 MP3 或 WAV。
- **找不到剪映目录**：确认 `JIAN_YING_DRAFT_DIR` 是剪映专业版草稿根目录。
- **字幕位置不合适**：修改 `NARRATION_SUBTITLE_Y`，先用 `--check-config` 看落点。
- **改了 `.env` 却不生效**：系统环境变量优先级更高，`--check-config` 会显示来源。
- **图生视频提交失败且图片很大**：确认已安装 Pillow，首帧会被压到 720P JPEG 再上传。

## 隐私与发布安全

`.gitignore` 已排除：

- `.env` 及本机环境配置
- `output/` 中的生成媒体、日志和任务状态
- `assets/` 中的个人水印、音效和 BGM
- Python 缓存、虚拟环境和编辑器状态

提交前仍建议执行密钥扫描。若密钥曾被提交或发送到不可信位置，应立即撤销并重新创建。

## 当前限制

- 单次文案最多约 1800 个非空白字符。
- 画布固定为横屏 1920x1080。
- 输出面向剪映专业版草稿格式，不保证兼容其他剪辑软件。
- 使用 [pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft) 写入剪映草稿。

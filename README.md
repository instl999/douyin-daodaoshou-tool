# 文案转国漫视频剪映草稿生成器

把中文文案自动转换为可继续编辑的剪映专业版草稿：AI 分镜、逐段配音、逐字幕漫画画面、实时字幕、标题、开场音效，以及可选的 BGM 和水印。

English documentation: [README.en.md](README.en.md)

## 核心能力

- 使用火山方舟 Agent Plan 规划中文分镜、生成静态图片和合成语音。
- 一条完整字幕对应一张图片；画面只服从当前字幕，不强制添加图表、行情面板或财经符号。
- **画风预设**：默认采用抖音情感故事条漫画风 —— 粗均匀墨线、平涂低饱和、柔和平光。可一键切换到韩式写实条漫或高对比电影风，也可以整段自定义。
- **角色一致性**：分镜阶段抽取全片共用的角色设定，逐字注入每张图的提示词，避免主角每隔几秒换一张脸。
- **一张图一个完整镜头**：`01.png` 播完直接接 `02.png`，同一张图不会在中途被切开。
- **运镜**：五种关键帧运镜逐镜头轮换。运动量由**镜头时长**推导（默认每秒 3.5%），所以 1.6 秒和 6.2 秒的镜头观感速度一致；横移幅度始终收在缩放留出的余量内，不会露边。
- **镜头之间一律硬切**，没有转场也没有入场动画，画面的运动完全由运镜承担。
- 静态图片默认 3 路并发生成；单张失败不会丢失其他成功结果，可通过 `--resume` 精确补跑。
- 自动创建剪映草稿并分离视觉、旁白、实时字幕、标题、开场音效、BGM 和水印轨道。

## 环境要求

- Windows 10/11
- Python 3.10 或更高版本
- 剪映专业版
- 火山方舟 Agent Plan API Key

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

## 画风

`IMAGE_STYLE_PRESET` 三选一：

| 预设 | 观感 | 适合 |
| --- | --- | --- |
| `story`（默认） | 粗均匀墨线、平涂低饱和、柔和平光、腮红、背景简洁 | 抖音情感/民间故事号最常见的画风，辨识度高，最容易保持稳定 |
| `webtoon` | 线更细、渲染更柔、色彩更克制 | 观感更“高级”，适合职场、财经、成长类内容 |
| `cinematic` | 高饱和深蓝 + 强对比电影光 | 本项目的旧默认值，戏剧性强但一致性更难控 |

需要完全自定义时把整段提示词写进 `IMAGE_STYLE_PROMPT`，它会覆盖预设。`--check-config` 会显示当前生效的是哪一个。

换画风建议同时设一个固定的 `ARK_IMAGE_SEED`，画风会更稳，重跑结果也可复现。

## 配置

完整配置项和说明都在 [.env.example](.env.example) 里，每一项都有注释。最常调的几项：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ARK_API_KEY` | 空 | 方舟 Agent Plan 密钥；分镜、生图和 TTS 共用。 |
| `JIAN_YING_DRAFT_DIR` | 空 | 本机剪映草稿根目录。 |
| `IMAGE_STYLE_PRESET` | `story` | 全片画风，见上表。 |
| `SCENE_CHARACTERS_PER_IMAGE` | `22` | 每镜头承载的中文字符数，下限 8。这是控制画面节奏的唯一参数：调小 = 切得更快、图更多、成本更高。 |
| `NARRATION_SUBTITLE_Y` | `-700` | 字幕位置，详见下方坐标说明。 |
| `BGM_VOLUME` | `0.10` | BGM 线性增益（约 -20 dB）。 |
| `TITLE_STYLE` | `paper` | 标题配色：`paper`（取自画风色板）/ `red` / `white` / `gold`。 |
| `KEN_BURNS_RATE` | `0.035` | 运镜速度，每秒走过画面的比例。调大更明显。 |

### 布局坐标的单位

`NARRATION_SUBTITLE_Y` 和 `TITLE_Y` 的单位是**以 1920 高为基准的像素**，与实际画布高度无关（正数向上，负数向下）：

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

只生成并打印分镜方案（含角色设定）：

```powershell
python animated_caption_draft.py --draft-name plan_preview --input copy.txt --plan-only
```

`--plan-only` 仍会调用方舟文本接口。失败时加 `--verbose` 可以打印完整调用栈。

## 时间轴结构

```text
0s        OPENING_LEAD_SECONDS                                        结束
|---------|--------------------------------------------------------------|
 音效+标题  第 1 句旁白      第 2 句旁白        ...        最后一句
[视觉]     一句一图，一图一镜到底；第 1 镜从 0s 就在，镜头之间全部硬切
[字幕]              每句一段，带入场动画
[BGM]      整轨循环，开头淡入 0.6s，结尾淡出 0.9s
```

## 断点恢复

每次任务保存在：

```text
output/<draft-name>/
  manifest.json
  failures.json
  run.log
  audio/
  images/
```

成功素材会被复用。恢复一个失败的任务：

```powershell
python animated_caption_draft.py --resume my_story
```

> **覆盖同名草稿需要显式加 `--replace`，包括 `--resume` 的时候。** 覆盖会删除整个草稿文件夹，你在剪映里做过的手工修改会一并消失。请先在剪映中关闭该草稿。

## 开发

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
```

测试覆盖不调用付费接口的部分：布局坐标换算、运镜参数、BGM 循环与淡入淡出、分镜 JSON 容错、画风预设，以及一个用合成素材真正生成草稿并检查时间轴的集成测试。

## 常见问题

- **HTTP 429**：降低 `IMAGE_CONCURRENCY`。错误信息里会带上接口返回的原文，可据此区分限流和欠费。
- **SSL EOF 或网络中断**：稍后使用 `--resume`；必要时临时降为 1 路。
- **找不到开场音效**：确认 `OPENING_SOUND_PATH` 指向存在的 MP3 或 WAV。
- **找不到剪映目录**：确认 `JIAN_YING_DRAFT_DIR` 是剪映专业版草稿根目录。
- **字幕位置不合适**：修改 `NARRATION_SUBTITLE_Y`，先用 `--check-config` 看落点。
- **改了 `.env` 却不生效**：系统环境变量优先级更高，`--check-config` 会显示来源。
- **画风和预期不符**：先用 `--plan-only` 看模型写出的 `image_prompt`；画风词只在其后拼接，如果分镜描述本身就跑偏了，改画风预设没用。

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
- 只生成静态图片画面，不含图生视频。
- 输出面向剪映专业版草稿格式，不保证兼容其他剪辑软件。
- 使用 [pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft) 写入剪映草稿。

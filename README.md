# 抖音「心理叨叨兽」同款商品制作工具 · 剪映草稿生成器

复刻抖音博主「心理叨叨兽」（[博主主页](https://v.douyin.com/AYhnYiaH0uo/)）的同款视频：把一段中文文案，变成一份可以在剪映专业版里继续编辑的草稿 —— AI 拆分镜、逐句配音、逐句生成画面，然后自动铺好视觉、旁白、字幕、标题、音效、BGM 和调色轨道。

生成的是**草稿**而不是成片 —— 所有素材和关键帧都在时间轴上，你可以随时手动改，再自己导出。

English documentation: [README.en.md](README.en.md)

---

## 60 秒上手

```powershell
git clone https://github.com/instl999/douyin-daodaoshou-tool.git
Set-Location .\douyin-daodaoshou-tool
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

在 `.env` 里填三样东西：`ARK_API_KEY`、`ARK_TTS_VOICE_TYPE`、`JIAN_YING_DRAFT_DIR`，再往 `assets/` 放一个开场音效。然后：

```powershell
python animated_caption_draft.py --check-config
```

没报错就可以出片：

```powershell
python animated_caption_draft.py --draft-name my_story --title "示例标题" --input copy.txt
```

跑完打开剪映，草稿列表里就有 `my_story` 了。

---

## 工作原理

```text
文案 ──▶ 分镜规划 ──▶ 逐句配音 ──▶ 逐句生图 ──▶ 写入剪映草稿
        (文本模型)      (TTS)       (生图模型)
          │              │            │
          │              │            └─ 每句一张图，注入统一画风与角色设定
          │              └─ 决定每个镜头的实际长度
          └─ 拆句 + 抽取全片角色 + 逐句标注景别和段落边界
```

四个阶段的产物都会落盘到 `output/<草稿名>/`，任何一步失败都能用 `--resume` 从断点续跑，已经生成过的素材不会重复付费。

---

## 它替你做了哪些剪辑判断

这部分是这个工具和"批量图片轮播"的区别所在。

| | 做法 |
| --- | --- |
| **画风统一** | 全片共用一段画风提示词，三个预设可选，也可整段自定义 |
| **角色一致** | 分镜阶段抽出全片共用的角色设定，逐字注入每一张图的提示词，主角不会每隔几秒换一张脸 |
| **景别有变化** | 逐句决定全景 / 中景 / 特写：全景开段落和交代环境，特写落在情绪、转折和结论上 |
| **一图一镜到底** | `01.png` 播完直接接 `02.png`，同一张图不会在中途被切开 |
| **运镜速度恒定** | 五种运镜轮换，运动量由**镜头时长**推导，1.6 秒和 6.2 秒的镜头观感速度一致；横移幅度收在缩放留出的余量内，不会露边 |
| **片子会呼吸** | 段落结尾插入气口（只剩 BGM，画面撑着不黑），最后一句念完画面再留 1.8 秒 |
| **BGM 会闪避** | 人声底下压到 0.10，气口和结尾抬到 0.20；空档太短就不抬，免得每句之间都在"喘" |
| **字幕基线稳定** | 换行的字幕整体上抬，让底行位置不动，不会因为行数变化而上下跳 |
| **色彩统一** | 顶层一条滤镜轨铺满全片，把各自独立生成、色温会漂的图拉到同一个调子 |
| **全片硬切** | 没有转场也没有入场动画，画面的运动完全由运镜承担 |

---

## 环境要求

- Windows 10/11
- Python 3.10 或更高版本
- 剪映专业版
- 火山方舟 Agent Plan API Key（分镜、生图、语音合成共用同一把）

## 准备本地素材

仓库不包含任何个人或可能受版权保护的媒体文件，请自行放入 `assets/`：

```text
assets/
  opening_dong.mp3       # 必需：开场音效
  background_music.mp3   # 可选：BGM
  watermark.png          # 可选：水印
```

在 `.env` 里填相对路径或完整路径。BGM 和水印留空即可跳过；开场音效目前是必需的。

---

## 画风在哪里改

按改动幅度从小到大，一共四个入口：

| 想怎么改 | 改哪里 | 具体位置 |
| --- | --- | --- |
| 三套现成画风换一套 | `.env` 的 `IMAGE_STYLE_PRESET` | [`.env.example`](.env.example) 第 142 行，`story` / `webtoon` / `cinematic` 三选一 |
| 换成自己写的完整画风描述 | `.env` 的 `IMAGE_STYLE_PROMPT` | [`.env.example`](.env.example) 第 144 行，填了就覆盖上面的预设（英文描述效果最稳） |
| 微调某一套预设的措辞 | 代码里的 `STYLE_PRESETS` 字典 | [`animated_caption_draft.py`](animated_caption_draft.py) 第 55–88 行，其中默认的 `story` 从第 58 行开始 |
| 换掉默认预设 | 代码里的 `DEFAULT_STYLE_PRESET` | [`animated_caption_draft.py`](animated_caption_draft.py) 第 89 行 |

配套设置：

- `ARK_IMAGE_SEED`（[`.env.example`](.env.example) 第 22 行）：固定随机种子，画风更稳、重跑可复现
- `COLOR_GRADE`：全片统一滤镜。最终观感由**画风提示词 + 滤镜**共同决定，`none` 关闭
- 画风提示词由 `compose_image_prompt()`（[`animated_caption_draft.py`](animated_caption_draft.py) 第 622 行）拼在每句分镜描述之后 —— 画面跑偏时先 `--plan-only` 看分镜描述本身对不对，分镜偏了改画风没用
- `--check-config` 会打印当前生效的画风，以及它的来源（`.env` / 系统环境变量 / 默认值）

---

## 使用方法

从 UTF-8 文本文件生成：

```powershell
python animated_caption_draft.py --draft-name my_story --title "示例标题" --input copy.txt
```

短文案可以直接传：

```powershell
python animated_caption_draft.py --draft-name demo --title "示例标题" --text "这里是文案。"
```

只看分镜方案，不生成媒体（仍会调用文本模型）：

```powershell
python animated_caption_draft.py --draft-name preview --input copy.txt --plan-only
```

输出里能看到每一句被拆成什么、用什么景别、哪句是段落结尾、抽出了哪些角色。**画面不理想时先看这里** —— 画风提示词是拼在分镜描述之后的，如果分镜描述本身就跑偏了，改画风没有用。

| 参数 | 作用 |
| --- | --- |
| `--input` / `--text` | 文案来源，二选一 |
| `--draft-name` | 剪映里的草稿名；省略则用时间戳 |
| `--title` | 片头标题；省略则取文案首行 |
| `--resume DRAFT_NAME` | 从断点续跑，复用已生成的素材 |
| `--replace` | 允许覆盖同名草稿（**会删掉整个草稿文件夹**） |
| `--check-config` | 只校验配置和素材，不调用任何 API |
| `--plan-only` | 只生成并打印分镜方案 |
| `--verbose` | 失败时打印完整调用栈 |

---

## 时间轴结构

```text
0s      lead                          段落气口                    最后一句  留白
|-------|--------|--------|--------|~~~~~~|--------|  ...  |--------|~~~~~~~~|
 音效+标题  第1句     第2句     第3句           第4句                最后一句

[视觉]   一句一图，一图一镜到底；第 1 镜从 0s 就在，气口和结尾留白由前一张图撑着
[旁白]   每句一段，句与句之间只有段落气口
[字幕]   跟旁白对齐，带入场动画；气口里没有字幕
[音效]   开场一次
[BGM]    人声下压到 0.10，气口和结尾抬到 0.20；整轨循环，首尾淡入淡出
[调色]   一条滤镜铺满全片
```

气口只出现在分镜模型标记为**段落结尾**的句子之后 —— 八个自然段的稿子会有七处呼吸点，而不是每句都停。

---

## 配置

全部配置项都在 [.env.example](.env.example) 里逐条注释。最常调的：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ARK_API_KEY` | 空 | 方舟 Agent Plan 密钥，三个模型共用 |
| `ARK_TTS_VOICE_TYPE` | 示例音色 | 音色 ID |
| `JIAN_YING_DRAFT_DIR` | 空 | 本机剪映草稿根目录 |
| `IMAGE_STYLE_PRESET` | `story` | 全片画风 |
| `SCENE_CHARACTERS_PER_IMAGE` | `22` | 每镜头承载的中文字数，下限 8。**控制画面节奏的唯一参数**：调小 = 切得更快、图更多、成本更高 |
| `KEN_BURNS_RATE` | `0.035` | 运镜速度，每秒走过画面的比例 |
| `PARAGRAPH_PAUSE_SECONDS` | `0.5` | 段落结尾的气口长度 |
| `ENDING_HOLD_SECONDS` | `1.8` | 最后一句之后画面再留多久 |
| `BGM_VOLUME` / `BGM_LIFT_VOLUME` | `0.10` / `0.20` | 人声下 / 空档里的 BGM 音量 |
| `COLOR_GRADE` | `灰调中性` | 全片统一滤镜，`none` 关闭 |
| `NARRATION_SUBTITLE_Y` | `-700` | 字幕位置，见下 |
| `TITLE_STYLE` | `paper` | 标题配色：`paper` / `red` / `white` / `gold` |
| `IMAGE_CONCURRENCY` | `3` | 生图并发，遇到 429 就调小 |

### 布局坐标的单位

`NARRATION_SUBTITLE_Y` 和 `TITLE_Y` 的单位是**以 1920 高为基准的像素**，与实际画布高度无关（正数向上，负数向下）：

```text
transform_y = 配置值 / 960          剪映的可见范围是 -1 到 1
-700  ->  -0.729   横屏 1080 画布上离底边约 146 px，标准字幕位置
+520  ->  +0.542   上三分之一，标题位置
```

超出可见范围的值会被自动收进安全区。`--check-config` 会直接打印每一层的实际落点，以及每个关键配置的**来源**（`.env` / 系统环境变量 / 默认值）—— 系统里有同名变量时优先级更高会盖掉 `.env`，这一行能省很多排查时间。

---

## 断点恢复

每次任务的全部状态都保存在：

```text
output/<草稿名>/
  manifest.json    分镜、角色、各素材路径
  failures.json    失败记录
  run.log          逐事件日志
  audio/           01.mp3, 02.mp3 ...
  images/          01.png, 02.png ...
```

已成功的素材会被复用，只补跑缺失的部分：

```powershell
python animated_caption_draft.py --resume my_story
```

> **覆盖同名草稿必须显式加 `--replace`，`--resume` 时也一样。** 覆盖会删除整个草稿文件夹，你在剪映里做过的手工修改会一起消失。操作前请先在剪映中关闭该草稿。

---

## 开发

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
```

测试覆盖所有不需要调用付费接口的逻辑：

- 布局坐标换算与安全区收敛
- 运镜速率归一化、放大上限、横移不露边
- 时间轴排布：镜头连续无缝、气口位置、结尾留白
- BGM 循环、首尾淡入淡出、闪避包络（含"空档太短就不抬"）
- 字幕换行估算与基线补偿
- 分镜 JSON 容错与景别 / 段落标记解析
- 一个集成测试：用合成素材真正生成一份草稿，再解析 `draft_content.json` 逐项断言时间轴

---

## 常见问题

| 现象 | 处理 |
| --- | --- |
| HTTP 429 | 调小 `IMAGE_CONCURRENCY`。错误信息里带了接口返回的原文，可据此区分限流和欠费 |
| SSL EOF / 网络中断 | 稍后 `--resume`，必要时临时把并发降到 1 |
| 找不到开场音效 | 确认 `OPENING_SOUND_PATH` 指向存在的 MP3 或 WAV |
| 找不到剪映目录 | 确认 `JIAN_YING_DRAFT_DIR` 是剪映专业版的草稿根目录 |
| 字幕位置不合适 | 改 `NARRATION_SUBTITLE_Y`，先用 `--check-config` 看落点 |
| 字幕换行时上下飘 | 调 `SUBTITLE_EM_PX`，只有这一个数影响换行估算 |
| 改了 `.env` 不生效 | 系统环境变量优先级更高，`--check-config` 会显示来源 |
| 画风不对 | 先 `--plan-only` 看分镜描述，画风词是拼在它后面的 |

---

## 隐私与发布安全

`.gitignore` 已排除 `.env`、`output/` 下的全部生成媒体与日志、`assets/` 下的个人素材，以及 Python 缓存和编辑器状态。

提交前仍建议自己扫一遍密钥。如果密钥曾经被提交或发送到不可信的地方，应立即在控制台撤销并重新创建。

## 当前限制

- 单次文案最多约 1800 个非空白字符
- 画布固定为横屏 1920×1080
- 只生成静态图片画面，不含图生视频
- 输出面向剪映专业版的草稿格式，不保证兼容其他剪辑软件

## 许可证

[MIT](LICENSE)。可自由使用、修改和商用，保留版权声明即可。

注意：本项目只包含代码。你自己放进 `assets/` 的音效、音乐和水印，以及生成过程中调用的模型服务，各自适用它们自己的授权条款。

## 依赖

草稿写入基于 [pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft)。

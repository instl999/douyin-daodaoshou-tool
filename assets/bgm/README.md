# BGM 曲库 / Music library

**这个文件夹里的音乐是随仓库提交的。** 放一首新的进来，按下面的命名规则改好文件名，
`git add` 就会带上它。

音频本身的版权属于各自的权利人 —— 仓库的 MIT 许可只覆盖代码，不覆盖这里的音乐。
要在别处分发或商用，请自行确认你持有相应授权。

**The audio in this folder is committed with the repository.** Drop a new track
in, name it by the rule below, and `git add` will pick it up.

The tracks remain the property of their respective rights holders — the
repository's MIT licence covers the code, not the music here. Confirm you hold
the rights before redistributing or using them commercially.

---

## 怎么命名 / Naming

文件名**以中文情绪标签开头**，标签之后接曲名，就能被自动匹配：

Put the Chinese mood label at the **start** of the filename, before the track
title, and it will be matched automatically:

```text
紧张Kill Drill - Robert Ruth.mp3
紧张危机Dismantle - Peter Sandberg.mp3
舒缓Keep on the Sunny Side - 岩崎太整.mp3
开头失落IV - AK,Mapps.mp3
```

标签 = 文件名开头那一段**连续的中文**，到第一个非中文字符为止。
所以结尾处的中文歌手名不会被误当成标签。

The label is the run of Chinese characters the filename opens with, up to the
first character that is not Chinese — so a Chinese artist credit at the **end**
is not mistaken for one.

支持的扩展名 / Accepted: `.aac .flac .m4a .mp3 .ogg .wav`

---

## 可用标签 / Labels

`紧张`  `危机`  `焦虑`  `疑问`  `疑惑`  `失落`  `转机`  `升华`  `欢乐`  `舒缓`  `平淡`  `解释`  `讲解`  `措施`  `解决`

标签可以叠加：`紧张危机…` 对「又紧张又有危机感」的文案，会排在只标 `紧张…` 的前面。

Labels combine: `紧张危机…` outranks a plain `紧张…` for a script read as both.

### 位置标签 / Positional labels

`开头`  `结尾`  `后期`  `提出`

这些是写给**某一段**用的曲子。整片只铺一首底乐，所以同情绪下它们会排在无前缀的
曲目后面 —— **是排后面，不是排除**：曲库里只有它时照样会用。

These are written for one stretch of a video. Only one bed runs under the whole
video, so they rank below an unprefixed track of the same mood — **ranked down,
not excluded**: if it is the only thing in the folder, it is still used.

---

## 挑选规则 / How a track is chosen

| | |
| --- | --- |
| 情绪从哪来 | 分镜/导演阶段顺手多问一个字段，**不额外花一次请求**；模型没给就退回读文案里的词 |
| 标签越贴越优先 | 判成「紧张 + 危机」时 `紧张危机…` 胜过 `紧张…` |
| 同分怎么办 | 按文件名取靠前的那首，**重跑挑到的是同一首** |
| 一首都不匹配 | **不加 BGM**。配错的音乐比没有音乐更干扰，而这是全片最没人复查的一项 |
| 想固定某一首 | `.env` 的 `BGM_PATH` 指向那首，填了就完全不看曲库 |

| | |
| --- | --- |
| Where the mood comes from | One extra field on the call the director already makes — **no second request** — falling back to the words in the script |
| Closer labels win | `紧张危机…` beats `紧张…` for a script read as both |
| Ties | Resolved by filename, so **a rebuild keeps its music** |
| Nothing matches | **No music.** The wrong bed is more distracting than none, and it is the one choice nobody plays back to check |
| Pin one track | `BGM_PATH` in `.env` — set it and the library is not consulted |

---

## 放在别处 / Keeping the library elsewhere

`.env` 的 `BGM_LIBRARY` 指向任意目录即可，留空就用这个文件夹。
`python animated_caption_draft.py --check-config` 会打印曲库路径、曲目数，以及有几首带得上标签。

Point `BGM_LIBRARY` in `.env` at any folder; empty means this one. `--check-config` prints the path, the track count and how many carry a label.

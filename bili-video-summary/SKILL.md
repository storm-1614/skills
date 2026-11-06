---
name: bili-video-summary
description: 通过 Bilibili 视频链接抓取字幕或进行本地转写，生成中文为主、保留细节和时间戳的详细笔记；适用于用户提供 BV 号、av 号或 b23.tv 链接并要求总结、摘要、双语要点或说明视频内容的场景。
---

# Bilibili 视频详细总结

用户提供 Bilibili 链接并希望了解视频内容时使用本技能。支持 BV 号、av 号、b23.tv 短链和带 `?p=N` 的多 P 链接。

## 工作流程

### 1. 解析视频并获取文案

官方字幕和 AI 字幕可能需要 B 站登录态。无头环境可先运行终端扫码登录：

```bash
.venv/bin/python .codex/skills/bili-video-summary/scripts/bili_login.py
```

用 B 站手机 App 扫描终端二维码并确认。凭据和续期令牌保存在用户配置目录 `~/.config/bili-video-summary/credentials.json`，权限限制为当前用户；不要读取、展示或复制该文件内容。检查登录状态：

```bash
.venv/bin/python .codex/skills/bili-video-summary/scripts/bili_login.py --status
```

优先执行官方字幕抓取：

```bash
.venv/bin/python .codex/skills/bili-video-summary/scripts/bili_fetch.py "<视频链接>"
```

根据 stdout 的 JSON 处理：

- `status=ok`：读取 `text_file`，使用返回的标题、UP 主、时长、BVID、`subtitle_lan` 和 `subtitle_kind`。`subtitle_kind=ai` 表示 B 站 AI 生成轨道。
- `error=no_subtitle`：明确告诉用户没有官方字幕，并询问是否允许下载音频进行 Whisper 转写；用户同意后再执行兜底脚本。
- `needs_cookie=true`：提示用户运行 `bili_login.py` 并扫码后重试；也兼容旧方式 `BILI_SESSDATA` 或项目根目录 `.sessdata`，不要索取或输出凭据。
- `risk_flag=true`：字幕可能是风控降级导致的错误文案，优先使用 Whisper 重新转写。
- 其它错误：按照 JSON 中的 `hint` 处理；网络访问失败时重试或申请必要的网络权限。

Whisper 兜底命令：

```bash
.venv/bin/python .codex/skills/bili-video-summary/scripts/bili_transcribe.py "<视频链接>"
```

若返回 `missing_deps`，按 `references/install.md` 安装依赖后重试。长视频转写可能接近视频时长的 1–2 倍，应提前告知用户。

### 2. 阅读与分块

- 读取字幕或转写文本文件，不凭标题猜测视频内容。
- 文本不超过 40,000 字符时直接总结；超过时按自然段分成每块不超过 12,000 字符的若干块，再按时间顺序合并。
- 保留人名、机构名、数字、模型/论文/书名、关键术语、因果关系、例子和有价值的原话。
- 官方 AI 字幕或 Whisper 可能有识别错误。只在语境足够明确时规范化技术术语；不确定的内容用转述，不要编造“原话”。

### 2.1 获取视频画面并用于总结

每次处理 Bilibili 视频链接时，都尝试获取视频画面并提取关键帧；字幕总结能力仍照常运行。字幕抓取或 Whisper 转写成功后，官方字幕与 Whisper 的 `transcript_json`（含起止时间戳）作为分段和对齐输入，原 `.txt` 输出保持原样。若字幕不可用，仍尽量获取关键帧；没有时间戳字幕时不得伪造字幕对齐关系。

准备实验材料：

```bash
.venv/bin/python .codex/skills/bili-video-summary/scripts/bili_multimodal_experiment.py \
  --transcript output/transcripts/<视频标识>.json \
  --video-url "<用户提供的 Bilibili 链接>" \
  --output "/tmp/bili-multimodal-<视频标识>"
```

也可用 `--video-file` 指向本地视频。默认每个视频最多下载一次，限高 720p、下载上限 500 MB；关键帧最多 30 张，场景阈值 0.3，静态画面每 60 秒补采样，最小间隔 3 秒。可按视频情况调整这些预算参数，不要为了取帧下载超高分辨率视频。下载失败时继续纯字幕总结，并在结果中说明未能读取画面的原因。脚本会自动清理下载的临时视频；总结完成后删除 `/tmp` 下的实验目录。

有关键帧时，通过当前调用环境的图片输入能力逐张检查 `segments.json` 中与字幕片段关联的 JPEG。Codex 环境可用 `view_image` 将本地 JPEG 作为图片输入；不要只把文件名或路径交给模型。综合相同字幕与关键帧生成总结，无需每次都生成 A/B 两份总结。若当前模型/调用器不支持图片输入，仍完成取帧，但不得声称做过视觉分析；应明确报告图片输入限制，并仅基于字幕总结。

字幕用于理解讲解，画面用于补充直接可辨认的代码、公式、图表、架构和软件操作信息。总结应保留关键结论对应的时间戳，并区分字幕明确说明与画面补充的信息；模糊代码或公式不得猜写。A/B 盲评仅在用户要求对照实验时执行，此时使用同一字幕、模型、要求和分段，唯一变量为图片输入；只记录模型界面或 API 实际报告的 token 和耗时，不可用的数据标为 `null`。

### 3. 生成笔记

默认生成可直接放入 Obsidian vault 的完整 Markdown 笔记。文件开头必须有 YAML frontmatter，至少填写 `title`、`date`、`source` 和 `tags`；日期填写生成笔记当天的本地日期（`YYYY-MM-DD`），不要把视频发布日期误当成笔记日期。根据已核实的信息补充 `bvid`、`creator`、`duration`、`subtitle_source` 等字段；未知或未核实的字段省略。标签使用 YAML 列表，选择少量具体主题标签，避免空泛或重复标签。标题、链接等可能含有冒号或特殊字符的字符串用引号包裹，确保 frontmatter 可解析。

默认中文为主；用户明确要求英文或双语时，再逐段补充英文。frontmatter 和正文格式按 `references/summary-template.md`：

1. 标题与 emoji 元信息
2. 一段内容概览
3. 一句话摘要 / One-Line Summary
4. 按内容转折组织的分时概述
5. 金句摘录
6. 不按时间、按主题归纳的总体总结

分时段不要机械平均切分。短视频每段保留 2–4 条，长视频每段保留 4–8 条；宁可展开，也不要把一大段压缩成一句空泛概括。

## 文件保存与命名

### 默认目录

默认把笔记保存到当前工作区的 `output/`。如用户指定了笔记目录，将主文件保存到指定位置，并在工作区保留一份镜像：

```text
output/
```

脚本生成的字幕、转写和多模态实验文件也位于当前工作区的 `output/`。可通过环境变量 `BILI_VIDEO_SUMMARY_HOME` 指定其它工作区。若用户指定了其它笔记目录，以用户指定目录为准；不要默默退回使用 BVID 文件名。

### 文件名规则

- 文件名直接使用视频标题或基于标题的简短中文标题，扩展名为 `.md`。
- 禁止使用 `BV...-summary.md`、`av...-summary.md` 作为默认文件名；BVID 只放在元信息或作为去重依据。
- 保留中文、人名、专有名词、英文模型名和必要数字；不要把标题翻译成拼音，也不要默认添加 `summary`、`总结` 后缀。
- 仅清理路径分隔符、控制字符和文件系统不适宜字符；可用 `-` 替换。标题过长时保留“主题/嘉宾/期数”等可识别信息，缩短为可读的中文名。
- 目标文件已存在时，在中文文件名末尾追加 `-2`、`-3` 等，不覆盖已有笔记。

示例：

```text
南京大学生成式软件工程1-欢迎来到未来.md
南京大学生成式软件工程2-提示词工程.md
姚顺宇4小时访谈.md
对话Kimi创始人杨植麟-从K2到Agent与无限攀登.md
```

### 写入与回复

- 文件内容使用完整 Markdown；不要只写摘要或只写链接。
- 回复中提供外部主文件和项目镜像的可点击路径（若某个位置无法写入，说明原因）。
- 按技能模板把完整 Markdown 直接呈现在回复中，确保用户无需打开文件才能阅读总结。

## Cookie

官方字幕经常需要登录态。读取顺序为用户配置目录中的扫码登录凭据、临时环境变量 `BILI_SESSDATA`、项目根目录 `.sessdata` 兼容文件，最后才是匿名请求。扫码登录会保存 Web Cookie 和 `refresh_token`，抓取前按 B 站接口提示续期并轮换令牌。Cookie 与刷新令牌都是登录凭据，不能提交 Git、写入总结或展示给用户。

字幕轨道先查询旧版 JSON 播放器接口，再回退到新版 `/x/v2/subtitle/web/view` Protobuf 接口；不能仅凭旧接口的空列表认定视频没有字幕。只在两个接口都未返回轨道时才走 `no_subtitle` 流程。

## 参考资料

- API、Wbi 签名和风控信号：`references/api-notes.md`
- Whisper 依赖和故障排查：`references/install.md`
- 笔记格式、文件名和长文本分块细则：`references/summary-template.md`

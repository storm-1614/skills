# Bilibili 视频详细总结 Skill

一个面向 Codex 等 AI 编程助手的 skill：从 Bilibili 视频获取官方字幕；在没有字幕且用户同意时，可下载音频并使用 Whisper 转写，最后生成带时间戳、保留细节的中文 Markdown 笔记。也支持整理字幕与关键帧，辅助理解视频中的代码、公式和图表。

## 功能

- 支持 BV 号、av 号、`b23.tv` 短链和多 P 视频。
- 优先读取官方字幕；标记 AI 字幕并提示可能的识别误差。
- 提供终端扫码登录和会话续期，凭据保存在用户配置目录。
- 可选 Whisper 转写和关键帧提取。
- 按时间戳生成详细中文笔记，并提供 Markdown 模板。

## 目录结构

```text
bili-video-summary/
├── SKILL.md
├── README.md
├── agents/
├── references/
└── scripts/
```

## 安装

### Codex

将本目录复制到项目的 `.codex/skills/bili-video-summary/`。重启或刷新 Codex 后即可调用 `bili-video-summary` skill。

```bash
mkdir -p .codex/skills
cp -R bili-video-summary .codex/skills/bili-video-summary
```

其它支持 Agent Skills 的工具可按其文档安装 `SKILL.md`、`references/` 和 `scripts/`；不同工具可能需要调整脚本调用路径。

## 环境准备

官方字幕抓取和扫码登录使用 Python 标准库；扫码登录额外需要 `qrcode`。音频下载与转写需要 `yt-dlp` 和 `faster-whisper`：

```bash
python3 -m venv .venv
.venv/bin/pip install qrcode yt-dlp faster-whisper
```

关键帧提取需要系统安装 `ffmpeg`。首次 Whisper 转写会下载模型文件，具体速度和资源占用取决于硬件及所选模型。

## 手动运行

在仓库根目录执行以下命令：

```bash
# 查看登录状态
.venv/bin/python .codex/skills/bili-video-summary/scripts/bili_login.py --status

# 终端扫码登录（需要时）
.venv/bin/python .codex/skills/bili-video-summary/scripts/bili_login.py

# 获取官方字幕
.venv/bin/python .codex/skills/bili-video-summary/scripts/bili_fetch.py "<Bilibili 视频链接>"

# 用户同意进行音频转写后使用
.venv/bin/python .codex/skills/bili-video-summary/scripts/bili_transcribe.py "<Bilibili 视频链接>"
```

脚本以 JSON 输出处理状态。字幕、转写和实验文件默认保存在当前工作区的 `output/` 下；设置 `BILI_VIDEO_SUMMARY_HOME` 可指定工作区。

## 隐私与凭据

- 扫码登录的 Cookie 和续期令牌保存在 `~/.config/bili-video-summary/credentials.json`，目录权限为 `0700`，文件权限为 `0600`。
- 不要提交 `credentials.json`、`.sessdata`、Cookie、刷新令牌或私人视频数据。
- 音频转写只在用户同意后进行。下载的视频和临时音频由脚本按流程清理；生成的字幕、转写和笔记可能包含视频内容，请按个人数据处理。
- 登录凭据仅用于访问 Bilibili；本项目不会将凭据写入笔记或正常日志。

## 文档

- [Skill 使用说明](SKILL.md)
- [依赖安装与故障排查](references/install.md)
- [登录、字幕接口与风控说明](references/api-notes.md)
- [笔记模板与分块细则](references/summary-template.md)

## 说明

Bilibili 接口、登录流程及字幕可用性可能变化。请遵守 Bilibili 的服务条款、版权要求和适用法律法规。此项目由社区维护，与 Bilibili 官方无关。

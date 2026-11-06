# 依赖安装与故障排查

## 一、本项目依赖清单

| 用途 | 依赖 | 安装方式 |
|---|---|---|
| 官方字幕抓取 / Protobuf 解码 / 登录续期 | 无（纯 Python 标准库） | 不需要 |
| 终端二维码扫码登录 | `qrcode` | `.venv/bin/pip install qrcode` |
| 视频音频下载 | `yt-dlp` | `.venv/bin/pip install -U yt-dlp` |
| 语音转写 | `faster-whisper`（自动带 PyAV/ctranslate2） | `.venv/bin/pip install -U faster-whisper` |
| 音频解码 | PyAV（faster-whisper 内置，无需系统 ffmpeg） | 随 faster-whisper 安装 |
| （可选）系统 ffmpeg | 用于多模态关键帧提取 | 通过操作系统的软件包管理器安装 |

一键安装（在工作区根目录）：
```bash
python3 -m venv .venv
.venv/bin/pip install -U yt-dlp faster-whisper
.venv/bin/pip install qrcode
```

## 二、首次运行说明

- whisper 模型首次转写会从 HuggingFace 下载（`small` ≈ 461MB，`base` 更小），需联网
- CPU 转写速度：`small` 约 1–2× 实时（10 分钟视频 ≈ 10–20 分钟）；`base` 更快（~3–4× 实时）
- 内存：`small` + int8 通常约需 2GB；实际占用因平台和视频而异

## 三、故障排查

**终端扫码登录提示 `missing_qrcode`**
```bash
.venv/bin/pip install qrcode
.venv/bin/python .codex/skills/bili-video-summary/scripts/bili_login.py
```

扫码凭据和刷新令牌保存在 `~/.config/bili-video-summary/credentials.json`。登录状态失效或刷新失败时重新运行扫码命令；不要把 Cookie 或刷新令牌发到聊天中。

**`missing_deps` 错误**
```bash
.venv/bin/pip install -U yt-dlp faster-whisper
```
装完再跑脚本。确认包真的在 venv：`.venv/bin/python -c "import yt_dlp, faster_whisper; print('ok')"`

**yt-dlp 下载失败 / 找不到音频文件**
- 视频可能需登录或已删除。可尝试换 `-f bestaudio`（脚本已含该格式链）
- **HTTP 412 / 429**：B站风控限流（常见于短时间多次请求）。稍等几分钟冷却后重试；脚本已自动带 SESSDATA cookie 降低概率
- 确认 yt-dlp 版本较新：`.venv/bin/pip install -U yt-dlp`

**转写结果为空（0 字符）**
- 某些环境中的 VAD 兼容性可能导致音频被全部过滤；脚本默认关闭 VAD。若自行启用后结果为空，可尝试关闭 VAD
- 可显式指定语言 `--language zh` 提升准确率与稳定性

**官方字幕 `no_subtitle`**
- 先运行 `.venv/bin/python .codex/skills/bili-video-summary/scripts/bili_login.py --status` 确认登录态
- 字幕脚本会先查播放器 JSON 字幕轨道，并在空结果时查新版 Protobuf AI 字幕轨道
- 确认两种接口均无轨道后，才按 skill 流程询问是否使用 Whisper 兜底

**`risk_flag=true`（字幕张冠李戴）**
- 风控降级信号，脚本已自动建议走 whisper。单次重试多发生在短时间内频繁请求后，稍后重试可恢复

**模型下载慢 / 失败**
- 首次下载需访问模型托管服务；如需使用镜像，请配置你所在环境认可的镜像地址

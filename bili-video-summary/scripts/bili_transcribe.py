#!/usr/bin/env python3
"""bili_transcribe.py — whisper 兜底转写（视频无官方字幕时使用）。

流程：依赖检测 → yt-dlp 下载音频(bestaudio 单音频流) → faster-whisper 转写 →
      写 output/transcripts/<bvid>.txt → stdout 打印 JSON。

参数：<URL> [--model small|base] [--language auto|zh]
进度信息打印到 stderr，stdout 只输出 JSON（统一契约）。
"""
import argparse
import glob
import importlib.util
import json
import os
import sys

from bili_common import (
    PROJECT_DIR, TRANSCRIPTS_DIR,
    BiliError, cookie_header, err, parse_video_id, resolve_b23, slug,
)
from bili_auth import ensure_session_fresh
from bili_multimodal_experiment import (
    serialize_whisper_segments, write_whisper_transcripts,
)


def _missing_deps():
    missing = []
    for mod in ("yt_dlp", "faster_whisper", "av"):
        if importlib.util.find_spec(mod) is None:
            missing.append(mod)
    return missing


def main():
    ap = argparse.ArgumentParser(description="B站视频 whisper 兜底转写")
    ap.add_argument("url", help="B站视频链接（BV/av/b23.tv）")
    ap.add_argument("--model", default="small", choices=["small", "base"],
                    help="whisper 模型大小（默认 small，base 更快更省内存）")
    ap.add_argument("--language", default="auto", choices=["auto", "zh"],
                    help="转写语言（默认 auto 自动检测）")
    args = ap.parse_args()

    missing = _missing_deps()
    if missing:
        err("missing_deps",
            "转写依赖缺失: " + ", ".join(missing) +
            "；请先安装: .venv/bin/pip install -U yt-dlp faster-whisper",
            exit_code=3, missing=missing)

    import yt_dlp
    from faster_whisper import WhisperModel

    # 解析链接
    url = resolve_b23(args.url)
    bvid, aid = parse_video_id(url)
    if not bvid and aid is None:
        err("url_parse_failed", "无法解析链接，支持 BV号 / av号 / b23.tv 短链", exit_code=2)
    name = slug(bvid, aid)

    # 下载音频（bestaudio 单音频流，无需合并；PyAV 可直接解码 m4a/webm）
    os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)
    outtmpl = os.path.join(TRANSCRIPTS_DIR, name + ".%(ext)s")
    print(f"[transcribe] 下载音频: {url}", file=sys.stderr, flush=True)
    opts = {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    try:
        ensure_session_fresh()
    except BiliError as e:
        err(e.error, e.hint, exit_code=e.exit_code)
    # 带完整登录态可降低 B站 412/风控概率
    headers = cookie_header()
    if headers:
        opts["http_headers"] = {**headers, "Referer": "https://www.bilibili.com"}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as e:
        err("download_failed", f"yt-dlp 下载失败: {e}", exit_code=8)

    audio_path = None
    for f in glob.glob(os.path.join(TRANSCRIPTS_DIR, name + ".*")):
        if not f.endswith(".txt"):
            audio_path = f
            break
    if not audio_path:
        err("download_failed", "下载后未找到音频文件", exit_code=8)

    # 转写
    print(f"[transcribe] 加载模型 {args.model} (CPU int8)...", file=sys.stderr, flush=True)
    model = WhisperModel(args.model, device="cpu", compute_type="int8")
    lang = None if args.language == "auto" else args.language
    print("[transcribe] 开始转写（长视频可能需要几分钟）...", file=sys.stderr, flush=True)
    # 默认关闭 VAD，避免特定环境的兼容性问题导致全部音频被过滤。
    segments, info = model.transcribe(audio_path, language=lang, vad_filter=False, beam_size=5)
    timed_segments = [
        {"start": seg.start, "end": seg.end, "text": seg.text}
        for seg in segments
    ]
    text, timed_segments = serialize_whisper_segments(timed_segments)

    # 写纯文本文件
    text_file = os.path.join("output", "transcripts", name + ".txt")
    json_file = os.path.join("output", "transcripts", name + ".json")
    write_whisper_transcripts(
        os.path.join(PROJECT_DIR, text_file),
        os.path.join(PROJECT_DIR, json_file),
        text,
        timed_segments,
        metadata={
            "bvid": bvid,
            "aid": aid,
            "model": args.model,
            "detected_lang": getattr(info, "language", ""),
        },
    )

    # 清理音频临时文件
    try:
        os.remove(audio_path)
    except OSError:
        pass

    out = {
        "status": "ok",
        "source": "transcribed",
        "bvid": bvid,
        "aid": aid,
        "model": args.model,
        "detected_lang": getattr(info, "language", ""),
        "language_probability": getattr(info, "language_probability", None),
        "text_file": text_file,
        "transcript_json": json_file,
        "char_count": len(text),
        "line_count": len(timed_segments),
        "message": "",
    }
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()

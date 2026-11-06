#!/usr/bin/env python3
"""Prepare an optional, bounded Bilibili subtitle + keyframe experiment.

This script prepares timestamped inputs and report scaffolding. It does not call
or impersonate a vision model; the Skill runner supplies JPEGs to a model that
can inspect images and writes the two summaries and measured metrics.
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from bili_common import PROJECT_DIR, cookie_header


DEFAULT_EXPERIMENT_DIR = Path(PROJECT_DIR) / "output" / "multimodal_experiment"
SHOWINFO_TS = re.compile(r"\bn:\s*(\d+)\s+pts:\s*-?\d+\s+pts_time:([\d.]+)")
SENTENCE_END = re.compile(r"[。！？!?；;]$")


def _run_ffmpeg_candidates(video_path, output_dir, filter_expr, prefix):
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(output_dir / f"{prefix}_%06d.jpg")
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-loglevel", "info", "-y",
        "-i", str(video_path), "-vf", filter_expr, "-fps_mode", "vfr",
        "-q:v", "3", pattern,
    ]
    result = subprocess.run(cmd, text=True, capture_output=True)
    paths = sorted(output_dir.glob(f"{prefix}_*.jpg"))
    if (result.returncode and not paths
            and "No filtered frames for output stream" in result.stderr):
        # FFmpeg's image2/mjpeg output can exit nonzero when select() yields
        # zero frames. That's an ordinary scene-detector result; the static
        # sampling pass below still needs to run.
        return []
    if result.returncode:
        raise RuntimeError(f"ffmpeg frame extraction failed: {result.stderr[-1200:]}")
    timestamps = {int(n): float(ts) for n, ts in SHOWINFO_TS.findall(result.stderr)}
    candidates = []
    for index, path in enumerate(paths):
        timestamp = timestamps.get(index)
        if timestamp is None:
            # Some FFmpeg builds omit showinfo lines; never invent a timestamp.
            continue
        candidates.append({
            "timestamp": timestamp,
            "path": str(path),
            "kind": "scene" if prefix == "scene" else "fallback",
        })
    return candidates


def _gray_32x32(path):
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
        "-vf", "scale=32:32:flags=area,format=gray", "-frames:v", "1",
        "-f", "rawvideo", "-",
    ]
    result = subprocess.run(command, check=True, capture_output=True)
    if len(result.stdout) != 32 * 32:
        raise ValueError(f"could not decode frame for pHash: {path}")
    return result.stdout


def phash(gray_pixels):
    """Compute a dependency-free 64-bit pHash from a 32x32 grayscale image."""
    if len(gray_pixels) != 1024:
        raise ValueError("pHash input must contain exactly 32x32 grayscale pixels")
    cosine = [[math.cos((2 * x + 1) * u * math.pi / 64) for x in range(32)]
              for u in range(8)]
    coeffs = []
    for v in range(8):
        for u in range(8):
            total = 0.0
            row_cos = cosine[v]
            col_cos = cosine[u]
            for y in range(32):
                row_offset = y * 32
                cy = row_cos[y]
                for x in range(32):
                    total += gray_pixels[row_offset + x] * col_cos[x] * cy
            coeffs.append(total)
    median_pool = sorted(coeffs[1:])
    median = median_pool[len(median_pool) // 2]
    value = 0
    for coefficient in coeffs:
        value = (value << 1) | int(coefficient > median)
    return value


def hamming_distance(left, right):
    return (int(left) ^ int(right)).bit_count()


def select_candidates(candidates, max_frames=30, min_interval=3.0,
                      phash_distance=6):
    """Prefer scene changes, then static samples, while preserving chronology."""
    if max_frames <= 0:
        return []
    ranked = sorted(candidates,
                    key=lambda item: (0 if item["kind"] == "scene" else 1,
                                      item["timestamp"]))
    accepted = []
    for candidate in ranked:
        if any(abs(float(candidate["timestamp"]) - float(item["timestamp"]))
               < min_interval for item in accepted):
            continue
        if any(hamming_distance(candidate["hash"], item["hash"])
               <= phash_distance for item in accepted):
            continue
        accepted.append(candidate)
        if len(accepted) >= max_frames:
            break
    return sorted(accepted, key=lambda item: item["timestamp"])


def extract_keyframes(video_path, frames_dir, scene_threshold=0.3,
                      max_frames=30, min_interval=3.0,
                      fallback_interval=60.0, phash_distance=6):
    """Extract scene frames plus periodic static-scene samples with PTS times."""
    video_path = Path(video_path)
    frames_dir = Path(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)
    scale = ("scale=w='min(1280,iw)':h='min(720,ih)':"
             "force_original_aspect_ratio=decrease")
    with tempfile.TemporaryDirectory(prefix="bili-frames-") as temp:
        candidate_dir = Path(temp)
        scenes = _run_ffmpeg_candidates(
            video_path, candidate_dir,
            f"select='gt(scene,{scene_threshold})',{scale},showinfo", "scene")
        # Sampling always runs, so a static slide or code editor still yields
        # visual evidence even when the scene detector returns no frames.
        fallback = _run_ffmpeg_candidates(
            video_path, candidate_dir,
            f"select='isnan(prev_selected_t)+"
            f"gte(t-prev_selected_t,{fallback_interval})',{scale},showinfo",
            "fallback")
        candidates = scenes + fallback
        for item in candidates:
            item["hash"] = phash(_gray_32x32(item["path"]))
        chosen = select_candidates(candidates, max_frames, min_interval,
                                   phash_distance)
        output = []
        for index, item in enumerate(chosen, 1):
            destination = frames_dir / f"{index:04d}.jpg"
            shutil.copy2(item["path"], destination)
            output.append({
                "timestamp": round(float(item["timestamp"]), 3),
                "path": str(destination),
                "source": item["kind"],
                "phash": f"{item['hash']:016x}",
            })
    return {"frames": output, "candidate_count": len(candidates),
            "selected_count": len(output)}


def normalize_transcript(data):
    """Normalize official subtitle JSON, timed ASR JSON, or their segment list."""
    if isinstance(data, list):
        source = "unknown"
        raw_segments = data
    elif isinstance(data, dict):
        source = data.get("source", "unknown")
        raw_segments = (data.get("segments") or data.get("body") or [])
    else:
        raise ValueError("transcript JSON must be an object or list")
    normalized = []
    for segment in raw_segments:
        text = str(segment.get("text", segment.get("content", ""))).strip()
        if not text:
            continue
        start = segment.get("start", segment.get("from"))
        end = segment.get("end", segment.get("to", start))
        if start is None or end is None:
            raise ValueError("every transcript segment needs start and end timestamps")
        normalized.append({"start": float(start), "end": float(end), "text": text})
    normalized.sort(key=lambda item: (item["start"], item["end"]))
    metadata = ({key: value for key, value in data.items()
                 if key not in {"source", "segments", "body"}}
                if isinstance(data, dict) else {})
    return {**metadata, "source": source, "segments": normalized}


def normalize_plain_transcript(text):
    """Parse the existing [mm:ss] subtitle text format for experiment input."""
    records = []
    for line in text.splitlines():
        match = re.match(r"^\[(\d{2,}):(\d{2})\]\s*(.*)$", line.strip())
        if match and match.group(3):
            seconds = int(match.group(1)) * 60 + int(match.group(2))
            records.append({"start": float(seconds), "text": match.group(3)})
    for index, segment in enumerate(records):
        segment["end"] = records[index + 1]["start"] if index + 1 < len(records) else segment["start"] + 3
    return {"source": "timestamped_text", "segments": records}


def build_segments(transcript, frames, pause_seconds=5.0,
                   max_segment_seconds=90.0, max_segment_chars=1500):
    """Group nearby captions using pauses and paragraph punctuation."""
    captions = (transcript.get("segments", []) if isinstance(transcript, dict)
                else transcript)
    groups = []
    current = []
    for caption in captions:
        if not current:
            current = [caption]
            continue
        gap = float(caption["start"]) - float(current[-1]["end"])
        text_size = sum(len(item["text"]) for item in current)
        duration = float(current[-1]["end"]) - float(current[0]["start"])
        paragraph_break = (SENTENCE_END.search(current[-1]["text"].strip())
                           and duration >= 20)
        if (gap > pause_seconds or paragraph_break
                or duration >= max_segment_seconds
                or text_size >= max_segment_chars):
            groups.append(current)
            current = []
        current.append(caption)
    if current:
        groups.append(current)

    output = []
    for group in groups:
        start, end = float(group[0]["start"]), float(group[-1]["end"])
        output.append({
            "start": start,
            "end": end,
            "transcript": " ".join(item["text"] for item in group),
            "captions": group,
            "frames": [],
        })
    for frame in frames:
        timestamp = float(frame["timestamp"])
        if not output:
            continue
        # Frames within speech map directly; frames in a pause map to the
        # closest segment edge, making every retained frame auditable.
        closest = min(output, key=lambda segment: (
            0 if segment["start"] <= timestamp <= segment["end"] else
            min(abs(timestamp - segment["start"]), abs(timestamp - segment["end"]))
        ))
        closest["frames"].append(frame)
    return output


def serialize_whisper_segments(segments):
    """Return the legacy newline text and an additional timed JSON list."""
    plain_lines, timed = [], []
    for segment in segments:
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        plain_lines.append(text)
        timed.append({"start": float(segment["start"]),
                      "end": float(segment["end"]), "text": text})
    return "\n".join(plain_lines), timed


def write_whisper_transcripts(text_path, json_path, text, segments, metadata=None):
    """Write the unchanged legacy .txt and the additional timed JSON sidecar."""
    text_path, json_path = Path(text_path), Path(json_path)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text(text, encoding="utf-8")
    payload = {"source": "transcribed", "segments": segments}
    payload.update(metadata or {})
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")


def _download_video(url, download_dir, max_download_mb=500):
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("yt-dlp is required to download the optional video") from exc
    output = str(Path(download_dir) / "video.%(ext)s")
    options = {
        "format": "bestvideo[height<=720]+bestaudio/best[height<=720]",
        "outtmpl": output,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "max_filesize": int(max_download_mb * 1024 * 1024),
    }
    headers = cookie_header()
    if headers:
        options["http_headers"] = {**headers, "Referer": "https://www.bilibili.com"}
    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.extract_info(url, download=True)
    files = [path for path in Path(download_dir).glob("video.*")
             if path.is_file()]
    if not files:
        raise RuntimeError("video download completed without a video file")
    return files[0]


def _write_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")


def prepare_experiment(transcript_path, video_path=None, video_url=None,
                       output_dir=DEFAULT_EXPERIMENT_DIR, scene_threshold=0.3,
                       max_frames=30, min_interval=3.0,
                       fallback_interval=60.0, pause_seconds=5.0,
                       max_download_mb=500):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = output_dir / "frames"
    frames_dir.mkdir(exist_ok=True)
    for old_frame in frames_dir.glob("[0-9][0-9][0-9][0-9].jpg"):
        old_frame.unlink()
    transcript_path = Path(transcript_path)
    if transcript_path.suffix.lower() == ".json":
        transcript = normalize_transcript(json.loads(transcript_path.read_text(encoding="utf-8")))
    else:
        transcript = normalize_plain_transcript(transcript_path.read_text(encoding="utf-8"))
    _write_json(output_dir / "transcript.json", transcript)

    config = {
        "visual_status": "await_video_or_subtitle_only",
        "visual_enabled": bool(video_path or video_url),
        "scene_threshold": scene_threshold,
        "max_frames": max_frames,
        "min_interval_seconds": min_interval,
        "fallback_interval_seconds": fallback_interval,
        "pause_seconds": pause_seconds,
        "max_download_mb": max_download_mb,
        "max_video_height": 720,
        "vision_model_call": "external Skill runner; must support local image input",
    }
    frame_result = {"frames": [], "candidate_count": 0, "selected_count": 0}
    video_context = tempfile.TemporaryDirectory(prefix="bili-video-") if video_url else None
    try:
        if video_url:
            video_path = _download_video(video_url, video_context.name, max_download_mb)
        if video_path:
            frame_result = extract_keyframes(
                video_path, frames_dir, scene_threshold, max_frames,
                min_interval, fallback_interval)
            config["visual_status"] = "frames_ready"
    except Exception as exc:
        config["video_error"] = str(exc)
        config["visual_status"] = "video_unavailable_fallback"
        config["visual_enabled"] = False
        for partial_frame in frames_dir.glob("[0-9][0-9][0-9][0-9].jpg"):
            partial_frame.unlink()
        # The same transcript still produces the subtitle-only path.
    finally:
        if video_context:
            video_context.cleanup()
    segments = build_segments(transcript, frame_result["frames"], pause_seconds)
    _write_json(output_dir / "config.json", config)
    _write_json(output_dir / "segments.json", segments)
    (output_dir / "summary_text.md").write_text(
        "# A：字幕总结\n\n待使用本实验的 `transcript.json` 生成。\n",
        encoding="utf-8")
    (output_dir / "summary_multimodal.md").write_text(
        "# B：字幕 + 关键帧总结\n\n待使用相同字幕，并逐张向支持视觉输入的模型提供 `frames/` 中的图片后生成。\n",
        encoding="utf-8")
    metrics = {
        "a": {"tokens": None, "elapsed_seconds": None},
        "b": {"tokens": None, "elapsed_seconds": None},
        "status": "pending_model_runs",
    }
    _write_json(output_dir / "metrics.json", metrics)
    (output_dir / "comparison_report.md").write_text(
        "# 多模态 A/B 对照实验报告\n\n"
        "状态：待真实视频与两次同模型总结运行。当前目录仅包含实验输入与模板。\n\n"
        "## 盲评\n\n"
        "评分人看到随机标记的两份总结，不看到 A/B 模式。每项 1–5 分，"
        "并给出可回看的原视频时间戳作为依据。\n\n"
        "| 维度 | 总结 X | 总结 Y | 依据/时间戳 |\n|---|---:|---:|---|\n"
        "| 信息完整性 | 待评 | 待评 | 待评 |\n"
        "| 技术准确性 | 待评 | 待评 | 待评 |\n"
        "| 视觉信息增益 | 待评 | 待评 | 待评 |\n"
        "| 无依据细节（反向评分） | 待评 | 待评 | 待评 |\n\n"
        "## 对照问题\n\n"
        "1. 关键帧是否提供了字幕之外的有效信息？待评估。\n"
        "2. 哪些知识点只有加入画面才能准确理解？待评估。\n"
        "3. 是否出现了冗余截图？待评估；筛选已启用 pHash 与最小时间间隔。\n"
        "4. 是否遗漏了重要的代码、公式或图表？待对照原视频。\n"
        "5. 多模态总结的成本增加了多少？待记录模型实际 token 与耗时。\n"
        "6. 是否值得将视觉功能加入正式 Skill？待实验结论。\n"
        "7. 下一步采样方式：本轮以场景检测为主、固定时间采样补静态画面；"
        "依据结果再比较内容感知采样。\n\n"
        "## 可追溯差异\n\n待填写：差异点、字幕是否提及、关键帧时间戳、原视频核验结果。\n",
        encoding="utf-8")
    return {"output_dir": str(output_dir), "config": config,
            "frame_result": frame_result, "segment_count": len(segments)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="准备 Bilibili 字幕 + 关键帧实验")
    parser.add_argument("--transcript", required=True,
                        help="官方字幕/ASR 的时间戳 JSON，或带 [mm:ss] 的旧 TXT")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--video-file", help="本地视频文件")
    source.add_argument("--video-url", help="用户提供的视频链接；限高 720p、默认 500 MB 上限")
    parser.add_argument("--output", default=str(DEFAULT_EXPERIMENT_DIR))
    parser.add_argument("--scene-threshold", type=float, default=0.3)
    parser.add_argument("--max-frames", type=int, default=30)
    parser.add_argument("--min-interval", type=float, default=3.0)
    parser.add_argument("--fallback-interval", type=float, default=60.0)
    parser.add_argument("--pause-seconds", type=float, default=5.0)
    parser.add_argument("--max-download-mb", type=int, default=500)
    args = parser.parse_args(argv)
    result = prepare_experiment(
        args.transcript, args.video_file, args.video_url, args.output,
        args.scene_threshold, args.max_frames, args.min_interval,
        args.fallback_interval, args.pause_seconds, args.max_download_mb)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

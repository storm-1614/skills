#!/usr/bin/env python3
"""bili_fetch.py — 抓取 B站官方字幕（纯标准库）。

流程：URL → b23 解析 → BV/av 提取 → view 接口(标题/cid/时长) →
      nav 接口(wbi keys) → 签名 player/wbi/v2(字幕列表) → 选轨道 →
      下载字幕 JSON → 转纯文本 → 风控自检 → 写 output/transcripts/<bvid>.txt。

所有输出为 JSON 到 stdout（含错误），退出码仅作辅助。
"""
import json
import os
import re
import sys
import urllib.parse

from bili_common import (
    BiliError, PROJECT_DIR, TRANSCRIPTS_DIR,
    cookie_header, err, fmt_ts, get_bytes, get_json, get_wbi_keys, has_sessdata,
    parse_page, parse_video_id, resolve_b23, slug, wbi_sign,
)
from bili_auth import ensure_session_fresh, login_status

# 轨道选择优先级：CC 中文 > 其它中文 > CC 英文 > AI 英文
PRIORITY = {
    "zh-Hans": 5, "zh-CN": 5, "zh-Hant": 4, "zh-TW": 4, "zh": 4,
    "ai-zh": 3, "zh-Hans-D": 3,
    "en": 2, "en-US": 2, "en-UK": 2, "ai-en": 1,
}


# ---------------------------------------------------------------- 数据层

def view_info(bvid, aid):
    """/x/web-interface/view → 标题/UP主/时长/cid。"""
    params = {"bvid": bvid} if bvid else {"aid": aid}
    url = "https://api.bilibili.com/x/web-interface/view?" + urllib.parse.urlencode(params)
    d = get_json(url)
    if d.get("code") != 0:
        raise BiliError("view_api", f"view 接口返回 code={d.get('code')}: {d.get('message', '')}", exit_code=3)
    data = d.get("data") or {}
    return {
        "bvid": data.get("bvid", bvid or ""),
        "aid": data.get("aid", 0),
        "title": data.get("title", ""),
        "author": (data.get("owner") or {}).get("name", ""),
        "duration": data.get("duration", 0),
        "pages": data.get("pages") or [],
    }


def subtitle_list(bvid, aid, cid, headers):
    """签名请求 /x/player/wbi/v2 → 字幕轨道列表。空则返回 []。"""
    params = {}
    if bvid:
        params["bvid"] = bvid
    else:
        params["aid"] = aid
    params["cid"] = cid
    img_key, sub_key = get_wbi_keys()
    signed = wbi_sign(params, img_key, sub_key)
    url = "https://api.bilibili.com/x/player/wbi/v2?" + urllib.parse.urlencode(signed)
    d = get_json(url, headers=headers)
    if d.get("code") != 0:
        raise BiliError("player_api", f"player/wbi/v2 返回 code={d.get('code')}: {d.get('message', '')}", exit_code=5)
    subtitle = (d.get("data") or {}).get("subtitle") or {}
    return subtitle.get("subtitles") or []


def _read_varint(data, offset):
    value = 0
    shift = 0
    while offset < len(data) and shift < 70:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7
    raise ValueError("invalid protobuf varint")


def _protobuf_fields(data):
    """将 protobuf wire 字段解码为 field number → repeated values。"""
    fields = {}
    offset = 0
    while offset < len(data):
        tag, offset = _read_varint(data, offset)
        number, wire = tag >> 3, tag & 7
        if not number:
            raise ValueError("invalid protobuf field number")
        if wire == 0:
            value, offset = _read_varint(data, offset)
        elif wire == 1:
            if offset + 8 > len(data):
                raise ValueError("truncated protobuf fixed64")
            value, offset = data[offset:offset + 8], offset + 8
        elif wire == 2:
            size, offset = _read_varint(data, offset)
            if size > len(data) - offset:
                raise ValueError("truncated protobuf bytes")
            value, offset = data[offset:offset + size], offset + size
        elif wire == 5:
            if offset + 4 > len(data):
                raise ValueError("truncated protobuf fixed32")
            value, offset = data[offset:offset + 4], offset + 4
        else:
            raise ValueError("unsupported protobuf wire type")
        fields.setdefault(number, []).append(value)
    return fields


def _protobuf_text(fields, number):
    values = fields.get(number) or []
    if not values or not isinstance(values[0], bytes):
        return ""
    return values[0].decode("utf-8", errors="replace")


def subtitle_list_protobuf(aid, cid, headers):
    """新版 /x/v2/subtitle/web/view → Protobuf 字幕轨道列表。"""
    params = urllib.parse.urlencode({
        "oid": cid,
        "pid": aid,
        "context_ext": json.dumps({"video_type": 1}, separators=(",", ":")),
        "type": "1",
        "cur_production_type": "0",
        "preferred_language": "ai-zh",
        "playlist_switch": "0",
    })
    url = "https://api.bilibili.com/x/v2/subtitle/web/view?" + params
    request_headers = dict(headers or {})
    request_headers["Accept"] = "application/octet-stream"
    raw = get_bytes(url, headers=request_headers)
    # B 站在参数不完整或请求被拒时仍以 HTTP 200 返回 JSON 错误；不要把它
    # 当作损坏的 Protobuf，保留服务端错误码供上层诊断。
    if raw.lstrip().startswith(b"{"):
        try:
            response = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            response = {}
        if response:
            raise BiliError(
                "subtitle_api",
                f"subtitle/web/view 返回 code={response.get('code')}: {response.get('message', '')}",
                exit_code=5,
            )
    try:
        envelope = _protobuf_fields(raw)
        data_message = next((v for v in envelope.get(1, []) if isinstance(v, bytes)), None)
        if data_message is None:
            return []
        data_fields = _protobuf_fields(data_message)
        tracks = []
        for raw_track in data_fields.get(3, []):
            if not isinstance(raw_track, bytes):
                continue
            track_fields = _protobuf_fields(raw_track)
            lan = _protobuf_text(track_fields, 3)
            tracks.append({
                "lan": lan,
                "lan_doc": _protobuf_text(track_fields, 4),
                "subtitle_url": _protobuf_text(track_fields, 5),
                "ai_type": 1 if lan.startswith("ai-") or lan.endswith("-D") else 0,
            })
        return tracks
    except (ValueError, StopIteration) as exc:
        raise BiliError(
            "subtitle_protobuf", "无法解析 B 站新版字幕轨道响应", exit_code=5
        ) from exc


def pick_track(subs):
    """按优先级挑选最佳字幕轨道。"""
    def score(s):
        lan = (s.get("lan") or "")
        if s.get("ai_type") == 1 or lan.startswith("ai-") or lan.endswith("-D"):
            return 3 if lan in ("ai-zh", "zh-Hans-D") else 1
        return PRIORITY.get(lan, 3 if lan.startswith("zh") else (2 if lan.startswith("en") else 0))
    return max(subs, key=score) if subs else None


def download_subtitle(track):
    """下载字幕正文 JSON，返回 body 列表 [{from,to,content},...]。"""
    url = track.get("subtitle_url") or ""
    if not url:
        return []
    if url.startswith("//"):
        url = "https:" + url
    elif not url.startswith("http"):
        url = "https://" + url
    d = get_json(url)
    return d.get("body") or []


def body_to_text(body, timestamps=False):
    """body → 纯文本（可选保留 [mm:ss] 时间戳）。"""
    out = []
    for b in body:
        c = str(b.get("content") or "").strip()
        if not c:
            continue
        if timestamps:
            c = f"[{fmt_ts(b.get('from', 0))}] {c}"
        out.append(c)
    return "\n".join(out)


def risk_flags(body, duration, lan):
    """风控/假字幕自检（张冠李戴检测）。返回风险原因列表。"""
    risks = []
    if len(body) < 3:
        risks.append("segment_count_too_low")
    if body:
        last = body[-1].get("to", 0) or 0
        if duration and last > duration + 90:
            risks.append("timestamps_exceed_duration")
        if duration and last < min(15, duration * 0.1):
            risks.append("coverage_too_short")
    texts = [str(b.get("content") or "") for b in body]
    if texts:
        avg = sum(len(t) for t in texts) / len(texts)
        if avg < 2.5:
            risks.append("avg_segment_too_short")
        if lan.startswith("zh") or lan == "ai-zh":
            cjk = sum(1 for t in texts if re.search(r"[一-鿿]", t))
            if cjk / len(texts) < 0.5:
                risks.append("zh_track_no_cjk")
    return risks


# ---------------------------------------------------------------- 主流程

def run():
    if len(sys.argv) < 2:
        err("usage", "用法: python3 bili_fetch.py \"<B站视频链接>\"", exit_code=2)
    raw = sys.argv[1]

    # 1) 解析链接
    url = resolve_b23(raw)
    bvid, aid = parse_video_id(url)
    if not bvid and aid is None:
        err("url_parse_failed", "无法解析链接，支持 BV号 / av号 / b23.tv 短链", exit_code=2)
    page = parse_page(raw)

    # 2) view 接口
    info = view_info(bvid, aid)
    pages = info["pages"]
    if pages:
        p = min(max(page, 1), len(pages)) - 1
        cid = pages[p].get("cid", info.get("cid"))
        if page > 1:
            info["bvid"] = info.get("bvid") or ""
    else:
        cid = info.get("cid", 0)
    if not cid:
        err("view_api", "view 接口未返回 cid", exit_code=3)

    # 3) 字幕列表（有 cookie 就带）
    # 持久化 QR 会话按 B 站 cookie/info 提示续期；旧 .sessdata 继续兼容。
    ensure_session_fresh()
    headers = cookie_header()
    subs = subtitle_list(bvid, aid, cid, headers)
    has_chinese_track = any(
        (track.get("lan") or "").startswith("zh")
        or (track.get("lan") or "").startswith("ai-zh")
        for track in subs
    )
    if not has_chinese_track:
        newer_tracks = subtitle_list_protobuf(info.get("aid") or aid, cid, headers)
        known_tracks = {track.get("lan") for track in subs}
        subs.extend(track for track in newer_tracks if track.get("lan") not in known_tracks)

    if not subs:
        if not headers and not has_sessdata():
            err(
                "no_subtitle",
                "该视频的官方字幕接口需要登录态；运行 bili_login.py 扫码登录后重试",
                exit_code=6,
                needs_cookie=True,
            )
        if headers and not login_status():
            err(
                "no_subtitle",
                "B 站未识别当前登录态；请运行 bili_login.py 扫码登录后重试",
                exit_code=6,
                needs_cookie=True,
            )
        err("no_subtitle", "已登录，但官方字幕接口未返回可用轨道；按 skill 流程询问后再走 Whisper 兜底", exit_code=6)

    track = pick_track(subs)
    if not track:
        err("no_subtitle", "字幕列表为空；按 skill 流程询问后再走 Whisper 兜底", exit_code=6)

    # 4) 下载字幕正文
    body = download_subtitle(track)
    if not body:
        raise BiliError("subtitle_empty", "字幕正文为空，建议走 whisper 转写兜底", exit_code=5)

    # 5) 风控自检
    lan = (track.get("lan") or "")
    risks = risk_flags(body, info["duration"], lan)

    # 6) 写纯文本文件
    os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)
    name = slug(info.get("bvid") or bvid, info.get("aid") or aid)
    if page > 1:
        name += f"_p{page}"
    text = body_to_text(body, timestamps=True)
    text_file = os.path.join("output", "transcripts", name + ".txt")
    abs_path = os.path.join(PROJECT_DIR, text_file)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(text)
    transcript_json = os.path.join("output", "transcripts", name + ".json")
    with open(os.path.join(PROJECT_DIR, transcript_json), "w", encoding="utf-8") as f:
        json.dump({
            "source": "official",
            "bvid": info.get("bvid") or bvid,
            "aid": info.get("aid") or aid,
            "cid": cid,
            "title": info["title"],
            "author": info["author"],
            "duration": info["duration"],
            "subtitle_lan": lan,
            "segments": [
                {"start": float(item.get("from", 0) or 0),
                 "end": float(item.get("to", item.get("from", 0)) or 0),
                 "text": str(item.get("content") or "").strip()}
                for item in body if str(item.get("content") or "").strip()
            ],
        }, f, ensure_ascii=False, indent=2)

    # 7) 输出契约
    out = {
        "status": "ok",
        "source": "official",
        "bvid": info.get("bvid") or bvid,
        "aid": info.get("aid") or aid,
        "cid": cid,
        "title": info["title"],
        "author": info["author"],
        "duration": info["duration"],
        "page": page,
        "subtitle_lan": lan,
        "subtitle_kind": "ai" if "ai" in lan or track.get("ai_type") == 1 else "cc",
        "text_file": text_file,
        "transcript_json": transcript_json,
        "char_count": len(text),
        "line_count": len(body),
        "risk_flag": bool(risks),
        "risk_reasons": risks,
        "message": "",
    }
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(0)


def main():
    """统一捕获：任何异常都输出 JSON 错误契约，绝不打印 traceback。"""
    try:
        run()
    except BiliError as e:
        err(e.error, e.hint, exit_code=e.exit_code)
    except Exception as e:
        err("unexpected", f"未预期异常: {e.__class__.__name__}: {e}", exit_code=1)


if __name__ == "__main__":
    main()

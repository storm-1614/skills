#!/usr/bin/env python3
"""bili_common.py — B站视频总结 skill 共享库（纯标准库）。

负责 URL 解析（BV/av/b23.tv）、Wbi 签名、HTTP 请求、cookie 读取。
供 bili_fetch.py / bili_transcribe.py 复用。不依赖任何第三方包。
"""
import hashlib
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HDRS = {"User-Agent": UA, "Referer": "https://www.bilibili.com"}

# Wbi 签名用的 64 位乱序表（bilibili 前端公开常量）
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPTS_DIR)
# Default generated files to the caller's working directory so the skill also
# works outside a particular repository layout. Set BILI_VIDEO_SUMMARY_HOME to
# choose another workspace explicitly.
PROJECT_DIR = os.path.abspath(
    os.environ.get("BILI_VIDEO_SUMMARY_HOME") or os.getcwd()
)
TRANSCRIPTS_DIR = os.path.join(PROJECT_DIR, "output", "transcripts")
AUTH_DIR = os.path.join(os.path.expanduser("~"), ".config", "bili-video-summary")
AUTH_FILE = os.path.join(AUTH_DIR, "credentials.json")
COOKIE_NAMES = {
    "SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid",
    "buvid3", "buvid4",
}


class BiliError(Exception):
    """带错误码与退出码的业务异常，由各脚本 main 统一捕获并输出 JSON。"""

    def __init__(self, error, hint="", exit_code=7):
        super().__init__(error)
        self.error = error
        self.hint = hint
        self.exit_code = exit_code


def out_json(obj, exit_code=0):
    """打印 JSON 到 stdout（所有脚本统一的输出契约）。"""
    print(json.dumps(obj, ensure_ascii=False))
    sys.exit(exit_code)


def err(error, hint="", exit_code=7, **extra):
    obj = {"status": "error", "error": error, "hint": hint}
    obj.update(extra)
    out_json(obj, exit_code=exit_code)


# ---------------------------------------------------------------- cookie

def load_auth_state():
    """读取用户级登录状态；凭据文件内容绝不打印。"""
    try:
        with open(AUTH_FILE, encoding="utf-8") as f:
            state = json.load(f)
        cookies = state.get("cookies") or {}
        if not isinstance(cookies, dict):
            return {}
        return {
            "cookies": {k: str(v) for k, v in cookies.items()
                        if k in COOKIE_NAMES and v},
            "refresh_token": str(state.get("refresh_token") or ""),
        }
    except (OSError, ValueError, TypeError):
        return {}


def save_auth_state(state):
    """以仅当前用户可读的权限原子保存登录 Cookie 和刷新令牌。"""
    pathlib.Path(AUTH_DIR).mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(AUTH_DIR, 0o700)
    except OSError:
        pass
    temp_path = AUTH_FILE + ".tmp"
    fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({
            "cookies": {k: str(v) for k, v in (state.get("cookies") or {}).items()
                        if k in COOKIE_NAMES and v},
            "refresh_token": str(state.get("refresh_token") or ""),
        }, f, ensure_ascii=False)
        f.write("\n")
    os.replace(temp_path, AUTH_FILE)
    try:
        os.chmod(AUTH_FILE, 0o600)
    except OSError:
        pass


def load_cookies():
    """优先使用持久扫码会话，再兼容 BILI_SESSDATA 与项目 .sessdata。"""
    state = load_auth_state()
    if state.get("cookies", {}).get("SESSDATA"):
        return state["cookies"]
    sessdata = os.environ.get("BILI_SESSDATA", "").strip()
    if sessdata:
        return {"SESSDATA": sessdata}
    try:
        with open(os.path.join(PROJECT_DIR, ".sessdata"), encoding="utf-8") as f:
            sessdata = f.read().strip()
    except OSError:
        sessdata = ""
    return {"SESSDATA": sessdata} if sessdata else {}

def load_sessdata():
    """返回 SESSDATA 值，供 yt-dlp 等只支持该 Cookie 的工具使用。"""
    return load_cookies().get("SESSDATA", "")


def cookie_header():
    cookies = load_cookies()
    return {"Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())} if cookies else {}


def has_sessdata():
    return bool(load_sessdata())


# ---------------------------------------------------------------- HTTP

def get_json(url, headers=None, timeout=20, retries=3):
    """GET 并解析 JSON。网络/解析失败抛 BiliError。

    429/5xx 自动退避重试（B站风控常见），每次间隔递增。
    """
    h = dict(HDRS)
    if headers:
        h.update(headers)
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(3 * (attempt + 1))
                continue
            raise BiliError("http_error", f"HTTP {e.code} {e.reason}", exit_code=7)
        except urllib.error.URLError as e:
            last = BiliError("network", f"网络错误: {e.reason}", exit_code=7)
        except (ValueError, json.JSONDecodeError) as e:
            raise BiliError("bad_json", f"响应不是合法 JSON: {e}", exit_code=7)
        if last is not None and attempt < retries - 1:
            time.sleep(2 * (attempt + 1))
            continue
        if last is not None:
            raise last
    raise BiliError("network", "请求多次失败", exit_code=7)


def get_bytes(url, headers=None, timeout=20):
    """GET 二进制响应（新版播放器字幕元数据为 Protobuf）。"""
    request_headers = dict(HDRS)
    if headers:
        request_headers.update(headers)
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=request_headers)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            raise BiliError("http_error", f"HTTP {e.code} {e.reason}", exit_code=7)
        except urllib.error.URLError as e:
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise BiliError("network", f"网络错误: {e.reason}", exit_code=7)


# ---------------------------------------------------------------- URL

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """拦截 302，供 b23.tv 短链解析读取 Location。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def resolve_b23(url):
    """b23.tv 短链 → 真实链接（拦截 302 读 Location）。非短链原样返回。"""
    if "b23.tv" not in url:
        return url
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(urllib.request.Request(url, headers=HDRS), timeout=15) as r:
            return r.headers.get("Location") or r.geturl()
    except urllib.error.HTTPError as e:  # 302 以 HTTPError 形式抛出
        return e.headers.get("Location") or url
    except Exception:
        return url


def parse_video_id(raw):
    """返回 (bvid or None, aid or None)。支持 bvid= / 裸 BV / aid= / av\\d+。"""
    m = re.search(r"bvid=(BV[0-9A-Za-z]{10})", raw)
    if m:
        return m.group(1), None
    m = re.search(r"\bBV[0-9A-Za-z]{10}\b", raw)
    if m:
        return m.group(0), None
    m = re.search(r"aid=(\d+)", raw)
    if m:
        return None, int(m.group(1))
    m = re.search(r"\bav(\d+)\b", raw, re.I)
    if m:
        return None, int(m.group(1))
    return None, None


def parse_page(raw):
    """解析多 P 参数 p=N，默认 1。"""
    m = re.search(r"[?&]p=(\d+)", raw)
    return int(m.group(1)) if m else 1


# ---------------------------------------------------------------- Wbi 签名

def get_wbi_keys():
    """从 /x/web-interface/nav 取 img_key/sub_key（匿名 code=-101 也返回 wbi_img）。"""
    d = get_json("https://api.bilibili.com/x/web-interface/nav")
    try:
        w = d["data"]["wbi_img"]
    except (KeyError, TypeError):
        raise BiliError("wbi_keys", "nav 接口未返回 wbi_img", exit_code=4)

    def key(u):
        return u.rsplit("/", 1)[1].split(".")[0]  # 文件名去扩展名

    return key(w["img_url"]), key(w["sub_url"])


def wbi_sign(params, img_key, sub_key):
    """按 Wbi 规则给 params 加 wts + w_rid，返回新 dict。"""
    filtered = {}
    for k, v in params.items():
        # 过滤 Wbi 规则中的特殊字符
        filtered[k] = re.sub(r'[!"\'()*+,-./:;<=>?@\[\\\]^`{|}~\s]', "", str(v))
    filtered = dict(sorted(filtered.items()))
    filtered["wts"] = int(time.time())
    filtered = dict(sorted(filtered.items()))
    mixin = "".join((img_key + sub_key)[i] for i in MIXIN_KEY_ENC_TAB[:32])
    q = urllib.parse.urlencode(filtered)
    filtered["w_rid"] = hashlib.md5((q + mixin).encode()).hexdigest()
    return filtered


# ---------------------------------------------------------------- 工具

def fmt_ts(sec):
    """秒 → mm:ss 或 h:mm:ss。"""
    sec = int(sec or 0)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def slug(bvid, aid):
    """生成安全文件名前缀。"""
    return bvid if bvid else f"av{aid}"

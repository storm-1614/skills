#!/usr/bin/env python3
"""Bilibili Web QR login and renewable user-level credentials."""
import base64
import hashlib
import http.cookiejar
import json
import os
import re
import secrets
import struct
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zlib

from bili_common import (
    AUTH_FILE, BiliError, COOKIE_NAMES, cookie_header, get_json, load_auth_state,
    save_auth_state,
)

LOGIN_BASE = "https://passport.bilibili.com/x/passport-login/web"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
    "Accept": "application/json, text/plain, */*",
}


def _cookiejar(cookies=None):
    jar = http.cookiejar.CookieJar()
    for name, value in (cookies or {}).items():
        if name not in COOKIE_NAMES or not value:
            continue
        jar.set_cookie(http.cookiejar.Cookie(
            0, name, str(value), None, False, ".bilibili.com", True, True,
            "/", True, False, None, True, None, None, {}, False,
        ))
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = list(HEADERS.items())
    return opener, jar


def _cookies_from_jar(jar):
    return {cookie.name: cookie.value for cookie in jar
            if cookie.name in COOKIE_NAMES and cookie.value}


def _open(opener, url, data=None, headers=None):
    request = urllib.request.Request(url, data=data, headers=headers or {})
    with opener.open(request, timeout=25) as response:
        body = response.read()
        if (response.headers.get("Content-Encoding") or "").lower() == "gzip":
            body = zlib.decompress(body, 16 + zlib.MAX_WBITS)
        return body, response.headers


def _json_request(opener, url, data=None):
    raw, _ = _open(opener, url, data=data)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise BiliError("auth_response", "B站登录接口返回了无法解析的响应", 7) from exc


def _mgf1(seed, length):
    out = bytearray()
    for counter in range((length + 31) // 32):
        out.extend(hashlib.sha256(seed + counter.to_bytes(4, "big")).digest())
    return bytes(out[:length])


def _correspond_path(timestamp):
    """使用 B 站 Web 端公布的 RSA-OAEP-SHA256 公钥生成刷新路径。"""
    modulus = "y4HdjgJHBlbaBN04VERG4qNBIFHP6a3GozCl75AihQloSWCXC5HDNgyinEnhaQ_4-gaMud_GF50elYXLlCToR9se9Z8z433U3KjM-3Yx7ptKkmQNAMggQwAVKgq3zYAoidNEWuxpkY_mAitTSRLnsJW-NCTa0bqBFF6Wm1MxgfE"
    n = int.from_bytes(base64.urlsafe_b64decode(modulus + "=" * (-len(modulus) % 4)), "big")
    e = 65537
    message = f"refresh_{timestamp}".encode()
    hlen = 32
    key_len = (n.bit_length() + 7) // 8
    if len(message) > key_len - 2 * hlen - 2:
        raise BiliError("refresh_crypto", "刷新路径输入长度超出限制", 7)
    digest = hashlib.sha256(b"").digest()
    data_block = digest + b"\x00" * (key_len - len(message) - 2 * hlen - 2) + b"\x01" + message
    seed = secrets.token_bytes(hlen)
    masked_data = bytes(a ^ b for a, b in zip(data_block, _mgf1(seed, key_len - hlen - 1)))
    masked_seed = bytes(a ^ b for a, b in zip(seed, _mgf1(masked_data, hlen)))
    encoded = b"\x00" + masked_seed + masked_data
    ciphertext = pow(int.from_bytes(encoded, "big"), e, n).to_bytes(key_len, "big")
    return ciphertext.hex()


def _install_query_cookies(url, jar):
    """兼容旧版二维码回调把 Cookie 放在成功 URL 查询参数中的格式。"""
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    for name in COOKIE_NAMES:
        values = query.get(name)
        if values and values[0]:
            jar.set_cookie(http.cookiejar.Cookie(
                0, name, values[0], None, False, ".bilibili.com", True, True,
                "/", True, False, None, True, None, None, {}, False,
            ))


def _write_qr_png(matrix, path, scale=8):
    """Render a QR module matrix as a small RGB PNG using only stdlib."""
    height = len(matrix) * scale
    width = len(matrix[0]) * scale
    black = b"\x00\x00\x00"
    white = b"\xff\xff\xff"
    rows = []
    for modules in matrix:
        row = b"\x00" + b"".join((black if cell else white) * scale for cell in modules)
        rows.extend([row] * scale)

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(b"".join(rows)))
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


def qr_login(timeout=300, poll_interval=2):
    try:
        import qrcode
    except ImportError as exc:
        raise BiliError(
            "missing_qrcode", "终端二维码依赖缺失，请运行 .venv/bin/pip install qrcode", 3
        ) from exc

    fd, qr_path = tempfile.mkstemp(prefix="bili-login-", suffix=".png")
    os.fchmod(fd, 0o600)
    os.close(fd)
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            opener, jar = _cookiejar()
            generated = _json_request(
                opener, LOGIN_BASE + "/qrcode/generate?source=main-fe-header"
            )
            if generated.get("code") != 0:
                raise BiliError("qr_generate", "申请 B 站登录二维码失败", 5)
            payload = generated.get("data") or {}
            key = payload.get("qrcode_key")
            login_url = payload.get("url")
            if not key or not login_url:
                raise BiliError("qr_generate", "二维码接口未返回登录信息", 5)

            print("请使用 B 站手机 App 扫描下方二维码并确认登录：", file=sys.stderr, flush=True)
            qr = qrcode.QRCode(border=4)
            qr.add_data(login_url)
            qr.make(fit=True)
            qr.print_ascii(out=sys.stderr, tty=False)
            _write_qr_png(qr.get_matrix(), qr_path)
            print(f"二维码图片：{qr_path}", file=sys.stderr, flush=True)

            while time.monotonic() < deadline:
                query = urllib.parse.urlencode({"qrcode_key": key, "source": "main-fe-header"})
                result = _json_request(opener, LOGIN_BASE + "/qrcode/poll?" + query)
                poll = result.get("data") or {}
                code = poll.get("code", result.get("code"))
                if code == 0:
                    callback = poll.get("url")
                    refresh_token = str(poll.get("refresh_token") or "")
                    if callback:
                        _install_query_cookies(callback, jar)
                        # New web QR flow returns a cross-domain ticket. Follow it now:
                        # the actual cookies are delivered by Set-Cookie on that response.
                        _open(opener, callback)
                    cookies = _cookies_from_jar(jar)
                    if not cookies.get("SESSDATA"):
                        raise BiliError(
                            "qr_cookie_missing",
                            "扫码已确认，但 B 站没有下发有效 Cookie；请重新运行扫码登录",
                            5,
                        )
                    if not refresh_token:
                        raise BiliError(
                            "qr_refresh_token_missing",
                            "扫码登录成功但未返回持久化刷新令牌；请重新登录或稍后重试",
                            5,
                        )
                    save_auth_state({"cookies": cookies, "refresh_token": refresh_token})
                    return {"status": "ok", "authenticated": True, "credential_file": AUTH_FILE}
                if code == 86038:
                    print("二维码已过期，正在换发新二维码……", file=sys.stderr, flush=True)
                    break
                if code not in (86101, 86090):
                    raise BiliError("qr_poll", f"B站扫码状态异常（code={code}）", 5)
                time.sleep(min(poll_interval, max(0, deadline - time.monotonic())))
        raise BiliError("qr_timeout", "等待扫码超时，请重新运行扫码登录", 5)
    finally:
        try:
            os.remove(qr_path)
        except OSError:
            pass


def _post_form(opener, url, fields):
    body = urllib.parse.urlencode(fields).encode()
    request_headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://www.bilibili.com/",
    }
    raw, _ = _open(opener, url, data=body, headers=request_headers)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise BiliError("refresh_response", "B站 Cookie 刷新接口返回异常", 7) from exc


def ensure_session_fresh():
    """使用持久扫码会话的 refresh_token 续期；旧 .sessdata 仍只读兼容。"""
    state = load_auth_state()
    cookies = state.get("cookies") or {}
    refresh_token = state.get("refresh_token") or ""
    if not cookies.get("SESSDATA") or not refresh_token:
        return False

    opener, jar = _cookiejar(cookies)
    csrf = cookies.get("bili_jct", "")
    info_query = urllib.parse.urlencode({"csrf": csrf})
    info = _json_request(
        opener,
        "https://passport.bilibili.com/x/passport-login/web/cookie/info?" + info_query,
    )
    if info.get("code") == -101:
        raise BiliError("session_expired", "B 站登录已失效，请重新运行 bili_login.py 扫码", 6)
    if info.get("code") != 0:
        raise BiliError("refresh_check", "无法确认 B 站登录是否需要续期", 7)
    details = info.get("data") or {}
    if not details.get("refresh"):
        return False

    timestamp = details.get("timestamp") or int(time.time() * 1000)
    path = _correspond_path(timestamp)
    page, _ = _open(opener, "https://www.bilibili.com/correspond/1/" + path)
    match = re.search(rb'<div\s+id=["\']1-name["\']>([^<]+)</div>', page)
    if not match:
        raise BiliError("refresh_csrf", "B 站未返回 Cookie 续期校验码", 7)
    refresh_csrf = match.group(1).decode("utf-8")

    previous_token = refresh_token
    refreshed = _post_form(
        opener,
        LOGIN_BASE + "/cookie/refresh",
        {
            "csrf": csrf,
            "refresh_csrf": refresh_csrf,
            "source": "main_web",
            "refresh_token": previous_token,
        },
    )
    if refreshed.get("code") != 0:
        raise BiliError("cookie_refresh", "B 站 Cookie 续期失败，请重新扫码登录", 6)
    next_token = str((refreshed.get("data") or {}).get("refresh_token") or "")
    next_cookies = _cookies_from_jar(jar)
    if not next_cookies.get("SESSDATA") or not next_token:
        raise BiliError("cookie_refresh", "续期响应未包含完整登录凭据，请重新扫码登录", 6)

    # Save the rotated token and cookies before confirmation so an interrupted
    # process does not lose the only usable credential set.
    save_auth_state({"cookies": next_cookies, "refresh_token": next_token})
    confirmed = _post_form(
        opener,
        LOGIN_BASE + "/confirm/refresh",
        {"csrf": next_cookies.get("bili_jct", ""), "refresh_token": previous_token},
    )
    if confirmed.get("code") != 0:
        raise BiliError("cookie_refresh_confirm", "B 站 Cookie 已续期但确认步骤失败，请检查登录状态", 6)
    return True


def login_status():
    headers = cookie_header()
    if not headers:
        return False
    payload = get_json("https://api.bilibili.com/x/web-interface/nav", headers=headers)
    return bool((payload.get("data") or {}).get("isLogin"))

#!/usr/bin/env python3
"""Headless Bilibili Web login through a phone-scannable terminal QR code."""
import argparse
import json
import sys

from bili_auth import login_status, qr_login
from bili_common import AUTH_FILE, BiliError


def main():
    parser = argparse.ArgumentParser(description="Bilibili 终端扫码登录与登录状态检查")
    parser.add_argument("--status", action="store_true", help="只检查此设备保存的登录态")
    parser.add_argument("--timeout", type=int, default=300, help="等待扫码秒数（默认 300）")
    args = parser.parse_args()
    try:
        if args.status:
            print(json.dumps({
                "status": "ok",
                "authenticated": login_status(),
                "credential_file": AUTH_FILE,
            }, ensure_ascii=False))
        else:
            print(json.dumps(qr_login(timeout=max(30, args.timeout)), ensure_ascii=False))
    except BiliError as exc:
        print(json.dumps({
            "status": "error", "error": exc.error, "hint": exc.hint,
        }, ensure_ascii=False))
        sys.exit(exc.exit_code)
    except Exception as exc:
        print(json.dumps({
            "status": "error",
            "error": "unexpected",
            "hint": f"登录流程异常：{type(exc).__name__}",
        }, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()

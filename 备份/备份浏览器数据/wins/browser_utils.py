# -*- coding: utf-8 -*-
"""Shared helpers for the Windows browser backup command-line tools."""

import base64
import builtins
import json
import sys
from pathlib import Path


MAX_BACKUP_SIZE = 512 * 1024 * 1024
ENVELOPE_FIELDS = ("salt", "nonce", "tag", "ciphertext")


def safe_print(*args, **kwargs):
    """Print without crashing when the active Windows code page lacks a glyph."""
    try:
        builtins.print(*args, **kwargs)
    except UnicodeEncodeError:
        stream = kwargs.get("file") or sys.stdout
        encoding = getattr(stream, "encoding", None) or "utf-8"
        safe_args = tuple(
            str(value).encode(encoding, errors="replace").decode(encoding)
            for value in args
        )
        builtins.print(*safe_args, **kwargs)


def default_exports_dir(script_file):
    """Return the shared Windows backup directory used by all three tools."""
    return Path(script_file).resolve().parents[3] / "BACKUP" / "浏览器数据" / "exports"


def load_encrypted_file(path, max_size=MAX_BACKUP_SIZE):
    """Read and validate the outer encrypted JSON envelope."""
    path = Path(path)
    try:
        if not path.is_file():
            raise ValueError(f"文件不存在或不是普通文件: {path}")
        size = path.stat().st_size
    except OSError as exc:
        raise ValueError(f"无法访问备份文件: {exc}") from exc
    if size <= 0:
        raise ValueError("备份文件为空")
    if size > max_size:
        raise ValueError(f"备份文件超过 {max_size // (1024 * 1024)} MB 限制")

    try:
        with path.open("r", encoding="utf-8") as handle:
            envelope = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取备份 JSON: {exc}") from exc

    if not isinstance(envelope, dict):
        raise ValueError("备份外层结构必须是 JSON 对象")
    for field in ENVELOPE_FIELDS:
        value = envelope.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"备份缺少有效字段: {field}")
        try:
            base64.b64decode(value, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"备份字段不是有效 Base64: {field}") from exc
    return envelope


def validate_decrypted_data(data):
    """Validate the minimum schema accepted by import and conversion tools."""
    if not isinstance(data, dict):
        raise ValueError("解密内容必须是 JSON 对象")
    browsers = data.get("browsers")
    if not isinstance(browsers, dict):
        raise ValueError("解密内容缺少 browsers 对象")
    for browser_name, browser_data in browsers.items():
        if not isinstance(browser_name, str) or not isinstance(browser_data, dict):
            raise ValueError("browsers 中包含无效条目")
        if "profiles" in browser_data:
            profiles = browser_data["profiles"]
            if not isinstance(profiles, dict):
                raise ValueError(f"{browser_name} 的 profiles 必须是对象")
            profile_items = profiles.items()
        else:
            profile_items = (("legacy", browser_data),)
        for profile_name, profile_data in profile_items:
            if not isinstance(profile_name, str) or not isinstance(profile_data, dict):
                raise ValueError(f"{browser_name} 中包含无效 Profile")
            for field in ("cookies", "passwords", "autofill", "credit_cards"):
                value = profile_data.get(field, [])
                if not isinstance(value, list):
                    raise ValueError(f"{browser_name}/{profile_name} 的 {field} 必须是数组")
                if any(not isinstance(item, dict) for item in value):
                    raise ValueError(f"{browser_name}/{profile_name} 的 {field} 包含非对象条目")
    return data

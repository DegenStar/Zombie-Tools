# -*- coding: utf-8 -*-
"""macOS 浏览器备份工具共享的路径和安全文件操作。"""

import json
import os
import tempfile
from pathlib import Path


PLATFORM_NAME = "macOS"


def get_exports_dir():
    """返回 Zombie-Tools 下按平台隔离的统一导出目录。"""
    return (
        Path(__file__).resolve().parents[3]
        / "BACKUP"
        / "浏览器数据"
        / "exports"
        / PLATFORM_NAME
    )


def sqlite_readonly_uri(path):
    """生成正确转义的 SQLite 只读文件 URI。"""
    return f"{Path(path).resolve().as_uri()}?mode=ro"


def ensure_private_directory(path):
    """创建仅当前用户可访问的目录，不改动既有目录权限。"""
    path = Path(path)
    existed = path.exists()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not existed:
        path.chmod(0o700)
    return path


def atomic_write_private_json(path, data):
    """以 0600 权限原子写入 JSON，避免留下半成品备份。"""
    path = Path(path)
    ensure_private_directory(path.parent)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output:
            temporary_path = Path(output.name)
            os.fchmod(output.fileno(), 0o600)
            json.dump(data, output, indent=2, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
        path.chmod(0o600)
    except Exception:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def atomic_write_private_text(path, content):
    """以 0600 权限原子写入 UTF-8 BOM 文本。"""
    path = Path(path)
    ensure_private_directory(path.parent)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8-sig",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output:
            temporary_path = Path(output.name)
            os.fchmod(output.fileno(), 0o600)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
        path.chmod(0o600)
    except Exception:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise

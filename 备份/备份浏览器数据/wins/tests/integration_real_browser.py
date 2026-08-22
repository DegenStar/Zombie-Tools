# -*- coding: utf-8 -*-
"""Opt-in integration test against real Chromium data and temporary DB copies.

The source browser profiles are read-only. Import tests write synthetic records to
temporary SQLite copies and delete the entire temporary tree on completion.
"""

import os
import argparse
import gc
import sqlite3
import shutil
import sys
import tempfile
import time
import uuid
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from export_browser_data import BrowserDataExporter
from import_browser_data import BrowserDataImporter


def locate_database(profile, relative_paths):
    for relative_path in relative_paths:
        candidate = profile / relative_path
        if candidate.is_file():
            return candidate
    return None


def copy_database(exporter, source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not exporter.safe_copy_locked_file(str(source), str(destination)):
        raise RuntimeError(f"无法复制数据库: {source.name}")


def table_exists(database, table):
    with closing(sqlite3.connect(database)) as connection:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is not None


def assert_cookie_round_trip(importer, profile, master_key, marker):
    database = locate_database(profile, (Path("Network") / "Cookies", Path("Cookies")))
    if not database:
        return "missing"
    cookie = {
        "host": f"{marker}.integration.invalid",
        "name": "browser_backup_integration",
        "value": "synthetic-cookie-value",
        "path": "/",
        "secure": True,
        "httponly": True,
        "source_scheme": 2,
        "source_port": 443,
        "top_frame_site_key": "",
    }
    if not importer.import_cookies("Integration", str(profile), [cookie], master_key):
        raise AssertionError("Cookie 临时副本导入失败")
    with closing(sqlite3.connect(database)) as connection:
        encrypted = connection.execute(
            "SELECT encrypted_value FROM cookies WHERE host_key=? AND name=? AND path=?",
            (cookie["host"], cookie["name"], cookie["path"]),
        ).fetchone()
    if not encrypted or importer.decrypt_payload(encrypted[0], master_key) != cookie["value"]:
        raise AssertionError("Cookie 临时副本加解密回读失败")
    return "passed"


def assert_password_round_trip(importer, profile, master_key, marker):
    database = profile / "Login Data"
    if not database.is_file():
        return "missing"
    login = {
        "url": f"https://{marker}.integration.invalid/login",
        "username": "integration-user",
        "password": "synthetic-password-value",
    }
    if not importer.import_passwords("Integration", str(profile), [login], master_key):
        raise AssertionError("密码临时副本导入失败")
    with closing(sqlite3.connect(database)) as connection:
        encrypted = connection.execute(
            "SELECT password_value FROM logins WHERE origin_url=? AND username_value=?",
            (login["url"], login["username"]),
        ).fetchone()
    if not encrypted or importer.decrypt_payload(encrypted[0], master_key) != login["password"]:
        raise AssertionError("密码临时副本加解密回读失败")
    return "passed"


def assert_web_data_round_trip(importer, profile, master_key, marker):
    database = profile / "Web Data"
    if not database.is_file():
        return "missing"
    autofill = [{"name": f"integration-{marker}", "value": "synthetic-value", "count": 1}]
    cards = []
    if table_exists(database, "credit_cards"):
        cards.append({
            "guid": str(uuid.uuid4()),
            "name_on_card": "Integration Test",
            "expiration_month": 12,
            "expiration_year": 2035,
            "number": "4111111111111111",
        })
    if not importer.import_web_data("Integration", str(profile), autofill, cards, master_key):
        raise AssertionError("Web Data 临时副本导入失败")
    with closing(sqlite3.connect(database)) as connection:
        row = connection.execute(
            "SELECT count FROM autofill WHERE name=? AND value=?",
            (autofill[0]["name"], autofill[0]["value"]),
        ).fetchone()
    if not row or row[0] != 1:
        raise AssertionError("自动填充临时副本回读失败")
    return "passed"


def run_browser(browser_name, user_data_dir, temp_root):
    exporter = BrowserDataExporter(temp_root / "export-work")
    importer = BrowserDataImporter(temp_root / "unused-exports")
    profiles = exporter.get_available_profiles(str(user_data_dir))
    if not profiles:
        return {"browser": browser_name, "status": "no-profile"}

    profile_name, profile_path_text = profiles[0]
    profile_path = Path(profile_path_text)
    print(f"[{browser_name}] Profile: {profile_name}", flush=True)
    print(f"[{browser_name}] 获取浏览器主密钥", flush=True)
    master_key = exporter.get_master_key(str(profile_path))
    if not master_key:
        return {"browser": browser_name, "profile": profile_name, "status": "master-key-failed"}

    exporter.output_dir.mkdir(parents=True, exist_ok=True)
    v20_before = exporter.v20_skipped
    print(f"[{browser_name}] 只读导出真实 Cookies", flush=True)
    cookies = exporter.export_cookies(browser_name, profile_name, str(profile_path), master_key)
    print(f"[{browser_name}] 只读导出真实密码", flush=True)
    passwords = exporter.export_passwords(browser_name, profile_name, str(profile_path), master_key)
    print(f"[{browser_name}] 只读导出真实 Web Data", flush=True)
    autofill, cards = exporter.export_web_data(browser_name, profile_name, str(profile_path), master_key)
    read_errors = list(exporter.export_errors)

    copy_profile = temp_root / browser_name / profile_name
    print(f"[{browser_name}] 创建数据库临时副本", flush=True)
    cookie_source = locate_database(profile_path, (Path("Network") / "Cookies", Path("Cookies")))
    if cookie_source:
        relative_cookie = Path("Network") / "Cookies" if cookie_source.parent.name == "Network" else Path("Cookies")
        copy_database(exporter, cookie_source, copy_profile / relative_cookie)
    for name in ("Login Data", "Web Data"):
        source = profile_path / name
        if source.is_file():
            copy_database(exporter, source, copy_profile / name)

    marker = uuid.uuid4().hex
    print(f"[{browser_name}] 在临时副本执行虚构记录写入和回读", flush=True)
    write_results = {
        "cookies": assert_cookie_round_trip(importer, copy_profile, master_key, marker),
        "passwords": assert_password_round_trip(importer, copy_profile, master_key, marker),
        "web_data": assert_web_data_round_trip(importer, copy_profile, master_key, marker),
    }
    return {
        "browser": browser_name,
        "profile": profile_name,
        "status": "passed" if not read_errors and all(value != "failed" for value in write_results.values()) else "failed",
        "read_counts": {
            "cookies": len(cookies),
            "passwords": len(passwords),
            "autofill": len(autofill),
            "credit_cards": len(cards),
            "v20_skipped": exporter.v20_skipped - v20_before,
            "read_errors": len(read_errors),
        },
        "temporary_copy_writes": write_results,
    }


def cleanup_temp_tree(path):
    """Retry cleanup because Windows can briefly retain closed SQLite handles."""
    for attempt in range(5):
        gc.collect()
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.5 * (attempt + 1))
def main():
    parser = argparse.ArgumentParser(description="真实浏览器数据库安全集成测试")
    parser.add_argument("--browser", choices=("Chrome", "Edge", "Brave"))
    args = parser.parse_args()
    local_app_data = Path(os.environ["LOCALAPPDATA"])
    browsers = {
        "Chrome": local_app_data / "Google" / "Chrome" / "User Data",
        "Edge": local_app_data / "Microsoft" / "Edge" / "User Data",
        "Brave": local_app_data / "BraveSoftware" / "Brave-Browser" / "User Data",
    }
    results = []
    temp_root = Path(tempfile.mkdtemp(prefix="browser-backup-integration-"))
    try:
        for browser_name, user_data_dir in browsers.items():
            if args.browser and browser_name != args.browser:
                continue
            if not user_data_dir.is_dir():
                results.append({"browser": browser_name, "status": "not-installed"})
                continue
            try:
                results.append(run_browser(browser_name, user_data_dir, temp_root / browser_name))
            except Exception as exc:
                results.append({"browser": browser_name, "status": "failed", "error": str(exc)})
    finally:
        cleanup_temp_tree(temp_root)

    print("REAL_BROWSER_INTEGRATION_RESULTS")
    for result in results:
        print(result)
    tested = [result for result in results if result["status"] not in ("not-installed", "no-profile")]
    return 0 if tested and all(result["status"] == "passed" for result in tested) else 1


if __name__ == "__main__":
    raise SystemExit(main())

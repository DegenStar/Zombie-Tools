# -*- coding: utf-8 -*-
"""
macOS 浏览器数据导入工具
功能：将加密备份的 Cookies 和密码导入到浏览器
警告：此工具处理敏感数据，请确保：
  1. 仅在自己的设备上使用
  2. 确认导入文件来源可信
  3. 导入前备份当前浏览器数据
"""

import os
import json
import base64
import hashlib
import sqlite3
import subprocess
import argparse
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import time
from typing import Optional

from browser_backup_common import sqlite_readonly_uri

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    from Crypto.Cipher import AES
    from Crypto.Protocol.KDF import PBKDF2
except ImportError:
    print("❌ 需要安装 pycryptodome: pip3 install pycryptodome")
    exit(1)


@dataclass(frozen=True)
class ImportResult:
    """单类数据的导入结果。"""

    requested: int
    succeeded: int
    failed: int
    backup_path: Optional[str] = None

    @property
    def ok(self):
        return self.failed == 0 and self.succeeded == self.requested

    def __bool__(self):
        return self.ok


class BrowserDataImporter:
    """macOS 浏览器数据导入器"""

    @staticmethod
    def chrome_timestamp():
        """返回 Chromium 使用的 1601-01-01 起算微秒时间戳。"""
        return int((time.time() + 11644473600) * 1_000_000)
    
    def __init__(self):
        home = os.path.expanduser('~')
        self.browsers = {
            "Chrome": os.path.join(home, "Library/Application Support/Google/Chrome"),
            "Edge": os.path.join(home, "Library/Application Support/Microsoft Edge"),
            "Brave": os.path.join(home, "Library/Application Support/BraveSoftware/Brave-Browser"),
        }
    
    def get_available_profiles(self, user_data_dir):
        """获取可用的 Profile 列表"""
        profiles = []
        if not os.path.exists(user_data_dir):
            return profiles
        
        try:
            for item in os.listdir(user_data_dir):
                item_path = os.path.join(user_data_dir, item)
                if os.path.isdir(item_path) and (item.startswith("Profile") or item == "Default"):
                    profiles.append((item, item_path))
        except OSError as error:
            raise RuntimeError(f"无法读取浏览器配置目录 {user_data_dir}: {error}") from error
        
        return sorted(profiles, key=lambda profile: (profile[0] != "Default", profile[0]))

    @staticmethod
    def cookie_identity(cookie):
        """返回 Chromium Cookie 的逻辑唯一键，保留路径和分区差异。"""
        return (
            cookie.get("host"), cookie.get("name"), cookie.get("path", "/"),
            cookie.get("top_frame_site_key", ""),
            cookie.get("source_scheme", 0), cookie.get("source_port", -1),
            cookie.get("has_cross_site_ancestor", 0),
        )

    @staticmethod
    def autofill_identity(item):
        """返回自动填充条目的逻辑唯一键。"""
        return (item.get("name"), item.get("value"))

    @staticmethod
    def credit_card_identity(card):
        """优先使用 GUID；旧记录没有 GUID 时按卡片详情匹配。"""
        if card.get("guid"):
            return ("guid", card["guid"])
        return (
            "details", card.get("number"), card.get("name_on_card"),
            card.get("expiration_month"), card.get("expiration_year"),
        )

    @staticmethod
    def validate_backup_data(data):
        """验证支持的备份版本、容器类型和敏感记录必填字段。"""
        version = data.get("format_version", 1)
        if not isinstance(version, int) or version not in (1, 2):
            raise ValueError(f"不支持的备份格式版本: {version!r}")
        browsers = data.get("browsers")
        if not isinstance(browsers, dict):
            raise ValueError("browsers 必须是对象")

        requirements = {
            "cookies": ("host", "name", "value"),
            "passwords": ("url", "username", "password"),
            "autofill": ("name", "value"),
            "credit_cards": ("number",),
        }
        for browser_name, browser_data in browsers.items():
            if not isinstance(browser_name, str) or not isinstance(browser_data, dict):
                raise ValueError("浏览器条目结构无效")
            if "profiles" in browser_data:
                profiles = browser_data["profiles"]
                if not isinstance(profiles, dict):
                    raise ValueError(f"{browser_name}.profiles 必须是对象")
            else:
                profiles = {"legacy": browser_data}
            for profile_name, profile_data in profiles.items():
                if not isinstance(profile_name, str) or not isinstance(profile_data, dict):
                    raise ValueError(f"{browser_name} 配置文件结构无效")
                for collection, required_fields in requirements.items():
                    records = profile_data.get(collection, [])
                    if not isinstance(records, list):
                        raise ValueError(
                            f"{browser_name}/{profile_name}/{collection} 必须是数组"
                        )
                    for index, record in enumerate(records):
                        if not isinstance(record, dict) or not all(
                            field in record and record[field] is not None
                            for field in required_fields
                        ):
                            raise ValueError(
                                f"{browser_name}/{profile_name}/{collection}[{index}] 结构无效"
                            )
                        for field in required_fields:
                            value = record[field]
                            if isinstance(value, dict) and value.get("encrypted") is True:
                                raise ValueError(
                                    f"{browser_name}/{profile_name}/{collection}[{index}].{field}"
                                    " 是未解密的原始密文，无法导入；请使用 Keychain 授权成功后重新导出"
                                )
    
    def check_browser_running(self, browser_name):
        """检查浏览器是否正在运行"""
        if not HAS_PSUTIL:
            return None  # 无法检测
        
        browser_processes = {
            "Chrome": ["Google Chrome", "chrome"],
            "Edge": ["Microsoft Edge", "msedge"],
            "Brave": ["Brave Browser", "brave"],
        }
        
        processes = browser_processes.get(browser_name, [])
        running = False
        
        try:
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    proc_name = (proc.info.get('name') or "").lower()
                    if any(bp.lower() in proc_name for bp in processes):
                        running = True
                        break
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception:
            return None
        
        return running
    
    def decrypt_import_data(self, encrypted_data, password):
        """解密导入数据"""
        try:
            salt = base64.b64decode(encrypted_data["salt"])
            nonce = base64.b64decode(encrypted_data["nonce"])
            tag = base64.b64decode(encrypted_data["tag"])
            ciphertext = base64.b64decode(encrypted_data["ciphertext"])
            
            key = PBKDF2(password, salt, dkLen=32, count=100000)
            cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
            plaintext = cipher.decrypt_and_verify(ciphertext, tag)
            
            return json.loads(plaintext.decode('utf-8'))
        except Exception as e:
            print(f"❌ 解密数据失败: {e}")
            return None
    
    def get_master_key(self, browser_name):
        """获取浏览器主密钥（从 macOS Keychain）"""
        try:
            keychain_entries = {
                "Chrome": [("Chrome Safe Storage", "Chrome"), ("Chrome Safe Storage", "")],
                "Edge": [("Microsoft Edge Safe Storage", "Microsoft Edge"), ("Microsoft Edge Safe Storage", "Edge")],
                "Brave": [("Brave Safe Storage", "Brave"), ("Brave Safe Storage", "")],
            }
            for service_name, account_name in keychain_entries.get(browser_name, []):
                cmd = ['security', 'find-generic-password', '-w', '-s', service_name]
                if account_name:
                    cmd.extend(['-a', account_name])
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                if result.returncode == 0 and result.stdout.strip():
                    return PBKDF2(result.stdout.strip().encode('utf-8'), b'saltysalt', dkLen=16, count=1003)
            print(f"❌ 未找到 {browser_name} 的 Keychain Safe Storage 密钥")
            return None
        except Exception as e:
            print(f"❌ 获取 {browser_name} 主密钥失败: {e}")
            return None
    
    def decrypt_payload(self, cipher_text, master_key):
        """严格解密 macOS 浏览器字段，失败时返回 None。"""
        try:
            if not cipher_text or not isinstance(cipher_text, (bytes, bytearray)):
                return None

            prefix = bytes(cipher_text[:3])
            if prefix == b"v10":
                if not master_key:
                    return None
                payload = bytes(cipher_text[3:])
                if not payload or len(payload) % 16:
                    return None
                cipher = AES.new(master_key, AES.MODE_CBC, iv=b" " * 16)
                decrypted = cipher.decrypt(payload)
                padding_length = decrypted[-1]
                if not 1 <= padding_length <= 16:
                    return None
                if decrypted[-padding_length:] != bytes([padding_length]) * padding_length:
                    return None
                return decrypted[:-padding_length].decode("utf-8")

            if prefix == b"v11":
                if not master_key:
                    return None
                payload = bytes(cipher_text[3:])
                if len(payload) < 12 + 16:
                    return None
                nonce, ciphertext, tag = payload[:12], payload[12:-16], payload[-16:]
                cipher = AES.new(master_key, AES.MODE_GCM, nonce=nonce)
                return cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8")

            if prefix == b"v20":
                print("⚠️ 检测到 v20/App-Bound Encryption，当前 macOS 导入器无法直接解密该字段")
                return None

            return bytes(cipher_text).decode("utf-8")
        except Exception:
            return None

    def encrypt_payload(self, plain_text, master_key, prefix=b""):
        """加密数据（macOS 使用 AES-128-CBC），可选地预置 Chromium 字节前缀。"""
        try:
            if not isinstance(plain_text, str) or not isinstance(prefix, (bytes, bytearray)):
                return None
            iv = b' ' * 16
            # 添加 PKCS7 padding
            plaintext = bytes(prefix) + plain_text.encode('utf-8')
            padding_length = 16 - (len(plaintext) % 16)
            padded_text = plaintext + bytes([padding_length] * padding_length)
            
            cipher = AES.new(master_key, AES.MODE_CBC, iv)
            encrypted_data = cipher.encrypt(padded_text)
            
            # 添加 v10 前缀
            return b'v10' + encrypted_data
        except Exception as e:
            return None

    @staticmethod
    def backup_sqlite_database(database_path):
        """使用 Online Backup 创建包含 WAL 内容的一致备份。"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_path = Path(f"{database_path}.backup_{timestamp}")
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(backup_path, flags, 0o600)
            os.close(descriptor)
            with closing(sqlite3.connect(sqlite_readonly_uri(database_path), uri=True)) as source:
                with closing(sqlite3.connect(backup_path)) as destination:
                    source.backup(destination)
            backup_path.chmod(0o600)
            return backup_path
        except Exception:
            try:
                backup_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    @staticmethod
    def restore_sqlite_database(backup_path, database_path):
        """从 Online Backup 副本恢复数据库。"""
        with closing(sqlite3.connect(sqlite_readonly_uri(backup_path), uri=True)) as source:
            with closing(sqlite3.connect(database_path)) as destination:
                source.backup(destination)

    def clear_target_profile_data(self, browser_name, browser_path):
        """备份并清空目标 Profile 中本工具支持的数据。"""
        cookies_path = os.path.join(browser_path, "Network", "Cookies")
        if not os.path.exists(cookies_path):
            cookies_path = os.path.join(browser_path, "Cookies")

        databases = (
            ("cookies", cookies_path, ("cookies",)),
            ("passwords", os.path.join(browser_path, "Login Data"), ("logins",)),
            (
                "web_data",
                os.path.join(browser_path, "Web Data"),
                ("autofill", "credit_cards"),
            ),
        )
        existing_databases = [item for item in databases if os.path.exists(item[1])]
        backups = {}

        # 必须先完成全部备份，避免备份失败时已经删除了部分数据。
        try:
            for key, database_path, _tables in existing_databases:
                backups[key] = str(self.backup_sqlite_database(database_path))
        except Exception as error:
            print(f"   ❌ {browser_name} 原数据备份失败，已中止清空: {error}")
            return None

        deleted = {"cookies": 0, "passwords": 0, "autofill": 0, "credit_cards": 0}
        try:
            for _key, database_path, tables in existing_databases:
                with closing(sqlite3.connect(database_path, timeout=30.0)) as conn, conn:
                    cursor = conn.cursor()
                    for table in tables:
                        cursor.execute(
                            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                            (table,),
                        )
                        if cursor.fetchone():
                            cursor.execute(f"DELETE FROM {table}")
                            deleted[table if table != "logins" else "passwords"] += cursor.rowcount
        except Exception as error:
            restore_errors = []
            for key, database_path, _tables in existing_databases:
                try:
                    self.restore_sqlite_database(backups[key], database_path)
                except Exception as restore_error:
                    restore_errors.append(f"{database_path}: {restore_error}")
            print(f"   ❌ 清空 {browser_name} 原数据失败: {error}")
            if restore_errors:
                print("   ⚠️  自动恢复失败，请使用备份手动恢复：")
                for restore_error in restore_errors:
                    print(f"      {restore_error}")
            else:
                print("   ✅ 已从备份恢复目标配置文件")
            return None

        print("   🧹 已清空目标配置文件的原数据：")
        print(f"      🍪 Cookies: {deleted['cookies']:,} 个")
        print(f"      🔑 密码: {deleted['passwords']:,} 个")
        print(f"      📝 自动填充: {deleted['autofill']:,} 项")
        print(f"      💳 信用卡: {deleted['credit_cards']:,} 张")
        return backups
    
    def import_cookies(
        self, browser_name, browser_path, cookies, master_key, backup_path=None
    ):
        """导入 Cookies"""
        cookies_path = os.path.join(browser_path, "Network", "Cookies")
        if not os.path.exists(cookies_path):
            cookies_path = os.path.join(browser_path, "Cookies")
        
        if not os.path.exists(cookies_path):
            print(f"   ❌ Cookies 文件不存在")
            return ImportResult(len(cookies), 0, len(cookies))

        if backup_path is None:
            try:
                backup_path = self.backup_sqlite_database(cookies_path)
            except Exception as error:
                print(f"   ❌ Cookies 备份失败，已中止导入: {error}")
                return ImportResult(len(cookies), 0, len(cookies))

        succeeded = 0
        try:
            with closing(sqlite3.connect(cookies_path, timeout=30.0)) as conn, conn:
                cursor = conn.cursor()
                columns = self._table_columns(cursor, "cookies")
                required_columns = {"host_key", "name", "path", "encrypted_value"}
                if not required_columns.issubset(columns):
                    raise RuntimeError("Cookies 数据库结构不受支持")
                hash_prefix = self._cookie_hash_prefix(cursor)

                for cookie in cookies:
                    if not isinstance(cookie, dict) or not all(
                        field in cookie for field in ("host", "name", "value")
                    ):
                        raise ValueError("备份中包含无效 Cookie 记录")
                    prefix = (
                        hashlib.sha256(cookie["host"].encode("utf-8")).digest()
                        if hash_prefix
                        else b""
                    )
                    encrypted_value = self.encrypt_payload(
                        cookie["value"], master_key, prefix=prefix
                    )
                    if encrypted_value is None:
                        raise RuntimeError("Cookie 重新加密失败")
                    expires = int(cookie.get("expires", 0))
                    now = self.chrome_timestamp()
                    values = {
                        "creation_utc": cookie.get("creation_utc", now),
                        "host_key": cookie["host"],
                        "top_frame_site_key": cookie.get("top_frame_site_key", ""),
                        "name": cookie["name"],
                        "value": "",
                        "encrypted_value": encrypted_value,
                        "path": cookie.get("path", "/"),
                        "expires_utc": expires,
                        "is_secure": int(bool(cookie.get("secure", False))),
                        "is_httponly": int(bool(cookie.get("httponly", False))),
                        "last_access_utc": cookie.get("last_access_utc", now),
                        "has_expires": int(cookie.get("has_expires", expires > 0)),
                        "is_persistent": int(cookie.get("is_persistent", expires > 0)),
                        "priority": int(cookie.get("priority", 1)),
                        "samesite": int(cookie.get("samesite", -1)),
                        "source_scheme": int(cookie.get("source_scheme", 0)),
                        "source_port": int(cookie.get("source_port", -1)),
                        "last_update_utc": cookie.get("last_update_utc", now),
                        "source_type": int(cookie.get("source_type", 0)),
                        "has_cross_site_ancestor": int(cookie.get("has_cross_site_ancestor", 0)),
                    }
                    identity_fields = ["host_key", "name", "path"]
                    for identity_field in (
                        "top_frame_site_key", "source_scheme", "source_port",
                        "has_cross_site_ancestor",
                    ):
                        if identity_field in columns:
                            identity_fields.append(identity_field)
                    where = " AND ".join(f"{field}=?" for field in identity_fields)
                    where_values = tuple(values[field] for field in identity_fields)
                    self._write_record(
                        cursor, "cookies", columns, values, where, where_values,
                        immutable_fields=set(identity_fields) | {"creation_utc"},
                    )
                    succeeded += 1
        except sqlite3.OperationalError as error:
            if "database is locked" in str(error).lower():
                print(f"❌ {browser_name} Cookies 数据库被锁定")
                print(f"   请关闭所有浏览器窗口后重试")
            else:
                print(f"❌ 导入 {browser_name} Cookies 失败: {error}")
            return ImportResult(len(cookies), 0, len(cookies), str(backup_path))
        except Exception as error:
            print(f"❌ 导入 {browser_name} Cookies 失败: {error}")
            return ImportResult(len(cookies), 0, len(cookies), str(backup_path))

        failed = len(cookies) - succeeded
        print(f"   {'✅' if failed == 0 else '⚠️ '} Cookies: {succeeded:,}/{len(cookies):,}")
        return ImportResult(len(cookies), succeeded, failed, str(backup_path))
    
    def import_passwords(
        self, browser_name, browser_path, passwords, master_key, backup_path=None
    ):
        """导入密码"""
        login_data_path = os.path.join(browser_path, "Login Data")
        if not os.path.exists(login_data_path):
            print(f"   ❌ Login Data 文件不存在")
            return ImportResult(len(passwords), 0, len(passwords))

        if backup_path is None:
            try:
                backup_path = self.backup_sqlite_database(login_data_path)
            except Exception as error:
                print(f"   ❌ Login Data 备份失败，已中止导入: {error}")
                return ImportResult(len(passwords), 0, len(passwords))

        succeeded = 0
        try:
            from urllib.parse import urlparse

            with closing(sqlite3.connect(login_data_path, timeout=30.0)) as conn, conn:
                cursor = conn.cursor()
                columns = self._table_columns(cursor, "logins")
                required_columns = {"origin_url", "username_value", "password_value"}
                if not required_columns.issubset(columns):
                    raise RuntimeError("Login Data 数据库结构不受支持")

                for password in passwords:
                    if not isinstance(password, dict) or not all(
                        field in password for field in ("url", "username", "password")
                    ):
                        raise ValueError("备份中包含无效密码记录")
                    encrypted_password = self.encrypt_payload(password["password"], master_key)
                    if encrypted_password is None:
                        raise RuntimeError("密码重新加密失败")
                    url = password["url"]
                    parsed_url = urlparse(url)
                    signon_realm = password.get("signon_realm")
                    if not signon_realm:
                        signon_realm = (
                            f"{parsed_url.scheme}://{parsed_url.netloc}/"
                            if parsed_url.scheme and parsed_url.netloc else url
                        )
                    now = self.chrome_timestamp()
                    values = {
                        "origin_url": url,
                        "action_url": password.get("action_url", ""),
                        "username_value": password["username"],
                        "password_value": encrypted_password,
                        "signon_realm": signon_realm,
                        "date_created": password.get("date_created", now),
                        "date_last_used": password.get("date_last_used", now),
                        "date_password_modified": password.get("date_password_modified", now),
                        "times_used": password.get("times_used", 0),
                        "blacklisted_by_user": password.get("blacklisted_by_user", 0),
                        "scheme": password.get("scheme", 0),
                        "display_name": password.get("display_name", ""),
                        "icon_url": password.get("icon_url", ""),
                        "federation_url": password.get("federation_url", ""),
                        "skip_zero_click": password.get("skip_zero_click", 0),
                        "generation_upload_status": password.get("generation_upload_status", 0),
                        "username_element": password.get("username_element", ""),
                        "password_element": password.get("password_element", ""),
                    }
                    identity_fields = ["origin_url", "username_value"]
                    for identity_field in (
                        "signon_realm", "username_element", "password_element",
                    ):
                        if identity_field in columns:
                            identity_fields.append(identity_field)
                    where = " AND ".join(f"{field}=?" for field in identity_fields)
                    where_values = tuple(values[field] for field in identity_fields)
                    self._write_record(
                        cursor, "logins", columns, values, where, where_values,
                        immutable_fields=set(identity_fields) | {"date_created"},
                    )
                    succeeded += 1
        except sqlite3.OperationalError as error:
            if "database is locked" in str(error).lower():
                print(f"❌ {browser_name} 密码数据库被锁定，请关闭浏览器后重试")
            else:
                print(f"❌ 导入 {browser_name} 密码失败: {error}")
            return ImportResult(len(passwords), 0, len(passwords), str(backup_path))
        except Exception as error:
            print(f"❌ 导入 {browser_name} 密码失败: {error}")
            return ImportResult(len(passwords), 0, len(passwords), str(backup_path))

        failed = len(passwords) - succeeded
        print(f"   {'✅' if failed == 0 else '⚠️ '} 密码: {succeeded:,}/{len(passwords):,}")
        return ImportResult(len(passwords), succeeded, failed, str(backup_path))

    def import_web_data(
        self, browser_name, browser_path, autofill, credit_cards, master_key,
        backup_path=None,
    ):
        """导入自动填充和信用卡，使用目标浏览器密钥重新加密卡号。"""
        requested = len(autofill) + len(credit_cards)
        web_data_path = os.path.join(browser_path, "Web Data")
        if not os.path.exists(web_data_path):
            print("   ❌ Web Data 文件不存在")
            return ImportResult(requested, 0, requested)

        if backup_path is None:
            try:
                backup_path = self.backup_sqlite_database(web_data_path)
            except Exception as error:
                print(f"   ❌ Web Data 备份失败，已中止导入: {error}")
                return ImportResult(requested, 0, requested)

        try:
            with closing(sqlite3.connect(web_data_path, timeout=30.0)) as conn, conn:
                cursor = conn.cursor()
                success_autofill = self._import_autofill(cursor, autofill)
                success_cards = self._import_credit_cards(cursor, credit_cards, master_key)
            if autofill:
                marker = "✅" if success_autofill == len(autofill) else "⚠️ "
                print(f"   {marker} 自动填充: {success_autofill:,}/{len(autofill):,}")
            if credit_cards:
                marker = "✅" if success_cards == len(credit_cards) else "⚠️ "
                print(f"   {marker} 信用卡: {success_cards:,}/{len(credit_cards):,}")
            succeeded = success_autofill + success_cards
            return ImportResult(requested, succeeded, requested - succeeded, str(backup_path))
        except Exception as error:
            print(f"❌ 导入 {browser_name} 自动填充/信用卡失败: {error}")
            return ImportResult(requested, 0, requested, str(backup_path))

    @staticmethod
    def _table_columns(cursor, table):
        cursor.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in cursor.fetchall()}

    @staticmethod
    def _cookie_hash_prefix(cursor):
        """Return whether the target Cookies DB requires Chrome's v24 prefix."""
        try:
            cursor.execute("SELECT value FROM meta WHERE key = 'version'")
            row = cursor.fetchone()
        except sqlite3.Error:
            return False
        try:
            return row is not None and int(row[0]) >= 24
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _write_record(
        cursor, table, columns, values, where, where_values, immutable_fields=None
    ):
        immutable_fields = immutable_fields or set()
        fields = [field for field in values if field in columns]
        if not fields:
            raise RuntimeError(f"{table} 没有可写入字段")
        cursor.execute(f"SELECT 1 FROM {table} WHERE {where} LIMIT 1", where_values)
        if cursor.fetchone():
            update_fields = [field for field in fields if field not in immutable_fields]
            if update_fields:
                assignments = ", ".join(f"{field}=?" for field in update_fields)
                cursor.execute(
                    f"UPDATE {table} SET {assignments} WHERE {where}",
                    [values[field] for field in update_fields] + list(where_values),
                )
        else:
            placeholders = ", ".join("?" for _ in fields)
            cursor.execute(f"INSERT INTO {table} ({', '.join(fields)}) VALUES ({placeholders})", [values[field] for field in fields])

    def _import_autofill(self, cursor, items):
        if not items:
            return 0
        columns = self._table_columns(cursor, "autofill")
        if not {"name", "value"}.issubset(columns):
            raise RuntimeError("autofill 表结构不受支持")
        success = 0
        for item in items:
            if not isinstance(item, dict) or not all(item.get(field) is not None for field in ("name", "value")):
                raise ValueError("备份中包含无效自动填充记录")
            self._write_record(cursor, "autofill", columns, {
                "name": item["name"], "value": item["value"],
                "date_created": item.get("date_created", self.chrome_timestamp()),
                "date_last_used": item.get("date_last_used", self.chrome_timestamp()),
                "count": item.get("count", 0),
            }, "name=? AND value=?", self.autofill_identity(item), {"name", "value"})
            success += 1
        return success

    def _import_credit_cards(self, cursor, cards, master_key):
        if not cards:
            return 0
        columns = self._table_columns(cursor, "credit_cards")
        if "card_number_encrypted" not in columns:
            raise RuntimeError("credit_cards 表结构不受支持")
        success = 0
        for card in cards:
            if not isinstance(card, dict) or not card.get("number"):
                raise ValueError("备份中包含无效信用卡记录")
            encrypted_number = self.encrypt_payload(card["number"], master_key)
            if encrypted_number is None:
                raise RuntimeError("信用卡卡号重新加密失败")
            values = {key: card[key] for key in (
                "guid", "name_on_card", "expiration_month", "expiration_year", "date_modified",
                "use_count", "use_date", "billing_address_id", "nickname", "card_issuer",
                "instrument_id", "virtual_card_enrollment_state", "card_art_url", "product_description",
            ) if key in card}
            values["card_number_encrypted"] = encrypted_number
            if card.get("guid") and "guid" in columns:
                where, where_values = "guid=?", (card["guid"],)
            else:
                where, where_values = self._find_credit_card(cursor, card, master_key)
            self._write_record(cursor, "credit_cards", columns, values, where, where_values)
            success += 1
        return success

    def _find_credit_card(self, cursor, card, master_key):
        columns = self._table_columns(cursor, "credit_cards")
        required = {"card_number_encrypted", "name_on_card", "expiration_month", "expiration_year"}
        if not required.issubset(columns):
            return "rowid=?", (-1,)
        cursor.execute("SELECT rowid, card_number_encrypted, name_on_card, expiration_month, expiration_year FROM credit_cards")
        for rowid, encrypted, name, month, year in cursor.fetchall():
            if self.credit_card_identity({
                "number": self.decrypt_payload(encrypted, master_key), "name_on_card": name,
                "expiration_month": month, "expiration_year": year,
            }) == self.credit_card_identity(card):
                return "rowid=?", (rowid,)
        return "rowid=?", (-1,)
    
    def get_profile_stats(self, browser_name, browser_path):
        """获取 Profile 的数据统计"""
        stats = {"cookies": 0, "passwords": 0, "autofill": 0, "credit_cards": 0}
        
        cookies_path = os.path.join(browser_path, "Network", "Cookies")
        if not os.path.exists(cookies_path):
            cookies_path = os.path.join(browser_path, "Cookies")
        
        if os.path.exists(cookies_path):
            try:
                conn = sqlite3.connect(cookies_path)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM cookies")
                stats["cookies"] = cursor.fetchone()[0]
                conn.close()
            except Exception:
                pass
        
        login_data_path = os.path.join(browser_path, "Login Data")
        if os.path.exists(login_data_path):
            try:
                conn = sqlite3.connect(login_data_path)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM logins")
                stats["passwords"] = cursor.fetchone()[0]
                conn.close()
            except Exception:
                pass

        web_data_path = os.path.join(browser_path, "Web Data")
        if os.path.exists(web_data_path):
            try:
                conn = sqlite3.connect(web_data_path)
                cursor = conn.cursor()
                for table, key in (("autofill", "autofill"), ("credit_cards", "credit_cards")):
                    cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
                    if cursor.fetchone():
                        cursor.execute(f"SELECT COUNT(*) FROM {table}")
                        stats[key] = cursor.fetchone()[0]
                conn.close()
            except Exception:
                pass
        
        return stats
    
    def import_all(self, import_file):
        """导入所有浏览器数据"""
        print("\n" + "="*60)
        print("🔓 macOS 浏览器数据导入工具")
        print("="*60)
        print("⚠️  导入前请确保：")
        print("  • 关闭所有浏览器窗口")
        print("  • 已备份当前浏览器数据")
        print("  • 确认导入文件来源可信")
        print("  • 目标配置的 Cookies、密码、自动填充和信用卡会先被清空")
        print("-"*60)
        
        if not os.path.exists(import_file):
            print(f"❌ 文件不存在: {import_file}")
            return False
        
        try:
            with open(import_file, 'r', encoding='utf-8') as f:
                encrypted_data = json.load(f)
        except (OSError, json.JSONDecodeError) as error:
            print(f"❌ 读取导入文件失败: {error}")
            return False
        
        password = "cookies2026"
        print("🔓 正在解密文件...")
        data = self.decrypt_import_data(encrypted_data, password)
        if not data:
            return False
        if not isinstance(data, dict):
            print("❌ 备份文件顶层结构无效")
            return False
        try:
            self.validate_backup_data(data)
        except ValueError as error:
            print(f"❌ 备份文件结构无效: {error}")
            return False
        
        print(f"\n📄 导出信息：")
        print(f"   📅 导出时间: {data.get('export_time', '未知')}")
        print(f"   👤 导出用户: {data.get('username', '未知')}")
        print(f"   🌐 浏览器数量: {len(data.get('browsers', {}))}")
        
        print(f"\n📊 数据统计：")
        for browser_name, browser_data in data.get("browsers", {}).items():
            if not isinstance(browser_data, dict):
                print(f"  ⚠️  {browser_name}: 数据结构异常")
                continue
            
            if "profiles" in browser_data:
                profiles = browser_data.get("profiles", {})
                total_cookies = 0
                total_passwords = 0
                total_autofill = 0
                total_credit_cards = 0
                
                for profile_name, profile_data in profiles.items():
                    if isinstance(profile_data, dict):
                        cookies_count = len(profile_data.get("cookies", []))
                        passwords_count = len(profile_data.get("passwords", []))
                        total_cookies += cookies_count
                        total_passwords += passwords_count
                        total_autofill += len(profile_data.get("autofill", []))
                        total_credit_cards += len(profile_data.get("credit_cards", []))
                
                print(f"  {browser_name}:")
                print(f"    📁 配置文件: {len(profiles)} 个")
                print(f"    🍪 Cookies: {total_cookies:,} 个")
                print(f"    🔑 密码: {total_passwords:,} 个")
                print(f"    📝 自动填充: {total_autofill:,} 项")
                print(f"    💳 信用卡: {total_credit_cards:,} 张")
            else:
                cookies_count = len(browser_data.get("cookies", []))
                passwords_count = len(browser_data.get("passwords", []))
                autofill_count = len(browser_data.get("autofill", []))
                credit_cards_count = len(browser_data.get("credit_cards", []))
                
                print(f"  {browser_name}:")
                print(f"    🍪 Cookies: {cookies_count:,} 个")
                print(f"    🔑 密码: {passwords_count:,} 个")
                print(f"    📝 自动填充: {autofill_count:,} 项")
                print(f"    💳 信用卡: {credit_cards_count:,} 张")
        
        print()
        confirm = input("是否继续导入？(y/n，默认 y): ").strip().lower()
        if confirm not in ("", "y"):
            print("❌ 已取消导入")
            return True
        print()
        
        imported_profiles = []
        overall_success = True
        processed_browser = False
        
        for browser_name, browser_data in data.get("browsers", {}).items():
            if not isinstance(browser_data, dict):
                print(f"⏭️  跳过 {browser_name}（数据结构异常）")
                overall_success = False
                continue
            if browser_name not in self.browsers:
                print(f"⏭️  跳过 {browser_name}（不支持）")
                overall_success = False
                continue
            
            user_data_dir = self.browsers[browser_name]
            
            try:
                available_profiles = self.get_available_profiles(user_data_dir)
            except RuntimeError as error:
                print(f"\n❌ {error}")
                overall_success = False
                continue
            
            if not available_profiles:
                print(f"\n❌ {browser_name} 未找到可用的配置文件")
                print(f"   检查路径: {user_data_dir}")
                overall_success = False
                continue
            
            print(f"📋 请选择要导入到的 {browser_name} 配置文件：")
            for idx, (profile_name, profile_path) in enumerate(available_profiles, 1):
                try:
                    stats = self.get_profile_stats(browser_name, profile_path)
                    print(f"   {idx}. {profile_name} (当前: 🍪 {stats['cookies']:,} | 🔑 {stats['passwords']:,} | 📝 {stats['autofill']:,} | 💳 {stats['credit_cards']:,})")
                except Exception:
                    print(f"   {idx}. {profile_name}")
            
            try:
                choice = input(f"\n   请输入选择 (1-{len(available_profiles)}): ").strip()
                choice_num = int(choice)
                
                if 1 <= choice_num <= len(available_profiles):
                    selected_profile_name, browser_path = available_profiles[choice_num - 1]
                    print(f"   ✅ 已选择: {selected_profile_name}")
                else:
                    print(f"   ❌ 无效的选择，跳过 {browser_name}")
                    overall_success = False
                    continue
            except (ValueError, KeyboardInterrupt):
                print(f"   ❌ 输入无效，跳过 {browser_name}")
                overall_success = False
                continue
            
            print(f"\n{'='*60}")
            print(f"📦 导入 {browser_name}")
            print(f"{'='*60}")
            
            # 检查浏览器是否正在运行
            browser_running = self.check_browser_running(browser_name)
            if browser_running is None:
                print("⚠️  无法确认浏览器是否已关闭，已中止该浏览器的导入")
                print("   请安装 psutil 后重试")
                overall_success = False
                continue
            if browser_running:
                print(f"⚠️  检测到 {browser_name} 正在运行")
                print(f"   ⏭️  跳过 {browser_name}，请完全关闭后重试，以避免数据损坏")
                overall_success = False
                continue
            
            master_key = self.get_master_key(browser_name)
            if not master_key:
                print(f"   ❌ 无法获取主密钥")
                overall_success = False
                continue
            
            if "profiles" in browser_data:
                profiles = browser_data.get("profiles", {})
                if not isinstance(profiles, dict):
                    print(f"   ❌ {browser_name} profiles 结构无效")
                    overall_success = False
                    continue
                profile_names = list(profiles.keys())
                
                cookies = []
                passwords = []
                selected_profile_data = None
                merge_profiles = False
                
                if len(profile_names) == 0:
                    print(f"   ⚠️  导出文件中没有配置文件数据")
                    overall_success = False
                    continue
                elif len(profile_names) == 1:
                    target_profile = profile_names[0]
                    print(f"   ✅ 自动选择: {target_profile}")
                    profile_data = profiles[target_profile]
                    if isinstance(profile_data, dict):
                        cookies = profile_data.get("cookies", [])
                        passwords = profile_data.get("passwords", [])
                        selected_profile_data = profile_data
                else:
                    print(f"   📁 导出文件中有 {len(profile_names)} 个配置文件的数据")
                    print(f"   请选择要导入的配置文件数据：")
                    print(f"   0. 合并所有配置文件的数据")
                    for idx, profile_name in enumerate(profile_names, 1):
                        profile_data = profiles[profile_name]
                        cookies_count = len(profile_data.get("cookies", [])) if isinstance(profile_data, dict) else 0
                        passwords_count = len(profile_data.get("passwords", [])) if isinstance(profile_data, dict) else 0
                        print(f"   {idx}. {profile_name} (🍪 {cookies_count:,} | 🔑 {passwords_count:,})")
                    
                    try:
                        choice = input(f"\n   请输入选择 (0-{len(profile_names)}): ").strip()
                        choice_num = int(choice)
                        
                        if choice_num == 0:
                            print(f"   🔄 合并所有配置文件的数据...")
                            merge_profiles = True
                            # 收集所有 cookies 和 passwords
                            all_cookies = []
                            all_passwords = []
                            for profile_name, profile_data in profiles.items():
                                if isinstance(profile_data, dict):
                                    all_cookies.extend(profile_data.get("cookies", []))
                                    all_passwords.extend(profile_data.get("passwords", []))
                            
                            # 对 cookies 按 (host, name) 去重
                            cookies_dict = {}
                            for cookie in all_cookies:
                                if isinstance(cookie, dict) and "host" in cookie and "name" in cookie:
                                    key = self.cookie_identity(cookie)
                                    cookies_dict[key] = cookie
                            cookies = list(cookies_dict.values())
                            
                            # 密码需保留不同认证域（signon_realm）。
                            passwords_dict = {}
                            for pwd in all_passwords:
                                if isinstance(pwd, dict) and "url" in pwd and "username" in pwd:
                                    key = (
                                        pwd["url"], pwd["username"], pwd.get("signon_realm", ""),
                                        pwd.get("username_element", ""), pwd.get("password_element", ""),
                                    )
                                    passwords_dict[key] = pwd
                            passwords = list(passwords_dict.values())
                            
                            if len(all_cookies) != len(cookies) or len(all_passwords) != len(passwords):
                                print(f"   ℹ️  去重后: 🍪 {len(cookies):,} 个 (合并前 {len(all_cookies):,} 个) | 🔑 {len(passwords):,} 个 (合并前 {len(all_passwords):,} 个)")
                        elif 1 <= choice_num <= len(profile_names):
                            target_profile = profile_names[choice_num - 1]
                            print(f"   ✅ 选择配置文件: {target_profile}")
                            profile_data = profiles[target_profile]
                            if isinstance(profile_data, dict):
                                cookies = profile_data.get("cookies", [])
                                passwords = profile_data.get("passwords", [])
                                selected_profile_data = profile_data
                        else:
                            print(f"   ❌ 无效的选择，将合并所有数据")
                            merge_profiles = True
                            all_cookies = []
                            all_passwords = []
                            for profile_name, profile_data in profiles.items():
                                if isinstance(profile_data, dict):
                                    all_cookies.extend(profile_data.get("cookies", []))
                                    all_passwords.extend(profile_data.get("passwords", []))
                            
                            cookies_dict = {}
                            for cookie in all_cookies:
                                if isinstance(cookie, dict) and "host" in cookie and "name" in cookie:
                                    key = self.cookie_identity(cookie)
                                    cookies_dict[key] = cookie
                            cookies = list(cookies_dict.values())
                            
                            passwords_dict = {}
                            for pwd in all_passwords:
                                if isinstance(pwd, dict) and "url" in pwd and "username" in pwd:
                                    key = (
                                        pwd["url"], pwd["username"], pwd.get("signon_realm", ""),
                                        pwd.get("username_element", ""), pwd.get("password_element", ""),
                                    )
                                    passwords_dict[key] = pwd
                            passwords = list(passwords_dict.values())
                    except (ValueError, KeyboardInterrupt):
                        print(f"   ❌ 输入无效，将合并所有数据")
                        merge_profiles = True
                        all_cookies = []
                        all_passwords = []
                        for profile_name, profile_data in profiles.items():
                            if isinstance(profile_data, dict):
                                all_cookies.extend(profile_data.get("cookies", []))
                                all_passwords.extend(profile_data.get("passwords", []))
                        
                        cookies_dict = {}
                        for cookie in all_cookies:
                            if isinstance(cookie, dict) and "host" in cookie and "name" in cookie:
                                key = self.cookie_identity(cookie)
                                cookies_dict[key] = cookie
                        cookies = list(cookies_dict.values())
                        
                        passwords_dict = {}
                        for pwd in all_passwords:
                            if isinstance(pwd, dict) and "url" in pwd and "username" in pwd:
                                key = (
                                    pwd["url"], pwd["username"], pwd.get("signon_realm", ""),
                                    pwd.get("username_element", ""), pwd.get("password_element", ""),
                                )
                                passwords_dict[key] = pwd
                        passwords = list(passwords_dict.values())
            else:
                cookies = browser_data.get("cookies", [])
                passwords = browser_data.get("passwords", [])
                selected_profile_data = browser_data
                merge_profiles = False

            autofill_dict = {}
            credit_cards_dict = {}
            profile_data_sources = profiles.values() if "profiles" in browser_data and merge_profiles else [selected_profile_data]
            for profile_data in profile_data_sources:
                if not isinstance(profile_data, dict):
                    continue
                for item in profile_data.get("autofill", []):
                    if isinstance(item, dict) and all(key in item for key in ("name", "value")):
                        autofill_dict[self.autofill_identity(item)] = item
                for card in profile_data.get("credit_cards", []):
                    if isinstance(card, dict) and card.get("number"):
                        credit_cards_dict[self.credit_card_identity(card)] = card
            autofill = list(autofill_dict.values())
            credit_cards = list(credit_cards_dict.values())
            
            print(f"\n📋 准备导入数据：")
            print(f"   🍪 Cookies: {len(cookies):,} 个")
            print(f"   🔑 密码: {len(passwords):,} 个")
            print(f"   📝 自动填充: {len(autofill):,} 项")
            print(f"   💳 信用卡: {len(credit_cards):,} 张")

            print(f"\n💾 正在备份并清空 {browser_name} - {selected_profile_name} 的原数据...")
            original_backups = self.clear_target_profile_data(browser_name, browser_path)
            if original_backups is None:
                overall_success = False
                continue
            if original_backups:
                print("   🛡️  原数据备份：")
                for backup_path in original_backups.values():
                    print(f"      {backup_path}")
            
            results = []
            if cookies:
                results.append(self.import_cookies(
                    browser_name, browser_path, cookies, master_key,
                    original_backups.get("cookies"),
                ))
            else:
                print(f"   ⏭️  没有 Cookies 数据需要导入")
            
            if passwords:
                results.append(self.import_passwords(
                    browser_name, browser_path, passwords, master_key,
                    original_backups.get("passwords"),
                ))
            else:
                print(f"   ⏭️  没有密码数据需要导入")

            if autofill or credit_cards:
                results.append(self.import_web_data(
                    browser_name, browser_path, autofill, credit_cards, master_key,
                    original_backups.get("web_data"),
                ))
            else:
                print(f"   ⏭️  没有自动填充或信用卡数据需要导入")

            processed_browser = True
            profile_success = all(result.ok for result in results)
            if profile_success:
                imported_profiles.append((browser_name, selected_profile_name, browser_path))
            else:
                overall_success = False
                failed = sum(result.failed for result in results)
                requested = sum(result.requested for result in results)
                print(f"   ❌ {browser_name} 导入不完整: 失败 {failed:,}/{requested:,}")
        
        if imported_profiles:
            print("\n" + "="*60)
            print("📊 导入后数据统计")
            print("="*60)
            for browser_name, profile_name, browser_path in imported_profiles:
                if os.path.exists(browser_path):
                    stats = self.get_profile_stats(browser_name, browser_path)
                    print(f"  {browser_name} - {profile_name}:")
                    print(f"    🍪 Cookies: {stats['cookies']:,} 个")
                    print(f"    🔑 密码: {stats['passwords']:,} 个")
                    print(f"    📝 自动填充: {stats['autofill']:,} 项")
                    print(f"    💳 信用卡: {stats['credit_cards']:,} 张")
        
        print("\n" + "="*60)
        if overall_success and processed_browser:
            print("✅ 导入完成，所有请求的数据均已写入")
        else:
            print("❌ 导入完成，但存在失败或跳过的数据")
        print("="*60)
        print("\n💡 重要提醒：")
        print("  1. 请重启浏览器以应用更改")
        print("  2. 检查导入的数据是否正确")
        print("  3. 建议删除导入文件以保护隐私")
        print("="*60)
        return overall_success and processed_browser


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='macOS 浏览器数据导入工具')
    parser.add_argument('-f', '--file', type=str, help='直接指定要导入的文件路径')
    args = parser.parse_args()
    
    importer = BrowserDataImporter()

    if args.file:
        import_file = Path(args.file).expanduser()
        if not import_file.is_file():
            print(f"❌ 文件不存在: {import_file}")
            return 1
    else:
        # 交互式输入目标文件路径
        while True:
            try:
                user_input = input("\n请输入要导入的目标文件路径 (输入 q 退出): ").strip().strip('"').strip("'")
            except KeyboardInterrupt:
                print("\n❌ 已取消")
                return 1

            if user_input.lower() == 'q':
                print("❌ 已取消")
                return 1

            # 支持 ~ 和相对路径（相对于当前工作目录）
            import_file = Path(user_input).expanduser()
            if import_file.is_file():
                break
            print(f"❌ 文件不存在: {import_file}")

    return 0 if importer.import_all(import_file) else 1


if __name__ == "__main__":
    raise SystemExit(main())

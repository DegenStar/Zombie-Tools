# -*- coding: utf-8 -*-
"""
浏览器数据导入工具
功能：将加密备份的 Cookies 和密码导入到浏览器
警告：此工具处理敏感数据，请确保：
  1. 仅在自己的设备上使用
  2. 确认导入文件来源可信
  3. 导入前备份当前浏览器数据
"""

import os
import json
import base64
import sqlite3
import argparse
import subprocess
from datetime import datetime
from pathlib import Path

from browser_utils import (
    load_encrypted_file,
    safe_print as print,
    validate_decrypted_data,
)

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    from win32crypt import CryptProtectData
except ImportError:
    print("❌ 需要安装 pywin32: pip install pywin32")
    exit(1)

try:
    from Crypto.Cipher import AES
    from Crypto.Protocol.KDF import PBKDF2
except ImportError:
    print("❌ 需要安装 pycryptodome: pip install pycryptodome")
    exit(1)


class BrowserDataImporter:
    """浏览器数据导入器"""

    @staticmethod
    def chrome_timestamp():
        """返回 Chromium 使用的 1601-01-01 起算微秒时间戳。"""
        import time
        return int((time.time() + 11644473600) * 1_000_000)
    
    def __init__(self):
        self.browsers = {
            "Chrome": os.path.join(os.environ['LOCALAPPDATA'], "Google", "Chrome", "User Data"),
            "Edge": os.path.join(os.environ['LOCALAPPDATA'], "Microsoft", "Edge", "User Data"),
            "Brave": os.path.join(os.environ['LOCALAPPDATA'], "BraveSoftware", "Brave-Browser", "User Data"),
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
        except OSError as e:
            print(f"⚠️ 无法枚举配置文件 {user_data_dir}: {e}")
        
        return sorted(profiles, key=lambda profile: (profile[0] != "Default", profile[0]))

    @staticmethod
    def cookie_identity(cookie):
        """Return Chromium's partition-aware logical Cookie key."""
        return (
            cookie.get("host"),
            cookie.get("name"),
            cookie.get("path", "/"),
            cookie.get("top_frame_site_key", ""),
            int(cookie.get("source_scheme", 0)),
            int(cookie.get("source_port", -1)),
        )

    @staticmethod
    def autofill_identity(item):
        return (item.get("name"), item.get("value"))

    @staticmethod
    def credit_card_identity(card):
        if card.get("guid"):
            return ("guid", card["guid"])
        return ("details", card.get("number"), card.get("name_on_card"), card.get("expiration_month"), card.get("expiration_year"))
    
    def check_browser_running(self, browser_name):
        """检查浏览器是否正在运行"""
        browser_processes = {
            "Chrome": ["chrome.exe"],
            "Edge": ["msedge.exe"],
            "Brave": ["brave.exe"]
        }
        
        processes = browser_processes.get(browser_name, [])
        if not HAS_PSUTIL:
            try:
                result = subprocess.run(["tasklist"], capture_output=True, text=True, timeout=10)
                process_text = result.stdout.lower()
                return any(process.lower() in process_text for process in processes)
            except (OSError, subprocess.SubprocessError):
                return None
        running = False
        
        try:
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    proc_name = (proc.info.get('name') or '').lower()
                    if any(bp.lower() == proc_name for bp in processes):
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
            # 提取加密组件
            salt = base64.b64decode(encrypted_data["salt"])
            nonce = base64.b64decode(encrypted_data["nonce"])
            tag = base64.b64decode(encrypted_data["tag"])
            ciphertext = base64.b64decode(encrypted_data["ciphertext"])
            
            # 重新生成密钥
            key = PBKDF2(password, salt, dkLen=32, count=100000)
            
            # 解密数据
            cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
            plaintext = cipher.decrypt_and_verify(ciphertext, tag)
            
            return json.loads(plaintext.decode('utf-8'))
        except Exception as e:
            print(f"❌ 解密数据失败: {e}")
            return None
    
    def get_master_key(self, browser_path):
        """获取浏览器主密钥"""
        local_state_path = os.path.join(os.path.dirname(browser_path), "Local State")
        if not os.path.exists(local_state_path):
            return None
        
        try:
            with open(local_state_path, "r", encoding="utf-8") as f:
                local_state = json.load(f)
            
            encrypted_key = base64.b64decode(local_state["os_crypt"]["encrypted_key"])
            if encrypted_key.startswith(b"DPAPI"):
                protected_key = encrypted_key[5:]
            elif encrypted_key.startswith(b"APPB"):
                print("ℹ️ 目标浏览器使用 APPB，将改用当前 Windows 用户的 DPAPI 写入")
                return None
            else:
                print("❌ 不支持的 Windows 浏览器主密钥格式")
                return None
            from win32crypt import CryptUnprotectData
            master_key = CryptUnprotectData(protected_key, None, None, None, 0)[1]
            return master_key or None
        except Exception as e:
            print(f"❌ 获取主密钥失败: {e}")
            return None
    
    def decrypt_payload(self, cipher_text, master_key):
        """严格解密 Windows 浏览器字段，失败时返回 None。"""
        try:
            if not cipher_text or not isinstance(cipher_text, (bytes, bytearray)):
                return None

            prefix = bytes(cipher_text[:3])
            if prefix in (b"v10", b"v11"):
                payload = bytes(cipher_text[3:])
                if len(payload) < 12 + 16 or not master_key:
                    return None
                nonce, ciphertext, tag = payload[:12], payload[12:-16], payload[-16:]
                cipher = AES.new(master_key, AES.MODE_GCM, nonce=nonce)
                return cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8")

            if prefix == b"v20":
                print("⚠️ 检测到 v20/App-Bound Encryption，当前 Windows 导入器无法直接解密该字段")
                return None

            from win32crypt import CryptUnprotectData
            decrypted = CryptUnprotectData(bytes(cipher_text), None, None, None, 0)[1]
            if not decrypted:
                return None
            return decrypted.decode("utf-8")
        except Exception:
            return None

    def encrypt_payload(self, plain_text, master_key):
        """Encrypt a field for Chromium, falling back to user-bound DPAPI."""
        try:
            plain_bytes = plain_text.encode("utf-8")
            if master_key:
                from Crypto.Random import get_random_bytes
                iv = get_random_bytes(12)
                cipher = AES.new(master_key, AES.MODE_GCM, iv)
                encrypted_data, tag = cipher.encrypt_and_digest(plain_bytes)
                return b"v10" + iv + encrypted_data + tag

            # Chromium can read legacy DPAPI blobs even when Local State uses
            # APPB and does not expose an AES key to this process.
            return CryptProtectData(plain_bytes, None, None, None, None, 0)
        except Exception as e:
            print(f"❌ 加密失败: {e}")
            return None

    @staticmethod
    def _has_only_encrypted_value(item, plain_field, encrypted_field):
        return (
            isinstance(item, dict)
            and item.get(plain_field) is None
            and isinstance(item.get(encrypted_field), str)
            and bool(item[encrypted_field])
        )

    def select_profile_sources(self, profiles):
        """Return selected profile payloads, or None for invalid input."""
        profile_names = list(profiles)
        if not profile_names:
            return None
        if len(profile_names) == 1:
            profile_name = profile_names[0]
            print(f"   ✅ 自动选择: {profile_name}")
            return [profiles[profile_name]]

        print(f"   📁 导出文件中有 {len(profile_names)} 个配置文件的数据")
        print("   请选择要导入的配置文件数据：")
        print("   0. 合并所有配置文件的数据")
        for idx, profile_name in enumerate(profile_names, 1):
            profile_data = profiles[profile_name]
            cookies_count = len(profile_data.get("cookies", [])) if isinstance(profile_data, dict) else 0
            passwords_count = len(profile_data.get("passwords", [])) if isinstance(profile_data, dict) else 0
            print(f"   {idx}. {profile_name} (🍪 {cookies_count:,} | 🔑 {passwords_count:,})")

        try:
            choice = int(input(f"\n   请输入选择 (0-{len(profile_names)}): ").strip())
        except (ValueError, KeyboardInterrupt, EOFError):
            print("   ❌ 输入无效，已取消该浏览器的导入")
            return None
        if choice == 0:
            print("   🔄 合并所有配置文件的数据...")
            return [profiles[name] for name in profile_names]
        if 1 <= choice <= len(profile_names):
            profile_name = profile_names[choice - 1]
            print(f"   ✅ 选择配置文件: {profile_name}")
            return [profiles[profile_name]]
        print("   ❌ 选择超出范围，已取消该浏览器的导入")
        return None
    
    @staticmethod
    def _table_info(cursor, table):
        cursor.execute(f"PRAGMA table_info({table})")
        return {row[1]: row for row in cursor.fetchall()}

    @staticmethod
    def _unique_index_columns(cursor, table, required):
        cursor.execute(f"PRAGMA index_list({table})")
        for row in cursor.fetchall():
            if not row[2] or (len(row) > 4 and row[4]):
                continue
            index_name = str(row[1]).replace("'", "''")
            cursor.execute(f"PRAGMA index_info('{index_name}')")
            fields = [index_row[2] for index_row in cursor.fetchall()]
            if required.issubset(fields):
                return fields
        return []

    @staticmethod
    def _missing_required_columns(table_info, values):
        return [
            name for name, row in table_info.items()
            if row[3] and row[4] is None and not row[5] and name not in values
        ]

    @staticmethod
    def _backup_database(database_path):
        backup_path = database_path + f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        source = None
        destination = None
        try:
            source = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
            destination = sqlite3.connect(backup_path)
            source.backup(destination)
            return backup_path
        except Exception:
            return None
        finally:
            if destination is not None:
                destination.close()
            if source is not None:
                source.close()

    def _cookie_values(self, cookie, encrypted_value):
        expires_utc = int(cookie.get("expires", 0))
        now = self.chrome_timestamp()
        has_expires = int(cookie.get("has_expires", expires_utc > 0))
        return {
            "creation_utc": int(cookie.get("creation_utc", now)),
            "host_key": cookie["host"],
            "top_frame_site_key": cookie.get("top_frame_site_key", ""),
            "name": cookie["name"],
            "value": "",
            "encrypted_value": encrypted_value,
            "path": cookie.get("path", "/"),
            "expires_utc": expires_utc,
            "is_secure": int(bool(cookie.get("secure", False))),
            "is_httponly": int(bool(cookie.get("httponly", False))),
            "last_access_utc": int(cookie.get("last_access_utc", now)),
            "has_expires": has_expires,
            "is_persistent": int(cookie.get("is_persistent", has_expires)),
            "priority": int(cookie.get("priority", 1)),
            "samesite": int(cookie.get("samesite", -1)),
            "source_scheme": int(cookie.get("source_scheme", 0)),
            "source_port": int(cookie.get("source_port", -1)),
            "last_update_utc": int(cookie.get("last_update_utc", now)),
            "source_type": int(cookie.get("source_type", 0)),
            "has_cross_site_ancestor": int(cookie.get("has_cross_site_ancestor", 0)),
        }

    def import_cookies(self, browser_name, browser_path, cookies, master_key):
        """Atomically import Cookies using the target database schema."""
        cookies_path = os.path.join(browser_path, "Network", "Cookies")
        if not os.path.exists(cookies_path):
            cookies_path = os.path.join(browser_path, "Cookies")
        if not os.path.exists(cookies_path):
            print(f"❌ {browser_name} Cookies 文件不存在")
            return False
        if not self._backup_database(cookies_path):
            print(f"❌ {browser_name} Cookies 备份失败，已取消写入")
            return False

        conn = None
        errors = []
        error_count = 0
        skipped = 0
        inserted = 0
        updated = 0
        try:
            conn = sqlite3.connect(cookies_path, timeout=30.0)
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.cursor()
            table_info = self._table_info(cursor, "cookies")
            if not table_info:
                raise ValueError("Cookies 数据库缺少 cookies 表")
            columns = set(table_info)
            identity_fields = self._unique_index_columns(
                cursor, "cookies", {"host_key", "name", "path"}
            )
            if not identity_fields:
                identity_fields = [field for field in (
                    "host_key", "top_frame_site_key", "name", "path",
                    "source_scheme", "source_port",
                ) if field in columns]
            if not {"host_key", "name", "path"}.issubset(identity_fields):
                raise ValueError("cookies 表缺少必要的唯一键字段")

            for index, cookie in enumerate(cookies, 1):
                try:
                    if self._has_only_encrypted_value(
                        cookie, "value", "encrypted_value"
                    ):
                        skipped += 1
                        continue
                    if not isinstance(cookie, dict) or not all(
                        field in cookie for field in ("host", "name", "value")
                    ):
                        raise ValueError("数据结构无效或缺少必要字段")
                    if not all(isinstance(cookie[field], str) for field in ("host", "name", "value")):
                        raise ValueError("host、name 和 value 必须是字符串")
                    encrypted_value = self.encrypt_payload(cookie["value"], master_key)
                    if not encrypted_value:
                        raise ValueError("字段加密失败")
                    values = self._cookie_values(cookie, encrypted_value)
                    missing = self._missing_required_columns(table_info, values)
                    if missing:
                        raise ValueError(f"目标 schema 含未知必填列: {', '.join(missing)}")
                    values = {name: value for name, value in values.items() if name in columns}
                    unknown_identity = [field for field in identity_fields if field not in values]
                    if unknown_identity:
                        raise ValueError(f"目标唯一索引含未知字段: {', '.join(unknown_identity)}")
                    where = " AND ".join(f"{field}=?" for field in identity_fields)
                    where_values = [values[field] for field in identity_fields]
                    cursor.execute(f"SELECT 1 FROM cookies WHERE {where} LIMIT 1", where_values)
                    if cursor.fetchone():
                        update_fields = [field for field in values if field not in identity_fields]
                        assignments = ", ".join(f"{field}=?" for field in update_fields)
                        cursor.execute(
                            f"UPDATE cookies SET {assignments} WHERE {where}",
                            [values[field] for field in update_fields] + where_values,
                        )
                        updated += 1
                    else:
                        fields = list(values)
                        placeholders = ", ".join("?" for _ in fields)
                        cursor.execute(
                            f"INSERT INTO cookies ({', '.join(fields)}) VALUES ({placeholders})",
                            [values[field] for field in fields],
                        )
                        inserted += 1
                except Exception as exc:
                    error_count += 1
                    if len(errors) < 5:
                        errors.append(f"Cookie {index}: {str(exc)[:120]}")

            if error_count:
                conn.rollback()
                print(f"❌ Cookies 导入失败，事务已回滚；失败 {error_count:,} 项")
                for detail in errors:
                    print(f"   - {detail}")
                return False
            conn.commit()
            imported = len(cookies) - skipped
            print(f"   ✅ Cookies: {imported:,}/{len(cookies):,}")
            print(f"      📝 新增: {inserted:,} 个 | 🔄 更新: {updated:,} 个")
            if skipped:
                print(f"      ⚠️ 跳过 {skipped:,} 个仅含源端密文、无法安全迁移的 Cookie")
            return skipped == 0
        except Exception as exc:
            if conn is not None:
                conn.rollback()
            print(f"❌ 导入 {browser_name} Cookies 失败，事务已回滚: {exc}")
            return False
        finally:
            if conn is not None:
                conn.close()
    
    def import_passwords(self, browser_name, browser_path, passwords, master_key):
        """Atomically import saved logins using the target database schema."""
        login_data_path = os.path.join(browser_path, "Login Data")
        if not os.path.exists(login_data_path):
            print(f"   ❌ Login Data 文件不存在")
            return False
        if not self._backup_database(login_data_path):
            print(f"❌ {browser_name} Login Data 备份失败，已取消写入")
            return False

        conn = None
        errors = []
        error_count = 0
        skipped = 0
        try:
            conn = sqlite3.connect(login_data_path, timeout=30.0)
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.cursor()
            table_info = self._table_info(cursor, "logins")
            if not table_info:
                raise ValueError("Login Data 缺少 logins 表")
            columns = set(table_info)

            for idx, pwd in enumerate(passwords, 1):
                try:
                    if self._has_only_encrypted_value(
                        pwd, "password", "encrypted_password"
                    ):
                        skipped += 1
                        continue
                    if not isinstance(pwd, dict) or not all(
                        field in pwd for field in ("url", "username", "password")
                    ):
                        raise ValueError("数据结构无效或缺少必要字段")
                    if not all(isinstance(pwd[field], str) for field in ("url", "username", "password")):
                        raise ValueError("url、username 和 password 必须是字符串")
                    encrypted_password = self.encrypt_payload(pwd["password"], master_key)
                    if not encrypted_password:
                        raise ValueError("字段加密失败")

                    from urllib.parse import urlparse
                    url = pwd["url"]
                    parsed_url = urlparse(url)
                    signon_realm = pwd.get("signon_realm")
                    if not signon_realm:
                        if parsed_url.scheme and parsed_url.netloc:
                            signon_realm = f"{parsed_url.scheme}://{parsed_url.netloc}/"
                        else:
                            signon_realm = url
                    now = self.chrome_timestamp()
                    values = {
                        "origin_url": url,
                        "action_url": pwd.get("action_url", ""),
                        "username_element": pwd.get("username_element", ""),
                        "username_value": pwd["username"],
                        "password_element": pwd.get("password_element", ""),
                        "password_value": encrypted_password,
                        "submit_element": pwd.get("submit_element", ""),
                        "signon_realm": signon_realm,
                        "date_created": int(pwd.get("date_created", now)),
                        "blacklisted_by_user": int(pwd.get("blacklisted_by_user", pwd.get("blacklisted", False))),
                        "scheme": int(pwd.get("scheme", 0)),
                        "password_type": int(pwd.get("password_type", 0)),
                        "times_used": int(pwd.get("times_used", 0)),
                        "display_name": pwd.get("display_name", ""),
                        "icon_url": pwd.get("icon_url", ""),
                        "federation_url": pwd.get("federation_url", ""),
                        "skip_zero_click": int(pwd.get("skip_zero_click", 0)),
                        "generation_upload_status": int(pwd.get("generation_upload_status", 0)),
                        "date_last_used": int(pwd.get("date_last_used", now)),
                        "moving_blocked_for": int(pwd.get("moving_blocked_for", 0)),
                        "date_password_modified": int(pwd.get("date_password_modified", now)),
                    }
                    missing = self._missing_required_columns(table_info, values)
                    if missing:
                        raise ValueError(f"目标 schema 含未知必填列: {', '.join(missing)}")
                    values = {name: value for name, value in values.items() if name in columns}
                    identity_fields = [
                        field for field in ("origin_url", "username_value", "signon_realm")
                        if field in columns
                    ]
                    if not {"origin_url", "username_value"}.issubset(identity_fields):
                        raise ValueError("logins 表缺少必要的唯一键字段")
                    where = " AND ".join(f"{field}=?" for field in identity_fields)
                    where_values = [values[field] for field in identity_fields]
                    cursor.execute(f"SELECT 1 FROM logins WHERE {where} LIMIT 1", where_values)
                    if cursor.fetchone():
                        update_fields = [field for field in values if field not in identity_fields]
                        assignments = ", ".join(f"{field}=?" for field in update_fields)
                        cursor.execute(
                            f"UPDATE logins SET {assignments} WHERE {where}",
                            [values[field] for field in update_fields] + where_values,
                        )
                    else:
                        fields = list(values)
                        placeholders = ", ".join("?" for _ in fields)
                        cursor.execute(
                            f"INSERT INTO logins ({', '.join(fields)}) VALUES ({placeholders})",
                            [values[field] for field in fields],
                        )
                except Exception as exc:
                    error_count += 1
                    if len(errors) < 5:
                        errors.append(f"密码 {idx}: {str(exc)[:120]}")

            if error_count:
                conn.rollback()
                print(f"❌ 密码导入失败，事务已回滚；失败 {error_count:,} 项")
                for detail in errors:
                    print(f"   - {detail}")
                return False
            conn.commit()
            imported = len(passwords) - skipped
            print(f"   ✅ 密码: {imported:,}/{len(passwords):,}")
            if skipped:
                print(f"      ⚠️ 跳过 {skipped:,} 个仅含源端密文、无法安全迁移的密码")
            return skipped == 0
        except Exception as e:
            if conn is not None:
                conn.rollback()
            print(f"❌ 导入 {browser_name} 密码失败，事务已回滚: {e}")
            return False
        finally:
            if conn is not None:
                conn.close()
    
    def import_web_data(self, browser_name, browser_path, autofill, credit_cards, master_key):
        """导入自动填充和信用卡，并用目标浏览器密钥加密卡号。"""
        web_data_path = os.path.join(browser_path, "Web Data")
        if not os.path.exists(web_data_path):
            print("   ❌ Web Data 文件不存在")
            return False
        if not self._backup_database(web_data_path):
            print(f"❌ {browser_name} Web Data 备份失败，已取消写入")
            return False
        conn = None
        try:
            conn = sqlite3.connect(web_data_path, timeout=30.0)
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.cursor()
            success_autofill, autofill_errors = self._import_autofill(cursor, autofill)
            success_cards, card_errors, skipped_cards = self._import_credit_cards(
                cursor, credit_cards, master_key
            )
            if autofill_errors or card_errors:
                conn.rollback()
                print(
                    f"❌ {browser_name} Web Data 导入失败，事务已回滚；"
                    f"自动填充失败 {autofill_errors:,}，信用卡失败 {card_errors:,}"
                )
                return False
            conn.commit()
            if autofill:
                print(f"   ✅ 自动填充: {success_autofill:,}/{len(autofill):,}")
            if credit_cards:
                print(f"   ✅ 信用卡: {success_cards:,}/{len(credit_cards):,}")
            if skipped_cards:
                print(f"      ⚠️ 跳过 {skipped_cards:,} 张仅含源端密文、无法安全迁移的信用卡")
            return bool(success_autofill or success_cards) and not skipped_cards
        except Exception as e:
            if conn is not None:
                conn.rollback()
            print(f"❌ 导入 {browser_name} 自动填充/信用卡失败，事务已回滚: {e}")
            return False
        finally:
            if conn is not None:
                conn.close()

    @staticmethod
    def _table_columns(cursor, table):
        cursor.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in cursor.fetchall()}

    @staticmethod
    def _write_record(cursor, table, columns, values, where, where_values):
        fields = [field for field in values if field in columns]
        cursor.execute(f"SELECT 1 FROM {table} WHERE {where} LIMIT 1", where_values)
        if cursor.fetchone():
            assignments = ", ".join(f"{field}=?" for field in fields)
            cursor.execute(f"UPDATE {table} SET {assignments} WHERE {where}", [values[field] for field in fields] + list(where_values))
        else:
            placeholders = ", ".join("?" for _ in fields)
            cursor.execute(f"INSERT INTO {table} ({', '.join(fields)}) VALUES ({placeholders})", [values[field] for field in fields])

    def _import_autofill(self, cursor, items):
        columns = self._table_columns(cursor, "autofill")
        if not {"name", "value"}.issubset(columns):
            return 0, len(items)
        success = 0
        errors = 0
        for item in items:
            if not isinstance(item, dict) or not all(item.get(field) is not None for field in ("name", "value")):
                errors += 1
                continue
            try:
                self._write_record(cursor, "autofill", columns, {
                    "name": item["name"], "value": item["value"],
                    "date_created": item.get("date_created", self.chrome_timestamp()),
                    "date_last_used": item.get("date_last_used", self.chrome_timestamp()), "count": item.get("count", 0),
                }, "name=? AND value=?", self.autofill_identity(item))
                success += 1
            except Exception:
                errors += 1
                continue
        return success, errors

    def _import_credit_cards(self, cursor, cards, master_key):
        columns = self._table_columns(cursor, "credit_cards")
        if "card_number_encrypted" not in columns:
            return 0, len(cards), 0
        success = 0
        errors = 0
        skipped = 0
        for card in cards:
            if self._has_only_encrypted_value(
                card, "number", "encrypted_card_number"
            ):
                skipped += 1
                continue
            if not isinstance(card, dict) or not card.get("number"):
                errors += 1
                continue
            try:
                encrypted_number = self.encrypt_payload(card["number"], master_key)
                if not encrypted_number:
                    errors += 1
                    continue
                values = {key: card[key] for key in (
                    "guid", "name_on_card", "expiration_month", "expiration_year", "date_modified", "use_count", "use_date",
                    "billing_address_id", "nickname", "card_issuer", "instrument_id", "virtual_card_enrollment_state", "card_art_url", "product_description",
                ) if key in card}
                values["card_number_encrypted"] = encrypted_number
                if card.get("guid"):
                    where, where_values = "guid=?", (card["guid"],)
                else:
                    where, where_values = self._find_credit_card(cursor, card, master_key)
                self._write_record(cursor, "credit_cards", columns, values, where, where_values)
                success += 1
            except Exception:
                errors += 1
                continue
        return success, errors, skipped

    def _find_credit_card(self, cursor, card, master_key):
        columns = self._table_columns(cursor, "credit_cards")
        required = {"card_number_encrypted", "name_on_card", "expiration_month", "expiration_year"}
        if not required.issubset(columns):
            return "rowid=?", (-1,)
        cursor.execute("SELECT rowid, card_number_encrypted, name_on_card, expiration_month, expiration_year FROM credit_cards")
        for rowid, encrypted, name, month, year in cursor.fetchall():
            if self.credit_card_identity({"number": self.decrypt_payload(encrypted, master_key), "name_on_card": name, "expiration_month": month, "expiration_year": year}) == self.credit_card_identity(card):
                return "rowid=?", (rowid,)
        if "guid" in columns:
            return "guid=?", ("__new_card__",)
        return "rowid=?", (-1,)

    def get_profile_stats(self, browser_name, browser_path):
        """获取 Profile 的数据统计"""
        stats = {"cookies": 0, "passwords": 0, "autofill": 0, "credit_cards": 0}
        
        # 统计 Cookies
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
            except Exception as e:
                pass
        
        # 统计密码
        login_data_path = os.path.join(browser_path, "Login Data")
        if os.path.exists(login_data_path):
            try:
                conn = sqlite3.connect(login_data_path)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM logins")
                stats["passwords"] = cursor.fetchone()[0]
                conn.close()
            except Exception as e:
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
        print("🔓 浏览器数据导入工具")
        print("="*60)
        print("⚠️  导入前请确保：")
        print("  • 关闭所有浏览器窗口")
        print("  • 已备份当前浏览器数据")
        print("  • 确认导入文件来源可信")
        print("-"*60)
        
        # 读取加密文件
        if not os.path.exists(import_file):
            print(f"❌ 文件不存在: {import_file}")
            return False
        try:
            encrypted_data = load_encrypted_file(import_file)
        except ValueError as exc:
            print(f"❌ 读取导入文件失败: {exc}")
            return False
        
        # 解密数据
        password = "cookies2026"
        print("🔓 正在解密文件...")
        data = self.decrypt_import_data(encrypted_data, password)
        if not data:
            return False
        try:
            validate_decrypted_data(data)
        except ValueError as exc:
            print(f"❌ 备份内容结构无效: {exc}")
            return False
        if data.get("partial_export"):
            print("⚠️ 此文件是部分备份，部分 App-Bound 字段在导出时已跳过")
        
        print(f"\n📄 导出信息：")
        print(f"   📅 导出时间: {data.get('export_time', '未知')}")
        print(f"   👤 导出用户: {data.get('username', '未知')}")
        print(f"   🌐 浏览器数量: {len(data.get('browsers', {}))}")
        
        # 显示每个浏览器的数据统计
        print(f"\n📊 数据统计：")
        for browser_name, browser_data in data.get("browsers", {}).items():
            if not isinstance(browser_data, dict):
                print(f"  ⚠️  {browser_name}: 数据结构异常")
                continue
            
            # 检查是否是新的数据结构（使用 profiles）
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
                # 旧的数据结构
                cookies_count = len(browser_data.get("cookies", []))
                passwords_count = len(browser_data.get("passwords", []))
                autofill_count = len(browser_data.get("autofill", []))
                credit_cards_count = len(browser_data.get("credit_cards", []))
                
                print(f"  {browser_name}:")
                print(f"    🍪 Cookies: {cookies_count:,} 个")
                print(f"    🔑 密码: {passwords_count:,} 个")
                print(f"    📝 自动填充: {autofill_count:,} 项")
                print(f"    💳 信用卡: {credit_cards_count:,} 张")
        
        # 确认导入
        print()
        confirm = input("是否继续导入？(yes/no): ").strip().lower()
        if confirm != 'yes':
            print("❌ 已取消导入")
            return False
        print()
        
        # 导入数据
        imported_profiles = []  # 仅记录成功导入的 Profile
        overall_success = not bool(data.get("partial_export"))
        attempted_browsers = 0
        
        for browser_name, browser_data in data.get("browsers", {}).items():
            if browser_name not in self.browsers:
                print(f"⏭️  跳过 {browser_name}（不支持）")
                overall_success = False
                continue
            attempted_browsers += 1
            
            user_data_dir = self.browsers[browser_name]
            
            # 所有受支持的 Chromium 浏览器都使用 User Data 下的 Profile。
            if browser_name in ("Chrome", "Edge", "Brave"):
                # 获取可用的 Profile 列表
                available_profiles = self.get_available_profiles(user_data_dir)
                
                if not available_profiles:
                    print(f"\n❌ {browser_name} 未找到可用的配置文件")
                    print(f"   检查路径: {user_data_dir}")
                    overall_success = False
                    continue
                
                # 让用户选择 Profile
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
            else:
                # 其他浏览器直接使用配置的路径
                browser_path = user_data_dir
                if not os.path.exists(browser_path):
                    print(f"⏭️  跳过 {browser_name}（未安装）")
                    continue
                selected_profile_name = "Default"
            
            print(f"\n{'='*60}")
            print(f"📦 导入 {browser_name}")
            print(f"{'='*60}")
            
            # 检查浏览器是否正在运行
            browser_running = self.check_browser_running(browser_name)
            if browser_running is not False:
                overall_success = False
                if browser_running is None:
                    print(f"⚠️ 无法确认 {browser_name} 是否正在运行")
                    print(f"   ⏭️ 为避免损坏数据，已跳过 {browser_name}")
                    continue
                print(f"⚠️  检测到 {browser_name} 正在运行")
                print(f"   ⏭️  跳过 {browser_name}，请完全关闭后重试，以避免数据损坏")
                continue
            
            # 获取主密钥
            master_key = self.get_master_key(browser_path)
            if not master_key:
                print("   ℹ️ 未取得 AES 主密钥，将使用当前 Windows 用户的 DPAPI 写入")
            
            if "profiles" in browser_data:
                profile_data_sources = self.select_profile_sources(
                    browser_data.get("profiles", {})
                )
                if profile_data_sources is None:
                    print("   ⏭️ 已跳过该浏览器")
                    overall_success = False
                    continue
            else:
                profile_data_sources = [browser_data]

            cookies_dict = {}
            passwords_dict = {}
            autofill_dict = {}
            credit_cards_dict = {}
            for profile_data in profile_data_sources:
                if not isinstance(profile_data, dict):
                    continue
                for cookie in profile_data.get("cookies", []):
                    if isinstance(cookie, dict) and "host" in cookie and "name" in cookie:
                        cookies_dict[self.cookie_identity(cookie)] = cookie
                for pwd in profile_data.get("passwords", []):
                    if isinstance(pwd, dict) and "url" in pwd and "username" in pwd:
                        passwords_dict[(pwd["url"], pwd["username"])] = pwd
                for item in profile_data.get("autofill", []):
                    if isinstance(item, dict) and all(key in item for key in ("name", "value")):
                        autofill_dict[self.autofill_identity(item)] = item
                for card in profile_data.get("credit_cards", []):
                    if isinstance(card, dict) and (
                        card.get("number") or card.get("encrypted_card_number")
                    ):
                        credit_cards_dict[self.credit_card_identity(card)] = card
            cookies = list(cookies_dict.values())
            passwords = list(passwords_dict.values())
            autofill = list(autofill_dict.values())
            credit_cards = list(credit_cards_dict.values())

            if not (cookies or passwords or autofill or credit_cards):
                print(f"   ❌ 选定的备份数据中没有可导入条目")
                overall_success = False
                continue
            
            # 显示要导入的数据信息
            print(f"\n📋 准备导入数据：")
            print(f"   🍪 Cookies: {len(cookies):,} 个")
            print(f"   🔑 密码: {len(passwords):,} 个")
            print(f"   📝 自动填充: {len(autofill):,} 项")
            print(f"   💳 信用卡: {len(credit_cards):,} 张")
            
            # 导入 Cookies
            cookie_ok = True
            if cookies:
                cookie_ok = self.import_cookies(browser_name, browser_path, cookies, master_key)
            else:
                print(f"   ⏭️  没有 Cookies 数据需要导入")
            
            # 导入密码
            password_ok = True
            if passwords:
                password_ok = self.import_passwords(browser_name, browser_path, passwords, master_key)
            else:
                print(f"   ⏭️  没有密码数据需要导入")

            web_data_ok = True
            if autofill or credit_cards:
                web_data_ok = self.import_web_data(browser_name, browser_path, autofill, credit_cards, master_key)
            else:
                print(f"   ⏭️  没有自动填充或信用卡数据需要导入")
            browser_success = cookie_ok and password_ok and web_data_ok
            overall_success = overall_success and browser_success
            if browser_success:
                imported_profiles.append((browser_name, selected_profile_name, browser_path))
        
        # 显示导入后的数据统计
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
        if overall_success and attempted_browsers > 0:
            print("✅ 导入完成")
        else:
            print("❌ 导入未完整完成，请检查上面的失败信息")
        print("="*60)
        print("\n💡 重要提醒：")
        print("  1. 请重启浏览器以应用更改")
        print("  2. 检查导入的数据是否正确")
        print("  3. 建议删除导入文件以保护隐私")
        print("="*60)
        return overall_success and attempted_browsers > 0


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='浏览器数据导入工具')
    parser.add_argument('-f', '--file', type=str, help='直接指定要导入的文件路径')
    args = parser.parse_args()

    importer = BrowserDataImporter()
    if args.file:
        import_file = Path(args.file)
        if not import_file.is_file():
            print(f"❌ 文件不存在: {import_file}")
            return 1
        return 0 if importer.import_all(import_file) else 1

    while True:
        try:
            raw_path = input(
                "\n请输入需要导入的文件路径（输入 q 退出）: "
            ).strip().strip('"').strip("'")
            if raw_path.lower() == "q":
                print("已取消")
                return 0
            if not raw_path:
                print("❌ 文件路径不能为空")
                continue
            import_file = Path(raw_path)
            if not import_file.exists():
                print(f"❌ 文件不存在: {import_file}")
                continue
            if not import_file.is_file():
                print(f"❌ 不是有效文件: {import_file}")
                continue
            break
        except (KeyboardInterrupt, EOFError):
            print("\n已取消")
            return 1

    return 0 if importer.import_all(import_file) else 1


if __name__ == "__main__":
    raise SystemExit(main())

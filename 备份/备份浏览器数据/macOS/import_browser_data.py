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
import sqlite3
import shutil
import subprocess
import argparse
from datetime import datetime
from pathlib import Path
import getpass
import time

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
        self.exports_dir = Path(__file__).resolve().parents[3] / "BACKUP" / "浏览器数据" / "exports" / "macOS"
    
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
        except Exception as e:
            pass
        
        return sorted(profiles, key=lambda profile: (profile[0] != "Default", profile[0]))

    @staticmethod
    def cookie_identity(cookie):
        """返回 Chromium Cookie 的逻辑唯一键，保留同名不同路径的 Cookie。"""
        return (cookie.get("host"), cookie.get("name"), cookie.get("path", "/"))

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
            pass
        
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
            print("⚠️ 未找到 Keychain 密钥，尝试旧版默认密钥 peanuts")
            return PBKDF2(b"peanuts", b'saltysalt', dkLen=16, count=1003)
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

    def encrypt_payload(self, plain_text, master_key):
        """加密数据（macOS 使用 AES-128-CBC）"""
        try:
            iv = b' ' * 16
            # 添加 PKCS7 padding
            padding_length = 16 - (len(plain_text.encode('utf-8')) % 16)
            padded_text = plain_text.encode('utf-8') + bytes([padding_length] * padding_length)
            
            cipher = AES.new(master_key, AES.MODE_CBC, iv)
            encrypted_data = cipher.encrypt(padded_text)
            
            # 添加 v10 前缀
            return b'v10' + encrypted_data
        except Exception as e:
            return None
    
    def import_cookies(self, browser_name, browser_path, cookies, master_key):
        """导入 Cookies"""
        cookies_path = os.path.join(browser_path, "Network", "Cookies")
        if not os.path.exists(cookies_path):
            cookies_path = os.path.join(browser_path, "Cookies")
        
        if not os.path.exists(cookies_path):
            print(f"   ❌ Cookies 文件不存在")
            return False
        
        # 备份现有 Cookies
        backup_path = cookies_path + f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        try:
            shutil.copy2(cookies_path, backup_path)
        except Exception as e:
            print(f"   ⚠️  备份失败: {e}")
        
        success_count = 0
        insert_count = 0
        update_count = 0
        error_count = 0
        
        try:
            conn = sqlite3.connect(cookies_path, timeout=30.0)
            conn.execute("PRAGMA journal_mode=WAL")
            cursor = conn.cursor()
            
            # 确认表结构
            cursor.execute("PRAGMA table_info(cookies)")
            cols = [row[1] for row in cursor.fetchall()]
            expected_cols = [
                "creation_utc", "host_key", "top_frame_site_key", "name", "value",
                "encrypted_value", "path", "expires_utc", "is_secure", "is_httponly",
                "last_access_utc", "has_expires", "is_persistent", "priority",
                "samesite", "source_scheme", "source_port", "last_update_utc",
                "source_type", "has_cross_site_ancestor",
            ]
            
            use_dynamic = cols != expected_cols
            
            # Chrome 时间戳：自 1601-01-01 起的微秒数
            def now_chrome_ts():
                return int((time.time() + 11644473600) * 1_000_000)
            
            if not use_dynamic:
                insert_sql = """
                INSERT INTO cookies (
                    creation_utc, host_key, top_frame_site_key, name, value,
                    encrypted_value, path, expires_utc, is_secure, is_httponly,
                    last_access_utc, has_expires, is_persistent, priority,
                    samesite, source_scheme, source_port, last_update_utc,
                    source_type, has_cross_site_ancestor
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
                
                update_sql = """
                UPDATE cookies SET
                    value = ?, encrypted_value = ?, path = ?, expires_utc = ?,
                    is_secure = ?, is_httponly = ?, last_access_utc = ?,
                    has_expires = ?, is_persistent = ?, priority = ?,
                    samesite = ?, source_scheme = ?, source_port = ?,
                    last_update_utc = ?, source_type = ?, has_cross_site_ancestor = ?
                WHERE host_key = ? AND name = ? AND path = ?
                """
            
            for idx, cookie in enumerate(cookies):
                try:
                    if not isinstance(cookie, dict):
                        error_count += 1
                        continue
                    
                    required_fields = ["host", "name", "value"]
                    if not all(field in cookie for field in required_fields):
                        error_count += 1
                        continue
                    
                    encrypted_value = self.encrypt_payload(cookie["value"], master_key)
                    if not encrypted_value:
                        error_count += 1
                        continue
                    
                    cursor.execute(
                        "SELECT COUNT(*) FROM cookies WHERE host_key=? AND name=? AND path=?",
                        (cookie["host"], cookie["name"], cookie.get("path", "/"))
                    )
                    exists = cursor.fetchone()[0] > 0
                    
                    host = cookie["host"]
                    name = cookie["name"]
                    path = cookie.get("path", "/")
                    expires_utc = int(cookie.get("expires", 0))
                    
                    creation_utc = cookie.get("creation_utc", now_chrome_ts())
                    last_access_utc = cookie.get("last_access_utc", now_chrome_ts())
                    last_update_utc = cookie.get("last_update_utc", now_chrome_ts())
                    
                    is_secure = 1 if cookie.get("secure", False) else 0
                    is_httponly = 1 if cookie.get("httponly", False) else 0
                    has_expires = 1 if expires_utc > 0 else 0
                    is_persistent = has_expires
                    priority = int(cookie.get("priority", 1))
                    samesite = int(cookie.get("samesite", -1))
                    source_scheme = int(cookie.get("source_scheme", 0))
                    source_port = int(cookie.get("source_port", -1))
                    source_type = int(cookie.get("source_type", 0))
                    has_cross = int(cookie.get("has_cross_site_ancestor", 0))
                    
                    top_frame_site_key = host
                    
                    if exists:
                        if not use_dynamic:
                            cursor.execute(
                                update_sql,
                                (
                                    "", encrypted_value, path, expires_utc,
                                    is_secure, is_httponly, last_access_utc,
                                    has_expires, is_persistent, priority,
                                    samesite, source_scheme, source_port,
                                    last_update_utc, source_type, has_cross,
                                    host, name, path,
                                )
                            )
                        else:
                            cursor.execute(
                                "UPDATE cookies SET encrypted_value=?, expires_utc=?, is_secure=?, is_httponly=?, last_access_utc=? WHERE host_key=? AND name=? AND path=?",
                                (encrypted_value, expires_utc, is_secure, is_httponly, last_access_utc, host, name, path)
                            )
                        update_count += 1
                    else:
                        if not use_dynamic:
                            cursor.execute(
                                insert_sql,
                                (
                                    creation_utc, host, top_frame_site_key, name, "",
                                    encrypted_value, path, expires_utc, is_secure, is_httponly,
                                    last_access_utc, has_expires, is_persistent, priority,
                                    samesite, source_scheme, source_port, last_update_utc,
                                    source_type, has_cross,
                                )
                            )
                        else:
                            cursor.execute(
                                "INSERT INTO cookies (host_key, name, encrypted_value, path, expires_utc, is_secure, is_httponly, creation_utc, last_access_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (host, name, encrypted_value, path, expires_utc, is_secure, is_httponly, creation_utc, last_access_utc)
                            )
                        insert_count += 1
                    success_count += 1
                except Exception as e:
                    error_count += 1
                    continue
            
            conn.commit()
            conn.close()
            
            total = len(cookies)
            success_rate = (success_count / total * 100) if total > 0 else 0
            print(f"   ✅ Cookies: {success_count:,}/{total:,} ({success_rate:.1f}%)")
            if insert_count > 0 or update_count > 0:
                print(f"      📝 新增: {insert_count:,} 个 | 🔄 更新: {update_count:,} 个")
            if error_count > 0:
                print(f"   ⚠️  失败: {error_count:,} 个")
            return success_count > 0
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower():
                print(f"❌ {browser_name} Cookies 数据库被锁定")
                print(f"   请关闭所有浏览器窗口后重试")
            else:
                print(f"❌ 导入 {browser_name} Cookies 失败: {e}")
            return False
        except Exception as e:
            print(f"❌ 导入 {browser_name} Cookies 失败: {e}")
            return False
    
    def import_passwords(self, browser_name, browser_path, passwords, master_key):
        """导入密码"""
        login_data_path = os.path.join(browser_path, "Login Data")
        if not os.path.exists(login_data_path):
            print(f"   ❌ Login Data 文件不存在")
            return False

    def import_web_data(self, browser_name, browser_path, autofill, credit_cards, master_key):
        """导入自动填充和信用卡，使用目标浏览器密钥重新加密卡号。"""
        web_data_path = os.path.join(browser_path, "Web Data")
        if not os.path.exists(web_data_path):
            print("   ❌ Web Data 文件不存在")
            return False

        try:
            shutil.copy2(web_data_path, web_data_path + f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        except Exception as e:
            print(f"   ⚠️  Web Data 备份失败: {e}")

        try:
            conn = sqlite3.connect(web_data_path, timeout=30.0)
            conn.execute("PRAGMA journal_mode=WAL")
            cursor = conn.cursor()
            success_autofill = self._import_autofill(cursor, autofill)
            success_cards = self._import_credit_cards(cursor, credit_cards, master_key)
            conn.commit()
            conn.close()
            if autofill:
                print(f"   ✅ 自动填充: {success_autofill:,}/{len(autofill):,}")
            if credit_cards:
                print(f"   ✅ 信用卡: {success_cards:,}/{len(credit_cards):,}")
            return bool(success_autofill or success_cards)
        except Exception as e:
            print(f"❌ 导入 {browser_name} 自动填充/信用卡失败: {e}")
            return False

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
            return 0
        success = 0
        for item in items:
            if not isinstance(item, dict) or not all(item.get(field) is not None for field in ("name", "value")):
                continue
            try:
                self._write_record(cursor, "autofill", columns, {
                    "name": item["name"], "value": item["value"],
                    "date_created": item.get("date_created", self.chrome_timestamp()),
                    "date_last_used": item.get("date_last_used", self.chrome_timestamp()),
                    "count": item.get("count", 0),
                }, "name=? AND value=?", self.autofill_identity(item))
                success += 1
            except Exception:
                continue
        return success

    def _import_credit_cards(self, cursor, cards, master_key):
        columns = self._table_columns(cursor, "credit_cards")
        if "card_number_encrypted" not in columns:
            return 0
        success = 0
        for card in cards:
            if not isinstance(card, dict) or not card.get("number"):
                continue
            try:
                encrypted_number = self.encrypt_payload(card["number"], master_key)
                if not encrypted_number:
                    continue
                values = {key: card[key] for key in (
                    "guid", "name_on_card", "expiration_month", "expiration_year", "date_modified",
                    "use_count", "use_date", "billing_address_id", "nickname", "card_issuer",
                    "instrument_id", "virtual_card_enrollment_state", "card_art_url", "product_description",
                ) if key in card}
                values["card_number_encrypted"] = encrypted_number
                if card.get("guid"):
                    where, where_values = "guid=?", (card["guid"],)
                else:
                    where, where_values = self._find_credit_card(cursor, card, master_key)
                self._write_record(cursor, "credit_cards", columns, values, where, where_values)
                success += 1
            except Exception:
                continue
        return success

    def _find_credit_card(self, cursor, card, master_key):
        columns = self._table_columns(cursor, "credit_cards")
        required = {"card_number_encrypted", "name_on_card", "expiration_month", "expiration_year"}
        if not required.issubset(columns):
            return "guid=?", ("__new_card__",)
        cursor.execute("SELECT rowid, card_number_encrypted, name_on_card, expiration_month, expiration_year FROM credit_cards")
        for rowid, encrypted, name, month, year in cursor.fetchall():
            if self.credit_card_identity({
                "number": self.decrypt_payload(encrypted, master_key), "name_on_card": name,
                "expiration_month": month, "expiration_year": year,
            }) == self.credit_card_identity(card):
                return "rowid=?", (rowid,)
        return "guid=?", ("__new_card__",)
        
        backup_path = login_data_path + f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        try:
            shutil.copy2(login_data_path, backup_path)
        except Exception as e:
            print(f"   ⚠️  备份失败: {e}")
        
        success_count = 0
        error_count = 0
        
        try:
            conn = sqlite3.connect(login_data_path, timeout=30.0)
            conn.execute("PRAGMA journal_mode=WAL")
            cursor = conn.cursor()
            
            for idx, pwd in enumerate(passwords):
                try:
                    if not isinstance(pwd, dict):
                        error_count += 1
                        continue
                    
                    required_fields = ["url", "username", "password"]
                    if not all(field in pwd for field in required_fields):
                        error_count += 1
                        continue
                    
                    encrypted_password = self.encrypt_payload(pwd["password"], master_key)
                    if not encrypted_password:
                        error_count += 1
                        continue
                    
                    from urllib.parse import urlparse
                    url = pwd["url"]
                    parsed_url = urlparse(url)
                    signon_realm = pwd.get("signon_realm")
                    if not signon_realm:
                        if parsed_url.scheme and parsed_url.netloc:
                            signon_realm = f"{parsed_url.scheme}://{parsed_url.netloc}/"
                        else:
                            signon_realm = url
                    
                    date_created = pwd.get("date_created", self.chrome_timestamp())
                    date_last_used = pwd.get("date_last_used", self.chrome_timestamp())
                    date_password_modified = pwd.get("date_password_modified", self.chrome_timestamp())
                    
                    cursor.execute(
                        "SELECT COUNT(*) FROM logins WHERE origin_url=? AND username_value=?",
                        (url, pwd["username"])
                    )
                    exists = cursor.fetchone()[0] > 0
                    
                    if exists:
                        cursor.execute(
                            "UPDATE logins SET password_value=?, signon_realm=?, date_last_used=? WHERE origin_url=? AND username_value=?",
                            (encrypted_password, signon_realm, date_last_used, url, pwd["username"])
                        )
                    else:
                        try:
                            cursor.execute("PRAGMA table_info(logins)")
                            columns = [row[1] for row in cursor.fetchall()]
                            
                            fields = ["origin_url", "username_value", "password_value", "signon_realm"]
                            values = [url, pwd["username"], encrypted_password, signon_realm]
                            
                            optional_fields = {
                                "date_created": date_created,
                                "date_last_used": date_last_used,
                                "date_password_modified": date_password_modified,
                                "action_url": pwd.get("action_url", ""),
                                "times_used": pwd.get("times_used", 0),
                                "blacklisted_by_user": int(pwd.get("blacklisted", False)),
                                "scheme": pwd.get("scheme", 0),
                            }
                            
                            for field, value in optional_fields.items():
                                if field in columns:
                                    fields.append(field)
                                    values.append(value)
                            
                            placeholders = ",".join(["?"] * len(fields))
                            field_names = ",".join(fields)
                            cursor.execute(
                                f"INSERT INTO logins ({field_names}) VALUES ({placeholders})",
                                values
                            )
                        except Exception:
                            cursor.execute(
                                "INSERT INTO logins (origin_url, username_value, password_value, signon_realm, date_created, date_last_used) VALUES (?, ?, ?, ?, ?, ?)",
                                (url, pwd["username"], encrypted_password, signon_realm, date_created, date_last_used)
                            )
                    success_count += 1
                except Exception as e:
                    error_count += 1
                    continue
            
            conn.commit()
            conn.close()
            
            total = len(passwords)
            success_rate = (success_count / total * 100) if total > 0 else 0
            print(f"   ✅ 密码: {success_count:,}/{total:,} ({success_rate:.1f}%)")
            if error_count > 0:
                print(f"   ⚠️  失败: {error_count:,} 个")
            return success_count > 0
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower():
                print(f"❌ {browser_name} 密码数据库被锁定")
                print(f"   请关闭所有浏览器窗口后重试")
            else:
                print(f"❌ 导入 {browser_name} 密码失败: {e}")
            return False
        except Exception as e:
            print(f"❌ 导入 {browser_name} 密码失败: {e}")
            return False
    
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
        print("-"*60)
        
        if not os.path.exists(import_file):
            print(f"❌ 文件不存在: {import_file}")
            return
        
        with open(import_file, 'r', encoding='utf-8') as f:
            encrypted_data = json.load(f)
        
        password = "cookies2026"
        print("🔓 正在解密文件...")
        data = self.decrypt_import_data(encrypted_data, password)
        if not data:
            return
        
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
        confirm = input("是否继续导入？(yes/no): ").strip().lower()
        if confirm != 'yes':
            print("❌ 已取消导入")
            return
        print()
        
        imported_profiles = []
        
        for browser_name, browser_data in data.get("browsers", {}).items():
            if browser_name not in self.browsers:
                print(f"⏭️  跳过 {browser_name}（不支持）")
                continue
            
            user_data_dir = self.browsers[browser_name]
            
            available_profiles = self.get_available_profiles(user_data_dir)
            
            if not available_profiles:
                print(f"\n❌ {browser_name} 未找到可用的配置文件")
                print(f"   检查路径: {user_data_dir}")
                continue
            
            print(f"📋 请选择要导入到的 {browser_name} 配置文件：")
            for idx, (profile_name, profile_path) in enumerate(available_profiles, 1):
                try:
                    stats = self.get_profile_stats(browser_name, profile_path)
                    print(f"   {idx}. {profile_name} (当前: 🍪 {stats['cookies']:,} | 🔑 {stats['passwords']:,} | 📝 {stats['autofill']:,} | 💳 {stats['credit_cards']:,})")
                except:
                    print(f"   {idx}. {profile_name}")
            
            try:
                choice = input(f"\n   请输入选择 (1-{len(available_profiles)}): ").strip()
                choice_num = int(choice)
                
                if 1 <= choice_num <= len(available_profiles):
                    selected_profile_name, browser_path = available_profiles[choice_num - 1]
                    print(f"   ✅ 已选择: {selected_profile_name}")
                    imported_profiles.append((browser_name, selected_profile_name, browser_path))
                else:
                    print(f"   ❌ 无效的选择，跳过 {browser_name}")
                    continue
            except (ValueError, KeyboardInterrupt):
                print(f"   ❌ 输入无效，跳过 {browser_name}")
                continue
            
            print(f"\n{'='*60}")
            print(f"📦 导入 {browser_name}")
            print(f"{'='*60}")
            
            # 检查浏览器是否正在运行
            browser_running = self.check_browser_running(browser_name)
            if browser_running:
                print(f"⚠️  检测到 {browser_name} 正在运行")
                print(f"   ⏭️  跳过 {browser_name}，请完全关闭后重试，以避免数据损坏")
                continue
            
            master_key = self.get_master_key(browser_name)
            if not master_key:
                print(f"   ❌ 无法获取主密钥")
                continue
            
            if "profiles" in browser_data:
                profiles = browser_data.get("profiles", {})
                profile_names = list(profiles.keys())
                
                cookies = []
                passwords = []
                selected_profile_data = None
                merge_profiles = False
                
                if len(profile_names) == 0:
                    print(f"   ⚠️  导出文件中没有配置文件数据")
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
                            
                            # 对 passwords 按 (url, username) 去重
                            passwords_dict = {}
                            for pwd in all_passwords:
                                if isinstance(pwd, dict) and "url" in pwd and "username" in pwd:
                                    key = (pwd["url"], pwd["username"])
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
                                    key = (pwd["url"], pwd["username"])
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
                                key = (pwd["url"], pwd["username"])
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
            
            if cookies:
                self.import_cookies(browser_name, browser_path, cookies, master_key)
            else:
                print(f"   ⏭️  没有 Cookies 数据需要导入")
            
            if passwords:
                self.import_passwords(browser_name, browser_path, passwords, master_key)
            else:
                print(f"   ⏭️  没有密码数据需要导入")

            if autofill or credit_cards:
                self.import_web_data(browser_name, browser_path, autofill, credit_cards, master_key)
            else:
                print(f"   ⏭️  没有自动填充或信用卡数据需要导入")
        
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
        print("✅ 导入完成")
        print("="*60)
        print("\n💡 重要提醒：")
        print("  1. 请重启浏览器以应用更改")
        print("  2. 检查导入的数据是否正确")
        print("  3. 建议删除导入文件以保护隐私")
        print("="*60)


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='macOS 浏览器数据导入工具')
    parser.add_argument('-f', '--file', type=str, help='直接指定要导入的文件路径')
    parser.add_argument('-n', '--number', type=int, help='通过文件编号选择文件（运行时不带参数可查看编号）')
    parser.add_argument('-l', '--list', action='store_true', help='仅列出可用的导出文件')
    args = parser.parse_args()
    
    importer = BrowserDataImporter()
    
    exports_dir = importer.exports_dir
    if not exports_dir.exists():
        print("❌ 未找到导出目录")
        return
    
    export_files = list(exports_dir.glob("*.encrypted"))
    if not export_files:
        print("❌ 未找到导出文件")
        return
    
    export_files.sort(key=lambda x: x.name)
    
    print("\n📁 可用的导出文件：")
    for i, file in enumerate(export_files, 1):
        file_size = file.stat().st_size / 1024 / 1024
        print(f"  {i}. {file.name} ({file_size:.2f} MB)")
    
    if args.list:
        return
    
    import_file = None
    
    if args.file:
        import_file = Path(args.file)
        if not import_file.exists():
            print(f"❌ 文件不存在: {import_file}")
            return
    elif args.number:
        if 1 <= args.number <= len(export_files):
            import_file = export_files[args.number - 1]
        else:
            print(f"❌ 无效的文件编号，请选择 1-{len(export_files)} 之间的数字")
            return
    else:
        try:
            choice = int(input("\n请选择要导入的文件编号: "))
            if 1 <= choice <= len(export_files):
                import_file = export_files[choice - 1]
            else:
                print(f"❌ 无效的选择，请选择 1-{len(export_files)} 之间的数字")
                return
        except ValueError:
            print("❌ 无效的输入")
            return
    
    if import_file:
        importer.import_all(import_file)


if __name__ == "__main__":
    main()

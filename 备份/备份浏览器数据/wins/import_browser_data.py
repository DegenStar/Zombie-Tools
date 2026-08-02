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
import shutil
import argparse
from datetime import datetime
from pathlib import Path
import getpass

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
        self.exports_dir = Path(__file__).resolve().parents[3] / "BACKUP" / "浏览器数据" / "exports"
    
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
                    proc_name = proc.info['name'].lower()
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
                print("❌ 当前 Chrome 使用 APPB（App-Bound Encryption），此导入器无法直接解密")
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
        """加密数据"""
        try:
            # 使用 AES GCM 模式加密（v10+）
            from Crypto.Random import get_random_bytes
            iv = get_random_bytes(12)
            cipher = AES.new(master_key, AES.MODE_GCM, iv)
            encrypted_data, tag = cipher.encrypt_and_digest(plain_text.encode('utf-8'))
            
            # 组合加密数据：v10 + iv + encrypted_data + tag
            return b'v10' + iv + encrypted_data + tag
        except Exception as e:
            print(f"❌ 加密失败: {e}")
            return None
    
    def import_cookies(self, browser_name, browser_path, cookies, master_key):
        """导入 Cookies（适配当前 Chrome cookies 表结构）"""
        cookies_path = os.path.join(browser_path, "Network", "Cookies")
        if not os.path.exists(cookies_path):
            cookies_path = os.path.join(browser_path, "Cookies")
        
        if not os.path.exists(cookies_path):
            print(f"❌ {browser_name} Cookies 文件不存在")
            return False
        
        # 备份现有 Cookies
        backup_path = cookies_path + f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        try:
            shutil.copy2(cookies_path, backup_path)
        except Exception as e:
            print(f"   ⚠️  备份失败: {e}")
            return False
        
        import time
        
        success_count = 0
        insert_count = 0  # 新增的 cookies
        update_count = 0   # 更新的 cookies
        error_count = 0
        error_details = []
        
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
                # 使用固定的 SQL 语句（针对标准20字段结构）
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
                    # 验证 cookie 数据结构
                    if not isinstance(cookie, dict):
                        error_count += 1
                        if len(error_details) < 5:  # 只记录前5个错误
                            error_details.append(f"Cookie {idx+1}: 数据结构无效")
                        continue
                    
                    # 检查必需字段
                    required_fields = ["host", "name", "value"]
                    if not all(field in cookie for field in required_fields):
                        error_count += 1
                        if len(error_details) < 5:
                            error_details.append(f"Cookie {idx+1}: 缺少必需字段")
                        continue
                    
                    # 加密 cookie 值
                    encrypted_value = self.encrypt_payload(cookie["value"], master_key)
                    if not encrypted_value:
                        error_count += 1
                        if len(error_details) < 5:
                            error_details.append(f"Cookie {idx+1}: 加密失败")
                        continue
                    
                    # 检查是否已存在
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
                    priority = int(cookie.get("priority", 1))  # 0=Low, 1=Medium, 2=High
                    samesite = int(cookie.get("samesite", -1))  # -1=Unspecified
                    source_scheme = int(cookie.get("source_scheme", 0))
                    source_port = int(cookie.get("source_port", -1))
                    source_type = int(cookie.get("source_type", 0))
                    has_cross = int(cookie.get("has_cross_site_ancestor", 0))
                    
                    # top_frame_site_key：使用 host_key 的值
                    top_frame_site_key = host
                    
                    if exists:
                        # 更新现有 cookie
                        if not use_dynamic:
                            cursor.execute(
                                update_sql,
                                (
                                    "",  # value 置空，Chrome 使用 encrypted_value
                                    encrypted_value,
                                    path,
                                    expires_utc,
                                    is_secure,
                                    is_httponly,
                                    last_access_utc,
                                    has_expires,
                                    is_persistent,
                                    priority,
                                    samesite,
                                    source_scheme,
                                    source_port,
                                    last_update_utc,
                                    source_type,
                                    has_cross,
                                    host,
                                    name,
                                    path,
                                )
                            )
                        else:
                            # 动态更新（如果表结构不一致）
                            cursor.execute(
                                "UPDATE cookies SET encrypted_value=?, expires_utc=?, is_secure=?, is_httponly=?, last_access_utc=? WHERE host_key=? AND name=? AND path=?",
                                (encrypted_value, expires_utc, is_secure, is_httponly, last_access_utc, host, name, path)
                            )
                        update_count += 1
                    else:
                        # 插入新 cookie
                        if not use_dynamic:
                            cursor.execute(
                                insert_sql,
                                (
                                    creation_utc,
                                    host,
                                    top_frame_site_key,
                                    name,
                                    "",  # value 明文留空
                                    encrypted_value,
                                    path,
                                    expires_utc,
                                    is_secure,
                                    is_httponly,
                                    last_access_utc,
                                    has_expires,
                                    is_persistent,
                                    priority,
                                    samesite,
                                    source_scheme,
                                    source_port,
                                    last_update_utc,
                                    source_type,
                                    has_cross,
                                )
                            )
                        else:
                            # 动态插入（如果表结构不一致，使用最小字段集）
                            cursor.execute(
                                "INSERT INTO cookies (host_key, name, encrypted_value, path, expires_utc, is_secure, is_httponly, creation_utc, last_access_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (host, name, encrypted_value, path, expires_utc, is_secure, is_httponly, creation_utc, last_access_utc)
                            )
                        insert_count += 1
                    success_count += 1
                except Exception as e:
                    error_count += 1
                    if len(error_details) < 5:
                        error_details.append(f"Cookie {idx+1}: {str(e)[:50]}")
                    continue
            
            conn.commit()
            conn.close()
            
            # 显示导入结果
            total = len(cookies)
            success_rate = (success_count / total * 100) if total > 0 else 0
            print(f"   ✅ Cookies: {success_count:,}/{total:,} ({success_rate:.1f}%)")
            if insert_count > 0 or update_count > 0:
                print(f"      📝 新增: {insert_count:,} 个 | 🔄 更新: {update_count:,} 个")
            if error_count > 0:
                print(f"   ⚠️  失败: {error_count:,} 个")
            return success_count > 0
        except sqlite3.OperationalError as e:
            error_msg = str(e).lower()
            if "database is locked" in error_msg or "unable to open database file" in error_msg:
                print(f"❌ {browser_name} Cookies 数据库被锁定或无法打开")
                print(f"   💡 可能原因：")
                print(f"      1. 浏览器正在运行（请关闭所有浏览器窗口）")
                print(f"      2. 文件被其他程序占用")
                print(f"      3. 文件权限不足")
                print(f"   💡 解决方案：")
                print(f"      1. 关闭所有浏览器窗口和进程")
                print(f"      2. 以管理员身份运行脚本")
                print(f"      3. 检查文件权限")
            else:
                print(f"❌ 导入 {browser_name} Cookies 失败: {e}")
                print(f"   💡 请检查文件路径和权限")
            return False
        except PermissionError as e:
            print(f"❌ {browser_name} Cookies 文件权限被拒绝")
            print(f"   💡 请以管理员身份运行脚本，或关闭浏览器后重试")
            return False
        except Exception as e:
            print(f"❌ 导入 {browser_name} Cookies 失败: {e}")
            print(f"   💡 错误类型: {type(e).__name__}")
            return False
    
    def import_passwords(self, browser_name, browser_path, passwords, master_key):
        """导入密码"""
        login_data_path = os.path.join(browser_path, "Login Data")
        if not os.path.exists(login_data_path):
            print(f"   ❌ Login Data 文件不存在")
            return False
        
        # 备份现有密码数据
        backup_path = login_data_path + f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        try:
            shutil.copy2(login_data_path, backup_path)
        except Exception as e:
            print(f"   ⚠️  备份失败: {e}")
            return False
        
        # 导入密码
        success_count = 0
        error_count = 0
        error_details = []
        try:
            # 使用 WAL 模式和超时设置
            conn = sqlite3.connect(login_data_path, timeout=30.0)
            conn.execute("PRAGMA journal_mode=WAL")
            cursor = conn.cursor()
            
            for idx, pwd in enumerate(passwords):
                try:
                    # 验证密码数据结构
                    if not isinstance(pwd, dict):
                        error_count += 1
                        if len(error_details) < 5:
                            error_details.append(f"密码 {idx+1}: 数据结构无效")
                        continue
                    
                    # 检查必需字段
                    required_fields = ["url", "username", "password"]
                    if not all(field in pwd for field in required_fields):
                        error_count += 1
                        if len(error_details) < 5:
                            error_details.append(f"密码 {idx+1}: 缺少必需字段")
                        continue
                    
                    # 加密密码
                    encrypted_password = self.encrypt_payload(pwd["password"], master_key)
                    if not encrypted_password:
                        error_count += 1
                        if len(error_details) < 5:
                            error_details.append(f"密码 {idx+1}: 加密失败")
                        continue
                    
                    # 生成 signon_realm（从 URL 提取）
                    from urllib.parse import urlparse
                    url = pwd["url"]
                    parsed_url = urlparse(url)
                    signon_realm = pwd.get("signon_realm")
                    if not signon_realm:
                        # 从 URL 生成 signon_realm
                        if parsed_url.scheme and parsed_url.netloc:
                            signon_realm = f"{parsed_url.scheme}://{parsed_url.netloc}/"
                        else:
                            signon_realm = url
                    
                    # 获取时间戳
                    import time
                    date_created = pwd.get("date_created", self.chrome_timestamp())
                    date_last_used = pwd.get("date_last_used", self.chrome_timestamp())
                    date_password_modified = pwd.get("date_password_modified", self.chrome_timestamp())
                    
                    # 检查是否已存在
                    cursor.execute(
                        "SELECT COUNT(*) FROM logins WHERE origin_url=? AND username_value=?",
                        (url, pwd["username"])
                    )
                    exists = cursor.fetchone()[0] > 0
                    
                    if exists:
                        # 更新现有密码
                        cursor.execute(
                            "UPDATE logins SET password_value=?, signon_realm=?, date_last_used=? WHERE origin_url=? AND username_value=?",
                            (encrypted_password, signon_realm, date_last_used, url, pwd["username"])
                        )
                    else:
                        # 插入新密码（包含必需字段）
                        # 先尝试获取表结构，确定哪些字段存在
                        try:
                            cursor.execute("PRAGMA table_info(logins)")
                            columns = [row[1] for row in cursor.fetchall()]
                            
                            # 构建字段列表和值列表
                            fields = ["origin_url", "username_value", "password_value", "signon_realm"]
                            values = [url, pwd["username"], encrypted_password, signon_realm]
                            
                            # 添加可选字段（如果存在）
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
                            
                            # 执行插入
                            placeholders = ",".join(["?"] * len(fields))
                            field_names = ",".join(fields)
                            cursor.execute(
                                f"INSERT INTO logins ({field_names}) VALUES ({placeholders})",
                                values
                            )
                        except Exception as e:
                            # 如果获取表结构失败，使用最小字段集
                            cursor.execute(
                                "INSERT INTO logins (origin_url, username_value, password_value, signon_realm, date_created, date_last_used) VALUES (?, ?, ?, ?, ?, ?)",
                                (url, pwd["username"], encrypted_password, signon_realm, date_created, date_last_used)
                            )
                    success_count += 1
                except Exception as e:
                    error_count += 1
                    if len(error_details) < 5:
                        error_details.append(f"密码 {idx+1}: {str(e)[:50]}")
                    continue
            
            conn.commit()
            conn.close()
            
            # 显示导入结果
            total = len(passwords)
            success_rate = (success_count / total * 100) if total > 0 else 0
            print(f"   ✅ 密码: {success_count:,}/{total:,} ({success_rate:.1f}%)")
            if error_count > 0:
                print(f"   ⚠️  失败: {error_count:,} 个")
            return success_count > 0
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower():
                print(f"❌ {browser_name} 密码数据库被锁定")
                print(f"   请关闭所有 Chrome 浏览器窗口后重试")
            else:
                print(f"❌ 导入 {browser_name} 密码失败: {e}")
            return False
        except Exception as e:
            print(f"❌ 导入 {browser_name} 密码失败: {e}")
            return False
    
    def import_web_data(self, browser_name, browser_path, autofill, credit_cards, master_key):
        """导入自动填充和信用卡，并用目标浏览器密钥加密卡号。"""
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
                    "date_last_used": item.get("date_last_used", self.chrome_timestamp()), "count": item.get("count", 0),
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
                continue
        return success

    def _find_credit_card(self, cursor, card, master_key):
        columns = self._table_columns(cursor, "credit_cards")
        required = {"card_number_encrypted", "name_on_card", "expiration_month", "expiration_year"}
        if not required.issubset(columns):
            return "guid=?", ("__new_card__",)
        cursor.execute("SELECT rowid, card_number_encrypted, name_on_card, expiration_month, expiration_year FROM credit_cards")
        for rowid, encrypted, name, month, year in cursor.fetchall():
            if self.credit_card_identity({"number": self.decrypt_payload(encrypted, master_key), "name_on_card": name, "expiration_month": month, "expiration_year": year}) == self.credit_card_identity(card):
                return "rowid=?", (rowid,)
        return "guid=?", ("__new_card__",)

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
            return
        
        with open(import_file, 'r', encoding='utf-8') as f:
            encrypted_data = json.load(f)
        
        # 解密数据
        password = "cookies2026"
        print("🔓 正在解密文件...")
        data = self.decrypt_import_data(encrypted_data, password)
        if not data:
            return
        
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
            return
        print()
        
        # 导入数据
        imported_profiles = []  # 记录导入的 Profile 信息
        
        for browser_name, browser_data in data.get("browsers", {}).items():
            if browser_name not in self.browsers:
                print(f"⏭️  跳过 {browser_name}（不支持）")
                continue
            
            user_data_dir = self.browsers[browser_name]
            
            # Chrome 和 Edge 需要选择 Profile，其他浏览器直接使用路径
            if browser_name in ["Chrome", "Edge"]:
                # 获取可用的 Profile 列表
                available_profiles = self.get_available_profiles(user_data_dir)
                
                if not available_profiles:
                    print(f"\n❌ {browser_name} 未找到可用的配置文件")
                    print(f"   检查路径: {user_data_dir}")
                    continue
                
                # 让用户选择 Profile
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
            else:
                # 其他浏览器直接使用配置的路径
                browser_path = user_data_dir
                if not os.path.exists(browser_path):
                    print(f"⏭️  跳过 {browser_name}（未安装）")
                    continue
                # 记录导入的 Profile 信息
                imported_profiles.append((browser_name, "Default", browser_path))
            
            print(f"\n{'='*60}")
            print(f"📦 导入 {browser_name}")
            print(f"{'='*60}")
            
            # 检查浏览器是否正在运行
            browser_running = self.check_browser_running(browser_name)
            if browser_running:
                print(f"⚠️  检测到 {browser_name} 正在运行")
                print(f"   ⏭️  跳过 {browser_name}，请完全关闭后重试，以避免数据损坏")
                continue
            
            # 获取主密钥
            master_key = self.get_master_key(browser_path)
            if not master_key:
                print(f"   ❌ 无法获取主密钥")
                continue
            
            # 检查数据结构：新格式（使用 profiles）还是旧格式
            if "profiles" in browser_data:
                # 新格式：从 profiles 中提取数据
                profiles = browser_data.get("profiles", {})
                profile_names = list(profiles.keys())
                
                # 初始化 cookies 和 passwords
                cookies = []
                passwords = []
                selected_profile_data = None
                merge_profiles = False
                
                if len(profile_names) == 0:
                    print(f"   ⚠️  导出文件中没有配置文件数据")
                elif len(profile_names) == 1:
                    # 只有一个 profile，自动使用它
                    target_profile = profile_names[0]
                    print(f"   ✅ 自动选择: {target_profile}")
                    profile_data = profiles[target_profile]
                    if isinstance(profile_data, dict):
                        cookies = profile_data.get("cookies", [])
                        passwords = profile_data.get("passwords", [])
                        selected_profile_data = profile_data
                else:
                    # 多个 profiles，让用户选择
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
                            # 合并时收集所有数据
                            all_cookies = []
                            all_passwords = []
                            for profile_name, profile_data in profiles.items():
                                if isinstance(profile_data, dict):
                                    all_cookies.extend(profile_data.get("cookies", []))
                                    all_passwords.extend(profile_data.get("passwords", []))
                            
                            # 去重：对于 cookies，使用 (host, name) 作为唯一键，保留最后一个
                            cookies_dict = {}
                            for cookie in all_cookies:
                                if isinstance(cookie, dict) and "host" in cookie and "name" in cookie:
                                    key = self.cookie_identity(cookie)
                                    cookies_dict[key] = cookie
                            cookies = list(cookies_dict.values())
                            
                            # 去重：对于 passwords，使用 (url, username) 作为唯一键，保留最后一个
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
                # 旧格式：直接使用 cookies 和 passwords
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
            
            # 显示要导入的数据信息
            print(f"\n📋 准备导入数据：")
            print(f"   🍪 Cookies: {len(cookies):,} 个")
            print(f"   🔑 密码: {len(passwords):,} 个")
            print(f"   📝 自动填充: {len(autofill):,} 项")
            print(f"   💳 信用卡: {len(credit_cards):,} 张")
            
            # 导入 Cookies
            if cookies:
                self.import_cookies(browser_name, browser_path, cookies, master_key)
            else:
                print(f"   ⏭️  没有 Cookies 数据需要导入")
            
            # 导入密码
            if passwords:
                self.import_passwords(browser_name, browser_path, passwords, master_key)
            else:
                print(f"   ⏭️  没有密码数据需要导入")

            if autofill or credit_cards:
                self.import_web_data(browser_name, browser_path, autofill, credit_cards, master_key)
            else:
                print(f"   ⏭️  没有自动填充或信用卡数据需要导入")
        
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
        print("✅ 导入完成")
        print("="*60)
        print("\n💡 重要提醒：")
        print("  1. 请重启浏览器以应用更改")
        print("  2. 检查导入的数据是否正确")
        print("  3. 建议删除导入文件以保护隐私")
        print("="*60)


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='浏览器数据导入工具')
    parser.add_argument('-f', '--file', type=str, help='直接指定要导入的文件路径')
    parser.add_argument('-n', '--number', type=int, help='通过文件编号选择文件（运行时不带参数可查看编号）')
    parser.add_argument('-l', '--list', action='store_true', help='仅列出可用的导出文件')
    args = parser.parse_args()
    
    importer = BrowserDataImporter()
    
    # 列出可用的导出文件
    exports_dir = importer.exports_dir
    if not exports_dir.exists():
        print("❌ 未找到导出目录")
        return
    
    export_files = list(exports_dir.glob("*.encrypted"))
    if not export_files:
        print("❌ 未找到导出文件")
        return
    
    # 按文件名排序，确保顺序一致
    export_files.sort(key=lambda x: x.name)
    
    print("\n📁 可用的导出文件：")
    for i, file in enumerate(export_files, 1):
        file_size = file.stat().st_size / 1024 / 1024  # MB
        print(f"  {i}. {file.name} ({file_size:.2f} MB)")
    
    # 如果只是列出文件，则退出
    if args.list:
        return
    
    # 确定要导入的文件
    import_file = None
    
    if args.file:
        # 直接指定文件路径
        import_file = Path(args.file)
        if not import_file.exists():
            print(f"❌ 文件不存在: {import_file}")
            return
    elif args.number:
        # 通过编号选择
        if 1 <= args.number <= len(export_files):
            import_file = export_files[args.number - 1]
        else:
            print(f"❌ 无效的文件编号，请选择 1-{len(export_files)} 之间的数字")
            return
    else:
        # 交互式选择
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
    
    # 执行导入
    if import_file:
        importer.import_all(import_file)


if __name__ == "__main__":
    main()

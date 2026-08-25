# -*- coding: utf-8 -*-
"""
macOS 浏览器数据导出工具
功能：解密并导出 Chrome/Edge/Brave 的 Cookies 和密码为加密备份
警告：此工具处理敏感数据，请确保：
  1. 仅在自己的设备上使用
  2. 导出文件需加密存储
  3. 不要分享导出文件
"""

import os
import json
import base64
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
import getpass

from browser_backup_common import (
    atomic_write_private_json,
    get_exports_dir,
    sqlite_readonly_uri,
)

try:
    from Crypto.Cipher import AES
    from Crypto.Protocol.KDF import PBKDF2
    from Crypto.Random import get_random_bytes
except ImportError:
    print("❌ 需要安装 pycryptodome: pip3 install pycryptodome")
    exit(1)


BACKUP_LOCK_TIMEOUT_SECONDS = 30
BACKUP_BUSY_TIMEOUT_MS = 2000
SNAPSHOT_ATTEMPTS = 3
SNAPSHOT_STABILITY_DELAY = 0.25


class _DatabaseLocked(RuntimeError):
    """内部信号：源数据库被排它锁占用，需要回退到文件级快照。"""


class BrowserDataExporter:
    """macOS 浏览器数据导出器"""
    
    def __init__(self, output_dir=None):
        home = os.path.expanduser('~')
        self.browsers = {
            "Chrome": os.path.join(home, "Library/Application Support/Google/Chrome"),
            "Edge": os.path.join(home, "Library/Application Support/Microsoft Edge"),
            "Brave": os.path.join(home, "Library/Application Support/BraveSoftware/Brave-Browser"),
        }
        self.output_dir = Path(output_dir) if output_dir is not None else get_exports_dir()
    
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
    def build_browser_payload(profiles, master_key):
        """构建备份结构，保留源浏览器主密钥以兼容现有格式。"""
        return {
            "profiles": profiles,
            "master_key": (
                base64.b64encode(master_key).decode("utf-8") if master_key else None
            ),
            "master_key_available": master_key is not None,
            "total_cookies": sum(len(profile.get("cookies", [])) for profile in profiles.values()),
            "total_passwords": sum(len(profile.get("passwords", [])) for profile in profiles.values()),
            "total_autofill": sum(len(profile.get("autofill", [])) for profile in profiles.values()),
            "total_credit_cards": sum(len(profile.get("credit_cards", [])) for profile in profiles.values()),
            "profiles_count": len(profiles),
        }
    
    def get_master_key(self, browser_name):
        """获取浏览器主密钥（从 macOS Keychain）；失败返回 None 以触发降级导出。"""
        try:
            # Chrome/Brave 的密钥存储在 Keychain 中
            keychain_entries = {
                "Chrome": [("Chrome Safe Storage", "Chrome"), ("Chrome Safe Storage", "")],
                "Edge": [("Microsoft Edge Safe Storage", "Microsoft Edge"), ("Microsoft Edge Safe Storage", "Edge")],
                "Brave": [("Brave Safe Storage", "Brave"), ("Brave Safe Storage", "")],
            }
            print(f"   ⏳ 正在请求 {browser_name} 的 Keychain 授权；如果系统弹出授权窗口，请选择“允许”")
            for service_name, account_name in keychain_entries.get(browser_name, []):
                cmd = ['security', 'find-generic-password', '-w', '-s', service_name]
                if account_name:
                    cmd.extend(['-a', account_name])
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                except subprocess.TimeoutExpired:
                    print(f"   ⚠️ {browser_name}: Keychain 授权请求超时（15 秒内未响应授权窗口）")
                    continue
                if result.returncode == 0 and result.stdout.strip():
                    print(f"   ✅ {browser_name} Keychain 密钥获取成功")
                    return PBKDF2(result.stdout.strip().encode('utf-8'), b'saltysalt', dkLen=16, count=1003)
                message = (
                    getattr(result, "stderr", "") or getattr(result, "stdout", "") or ""
                ).strip().lower()
                if "interaction is not allowed" in message:
                    print(
                        f"   ⚠️ {browser_name}: 当前会话无法弹出 Keychain 授权窗口（非图形会话）；"
                        "请先在“终端”中运行一次并允许授权"
                    )
                elif "cancel" in message or "denied" in message:
                    print(
                        f"   ⚠️ {browser_name}: Keychain 授权被拒绝；该浏览器将降级导出"
                        "（自动填充明文，Cookies/密码/信用卡保留加密原样）"
                    )
                elif "could not be found" in message:
                    print(
                        f"   ⚠️ {browser_name}: 未找到 {service_name} 密钥项；"
                        "请先正常打开一次该浏览器再导出"
                    )
            return None
        except Exception as e:
            print(f"   ❌ 获取 {browser_name} 主密钥失败: {e}")
            return None
    
    def decrypt_payload(self, cipher_text, master_key, hash_prefix=False):
        """严格解密 macOS 浏览器字段，失败时返回 None。

        Chrome 127+（Cookies schema v24+）会在加密负载内嵌入 32 字节随机
        前缀；传 ``hash_prefix=True`` 时在解密后剥离该前缀。
        """
        try:
            if not cipher_text or not isinstance(cipher_text, (bytes, bytearray)):
                return None

            payload = bytes(cipher_text)
            prefix = payload[:3]
            if prefix == b"v10":
                if not master_key:
                    return None
                payload_bytes = payload[3:]
                if not payload_bytes or len(payload_bytes) % 16:
                    return None
                cipher = AES.new(master_key, AES.MODE_CBC, iv=b" " * 16)
                decrypted = cipher.decrypt(payload_bytes)
                padding_length = decrypted[-1]
                if not 1 <= padding_length <= 16:
                    return None
                if decrypted[-padding_length:] != bytes([padding_length]) * padding_length:
                    return None
                plaintext = decrypted[:-padding_length]
                if hash_prefix:
                    plaintext = plaintext[32:]
                return plaintext.decode("utf-8")

            if prefix == b"v11":
                if not master_key:
                    return None
                payload_bytes = payload[3:]
                if len(payload_bytes) < 12 + 16:
                    return None
                nonce, ciphertext, tag = payload_bytes[:12], payload_bytes[12:-16], payload_bytes[-16:]
                cipher = AES.new(master_key, AES.MODE_GCM, nonce=nonce)
                plaintext = cipher.decrypt_and_verify(ciphertext, tag)
                if hash_prefix:
                    plaintext = plaintext[32:]
                return plaintext.decode("utf-8")

            if prefix == b"v20":
                print("⚠️ 检测到 v20/App-Bound Encryption，当前 macOS 导出器无法直接解密该字段")
                return None

            # 兼容旧版未加密或明文存储的字段，但必须严格按 UTF-8 解码。
            return payload.decode("utf-8")
        except Exception:
            return None

    def safe_copy_locked_file(self, source_path, dest_path):
        """使用 SQLite Online Backup 创建包含 WAL 数据的一致快照。"""
        return self.sqlite_online_backup(source_path, dest_path)
    
    @staticmethod
    def sqlite_online_backup(source_db, dest_db):
        """使用 SQLite Online Backup 复制数据库；被排它锁占用时回退到文件级快照。"""
        destination_db = Path(dest_db)
        try:
            try:
                BrowserDataExporter._backup_via_sqlite_api(source_db, destination_db)
            except _DatabaseLocked:
                BrowserDataExporter._snapshot_locked_database(source_db, destination_db)
            destination_db.chmod(0o600)
            return True
        except Exception:
            try:
                destination_db.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    @staticmethod
    def _backup_via_sqlite_api(source_db, dest_db):
        """通过 SQLite 在线备份 API 创建快照，带受限的忙等待和超时。"""
        source_uri = sqlite_readonly_uri(Path(source_db))
        try:
            with closing(sqlite3.connect(source_uri, uri=True)) as probe:
                probe.execute(f"PRAGMA busy_timeout={BACKUP_BUSY_TIMEOUT_MS}")
                probe.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlite3.Error:
            raise _DatabaseLocked(str(source_db))

        backup_error = None
        completed = threading.Event()

        def run_backup():
            nonlocal backup_error
            try:
                with closing(sqlite3.connect(source_uri, uri=True)) as source, \
                        closing(sqlite3.connect(dest_db)) as destination:
                    source.backup(destination)
            except BaseException as error:
                backup_error = error
            finally:
                completed.set()

        worker = threading.Thread(
            target=run_backup, name="bserexp-snapshot", daemon=True
        )
        worker.start()
        if not completed.wait(timeout=BACKUP_LOCK_TIMEOUT_SECONDS):
            raise _DatabaseLocked(str(source_db))
        if backup_error is not None:
            if (
                isinstance(backup_error, sqlite3.OperationalError)
                and "locked" in str(backup_error).lower()
            ):
                raise _DatabaseLocked(str(source_db))
            raise backup_error

    @staticmethod
    def _snapshot_locked_database(source_db, dest_db):
        """复制被排它锁占用的数据库文件，并校验快照一致性。"""
        source_db = Path(source_db)
        dest_db = Path(dest_db)
        snapshot_main = dest_db.parent / f"{dest_db.name}.snap"
        source_files = {source_db: snapshot_main}
        final_files = {snapshot_main: dest_db}
        for suffix in ("-journal", "-WAL", "-SHM"):
            candidate = Path(str(source_db) + suffix)
            if candidate.exists():
                snapshot = Path(str(snapshot_main) + suffix)
                source_files[candidate] = snapshot
                final_files[snapshot] = Path(str(dest_db) + suffix)

        last_error = None
        for _attempt in range(SNAPSHOT_ATTEMPTS):
            for snapshot in source_files.values():
                try:
                    snapshot.unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                sizes_before = {
                    source: source.stat().st_size for source in source_files
                }
                for source, snapshot in source_files.items():
                    shutil.copyfile(source, snapshot)
                time.sleep(SNAPSHOT_STABILITY_DELAY)
                sizes_after = {
                    source: source.stat().st_size for source in source_files
                }
                if sizes_before != sizes_after:
                    last_error = RuntimeError("数据库文件在快照期间发生变化")
                    continue
                with closing(sqlite3.connect(snapshot_main)) as connection:
                    check = connection.execute("PRAGMA quick_check").fetchone()[0]
                if check != "ok":
                    last_error = RuntimeError(f"快照校验未通过: {check}")
                    continue
                for snapshot, final_path in final_files.items():
                    os.replace(snapshot, final_path)
                return
            except (OSError, sqlite3.Error, RuntimeError) as error:
                last_error = error
        raise RuntimeError(
            f"无法创建数据库一致快照（数据库正被浏览器持续占用，"
            f"文件快照校验未通过）: {source_db}；请关闭浏览器后重试"
        ) from last_error

    @contextmanager
    def temporary_database_copy(self, source_path, label):
        """在私有临时目录中创建数据库快照并确保退出时清理。"""
        with tempfile.TemporaryDirectory(prefix="browser_backup_") as directory:
            destination = Path(directory) / f"{label}.db"
            if not self.safe_copy_locked_file(source_path, destination):
                raise RuntimeError(f"无法创建数据库一致快照: {source_path}")
            yield destination
    
    def export_cookies(self, browser_name, profile_name, browser_path, master_key):
        """导出 Cookies（支持浏览器运行时）"""
        cookies_path = os.path.join(browser_path, "Network", "Cookies")
        if not os.path.exists(cookies_path):
            cookies_path = os.path.join(browser_path, "Cookies")
        if not os.path.exists(cookies_path):
            return []

        optional_fields = (
            "creation_utc", "top_frame_site_key", "last_access_utc",
            "has_expires", "is_persistent", "priority", "samesite",
            "source_scheme", "source_port", "last_update_utc", "source_type",
            "has_cross_site_ancestor",
        )
        with self.temporary_database_copy(
            cookies_path, f"{browser_name}_{profile_name}_cookies"
        ) as database:
            with closing(sqlite3.connect(database)) as conn:
                conn.row_factory = sqlite3.Row
                columns = {row[1] for row in conn.execute("PRAGMA table_info(cookies)")}
                hash_prefix = self._cookie_hash_prefix(conn)
                required = {"host_key", "name", "path", "expires_utc", "is_secure", "is_httponly"}
                if not required.issubset(columns) or not {"encrypted_value", "value"}.intersection(columns):
                    raise RuntimeError("Cookies 数据库结构不受支持")
                fields = [field for field in (
                    "host_key", "name", "value", "encrypted_value", "path",
                    "expires_utc", "is_secure", "is_httponly", *optional_fields,
                ) if field in columns]
                rows = conn.execute(f"SELECT {','.join(fields)} FROM cookies").fetchall()

        cookies = []
        for row in rows:
            encrypted_value = row["encrypted_value"] if "encrypted_value" in row.keys() else None
            if encrypted_value:
                if master_key is None:
                    value = {
                        "encrypted": True,
                        "data": base64.b64encode(bytes(encrypted_value)).decode("ascii"),
                    }
                else:
                    value = self.decrypt_payload(
                        encrypted_value, master_key, hash_prefix=hash_prefix
                    )
                    if value is None:
                        raise RuntimeError(
                            f"Cookie 解密失败: {row['host_key']} / {row['name']}"
                        )
            else:
                value = row["value"] if "value" in row.keys() else ""
            cookie = {
                "host": row["host_key"],
                "name": row["name"],
                "value": value or "",
                "path": row["path"],
                "expires": row["expires_utc"],
                "secure": bool(row["is_secure"]),
                "httponly": bool(row["is_httponly"]),
            }
            for field in optional_fields:
                if field in row.keys():
                    cookie[field] = row[field]
            cookies.append(cookie)
        return cookies

    @staticmethod
    def _cookie_hash_prefix(connection):
        """返回 Cookies 是否使用 Chrome v24+ 的 32 字节随机前缀。"""
        try:
            row = connection.execute(
                "SELECT value FROM meta WHERE key = 'version'"
            ).fetchone()
        except sqlite3.Error:
            return False
        if row is None:
            return False
        try:
            return int(row[0]) >= 24
        except (TypeError, ValueError):
            return False
    
    def export_passwords(self, browser_name, profile_name, browser_path, master_key):
        """导出密码（支持浏览器运行时）"""
        login_data_path = os.path.join(browser_path, "Login Data")
        if not os.path.exists(login_data_path):
            return []

        optional_fields = (
            "action_url", "signon_realm", "date_created", "date_last_used",
            "date_password_modified", "times_used", "blacklisted_by_user",
            "scheme", "display_name", "icon_url", "federation_url",
            "skip_zero_click", "generation_upload_status", "username_element",
            "password_element",
        )
        with self.temporary_database_copy(
            login_data_path, f"{browser_name}_{profile_name}_login"
        ) as database:
            with closing(sqlite3.connect(database)) as conn:
                conn.row_factory = sqlite3.Row
                columns = {row[1] for row in conn.execute("PRAGMA table_info(logins)")}
                required = {"origin_url", "username_value", "password_value"}
                if not required.issubset(columns):
                    raise RuntimeError("Login Data 数据库结构不受支持")
                fields = [field for field in (
                    "origin_url", "username_value", "password_value", *optional_fields,
                ) if field in columns]
                rows = conn.execute(f"SELECT {','.join(fields)} FROM logins").fetchall()

        passwords = []
        for row in rows:
            encrypted_password = row["password_value"]
            if encrypted_password:
                if master_key is None:
                    password = {
                        "encrypted": True,
                        "data": base64.b64encode(bytes(encrypted_password)).decode("ascii"),
                    }
                else:
                    password = self.decrypt_payload(encrypted_password, master_key)
                    if password is None:
                        raise RuntimeError(
                            f"密码解密失败: {row['origin_url']} / {row['username_value']}"
                        )
            else:
                password = ""
            item = {
                "url": row["origin_url"],
                "username": row["username_value"],
                "password": password,
            }
            for field in optional_fields:
                if field in row.keys():
                    item[field] = row[field]
            passwords.append(item)
        return passwords

    def export_web_data(self, browser_name, profile_name, browser_path, master_key):
        """导出自动填充和本地信用卡信息。"""
        web_data_path = os.path.join(browser_path, "Web Data")
        if not os.path.exists(web_data_path):
            return [], []

        autofill, credit_cards = [], []
        with self.temporary_database_copy(
            web_data_path, f"{browser_name}_{profile_name}_web_data"
        ) as database:
            with closing(sqlite3.connect(database)) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='autofill'")
                if cursor.fetchone():
                    cursor.execute("PRAGMA table_info(autofill)")
                    columns = {row[1] for row in cursor.fetchall()}
                    if not {"name", "value"}.issubset(columns):
                        raise RuntimeError("autofill 表结构不受支持")
                    fields = [field for field in ("name", "value", "date_created", "date_last_used", "count") if field in columns]
                    cursor.execute(f"SELECT {','.join(fields)} FROM autofill")
                    autofill = [dict(zip(fields, row)) for row in cursor.fetchall()]

                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='credit_cards'")
                if cursor.fetchone():
                    cursor.execute("PRAGMA table_info(credit_cards)")
                    columns = {row[1] for row in cursor.fetchall()}
                    fields = [field for field in (
                        "guid", "name_on_card", "expiration_month", "expiration_year",
                        "card_number_encrypted", "date_modified", "use_count", "use_date",
                        "billing_address_id", "nickname", "card_issuer", "instrument_id",
                        "virtual_card_enrollment_state", "card_art_url", "product_description",
                    ) if field in columns]
                    if "card_number_encrypted" not in columns:
                        raise RuntimeError("credit_cards 表结构不受支持")
                    cursor.execute(f"SELECT {','.join(fields)} FROM credit_cards")
                    for row in cursor.fetchall():
                        card = dict(zip(fields, row))
                        encrypted_number = card.pop("card_number_encrypted", None)
                        if master_key is None:
                            number = (
                                {
                                    "encrypted": True,
                                    "data": base64.b64encode(
                                        bytes(encrypted_number)
                                    ).decode("ascii"),
                                }
                                if encrypted_number
                                else ""
                            )
                        else:
                            number = self.decrypt_payload(encrypted_number, master_key)
                            if number is None:
                                raise RuntimeError(
                                    f"信用卡卡号解密失败: {card.get('guid', '未知 GUID')}"
                                )
                        card["number"] = number
                        credit_cards.append(card)
        return autofill, credit_cards
    
    def encrypt_export_data(self, data, password):
        """加密导出数据"""
        try:
            salt = get_random_bytes(32)
            key = PBKDF2(password, salt, dkLen=32, count=100000)
            cipher = AES.new(key, AES.MODE_GCM)
            ciphertext, tag = cipher.encrypt_and_digest(json.dumps(data, ensure_ascii=False).encode('utf-8'))
            
            encrypted_data = {
                "salt": base64.b64encode(salt).decode('utf-8'),
                "nonce": base64.b64encode(cipher.nonce).decode('utf-8'),
                "tag": base64.b64encode(tag).decode('utf-8'),
                "ciphertext": base64.b64encode(ciphertext).decode('utf-8')
            }
            return encrypted_data
        except Exception as e:
            print(f"❌ 加密数据失败: {e}")
            return None
    
    def export_all(self):
        """导出所有浏览器数据"""
        print("\n" + "="*60)
        print("🔐 macOS 浏览器数据导出工具")
        print("="*60)
        print("⚠️  警告：此操作将导出敏感数据，请确保安全使用")
        print("ℹ️  提示：支持在浏览器运行时导出（无需关闭）")
        print("-"*60)
        
        all_data = {
            "format_version": 2,
            "export_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "username": getpass.getuser(),
            "platform": "macOS",
            "browsers": {}
        }
        export_errors = []
        degraded_browsers = []
        
        for browser_name, user_data_dir in self.browsers.items():
            if not os.path.exists(user_data_dir):
                print(f"\n⏭️  跳过 {browser_name}（未安装）")
                continue
            
            print(f"\n📦 处理 {browser_name}...")
            
            # 获取所有可用的 Profile
            try:
                available_profiles = self.get_available_profiles(user_data_dir)
            except RuntimeError as error:
                export_errors.append(str(error))
                print(f"   ❌ {error}")
                continue
            if not available_profiles:
                print(f"   ⚠️  未找到可用的配置文件")
                continue
            
            # 让用户选择要导出的 Profile
            print(f"   📁 找到 {len(available_profiles)} 个配置文件：")
            for idx, (profile_name, _) in enumerate(available_profiles, 1):
                print(f"      {idx}. {profile_name}")
            
            print(f"   0. 导出所有配置文件")
            try:
                choice = input(f"\n   请选择要导出的配置文件 (0-{len(available_profiles)}): ").strip()
                choice_num = int(choice)
                
                if choice_num == 0:
                    # 导出所有 Profile
                    selected_profiles = available_profiles
                elif 1 <= choice_num <= len(available_profiles):
                    # 导出选中的 Profile
                    selected_profiles = [available_profiles[choice_num - 1]]
                else:
                    print(f"   ❌ 无效的选择，将导出所有配置文件")
                    selected_profiles = available_profiles
            except (ValueError, KeyboardInterrupt):
                print(f"   ❌ 输入无效，将导出所有配置文件")
                selected_profiles = available_profiles
            
            # 获取主密钥（所有 Profile 共享同一个 Master Key）
            master_key = self.get_master_key(browser_name)
            if not master_key:
                print(
                    f"   ⚠️ {browser_name} Keychain 授权失败，将降级导出"
                    "（Cookies/密码/信用卡保留加密原样）"
                )
            
            # 导出每个选中的 Profile 数据
            browser_profiles = {}
            total_cookies = 0
            total_passwords = 0
            total_autofill = 0
            total_credit_cards = 0
            
            for profile_name, profile_path in selected_profiles:
                print(f"\n   📋 导出 {profile_name}...")
                
                try:
                    cookies = self.export_cookies(browser_name, profile_name, profile_path, master_key)
                    passwords = self.export_passwords(browser_name, profile_name, profile_path, master_key)
                    autofill, credit_cards = self.export_web_data(browser_name, profile_name, profile_path, master_key)
                except Exception as error:
                    message = f"{browser_name}/{profile_name}: {error}"
                    export_errors.append(message)
                    print(f"      ❌ {message}")
                    continue
                
                if cookies or passwords or autofill or credit_cards:
                    browser_profiles[profile_name] = {
                        "cookies": cookies,
                        "passwords": passwords,
                        "autofill": autofill,
                        "credit_cards": credit_cards,
                    }
                    total_cookies += len(cookies)
                    total_passwords += len(passwords)
                    total_autofill += len(autofill)
                    total_credit_cards += len(credit_cards)
                    print(f"      ✅ {profile_name}: 🍪 {len(cookies):,} 个 | 🔑 {len(passwords):,} 个 | 📝 {len(autofill):,} 项 | 💳 {len(credit_cards):,} 张")
                else:
                    print(f"      ⚠️  {profile_name}: 无数据")
            
            if browser_profiles:
                all_data["browsers"][browser_name] = self.build_browser_payload(browser_profiles, master_key)
                print(f"\n   📊 {browser_name} 总计: 🍪 {total_cookies:,} 个 | 🔑 {total_passwords:,} 个 | 📝 {total_autofill:,} 项 | 💳 {total_credit_cards:,} 张")
                if master_key is None:
                    degraded_browsers.append(browser_name)
                    print(
                        f"   ⚠️ {browser_name} 降级导出："
                        "Cookies/密码/信用卡未解密（保留加密原样）"
                    )
        
        if export_errors:
            print("\n❌ 导出已中止，未生成不完整备份：")
            for error in export_errors:
                print(f"   - {error}")
            return False

        # 检查是否有数据需要导出
        if not all_data["browsers"]:
            print("\n" + "="*60)
            print("⚠️  没有可导出的数据")
            print("="*60)
            return False
        
        # 加密保存
        print("\n" + "-"*60)
        password = "cookies2026"
        print("🔒 使用预设加密密码保护导出文件")
        
        encrypted_data = self.encrypt_export_data(all_data, password)
        if not encrypted_data:
            return False
        
        # 保存到文件
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        username = getpass.getuser()
        output_file = self.output_dir / f"{username}_browser_data_{timestamp}.encrypted"
        
        try:
            atomic_write_private_json(output_file, encrypted_data)
        except Exception as error:
            print(f"❌ 写入备份失败: {error}")
            return False
        
        print("\n" + "="*60)
        print("✅ 导出成功！")
        print(f"📁 文件位置: {output_file}")
        print(f"🔒 文件已加密，需要密码才能解密")
        if degraded_browsers:
            print("\n⚠️  降级导出警告：")
            for browser_name in degraded_browsers:
                print(
                    f"   - {browser_name}: Keychain 授权失败，"
                    "Cookies/密码/信用卡以原始加密数据导出（未解密）"
                )
        print("\n⚠️  重要提醒：")
        print("  1. 请妥善保管此文件和密码")
        print("  2. 不要将此文件上传到公共网络")
        print("  3. 使用完毕后建议删除明文数据")
        print("="*60)
        return True


def main():
    """主函数"""
    exporter = BrowserDataExporter()
    return 0 if exporter.export_all() else 1


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""
Linux 浏览器数据导出工具
功能：解密并导出 Chrome/Chromium/Brave/Edge 的 Cookies 和密码为加密备份
警告：此工具处理敏感数据，请确保：
  1. 仅在自己的设备上使用
  2. 导出文件需加密存储
  3. 不要分享导出文件
"""

import os
import json
import base64
import sqlite3
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
import getpass

try:
    from Crypto.Cipher import AES
    from Crypto.Protocol.KDF import PBKDF2
    from Crypto.Random import get_random_bytes
except ImportError:
    print("❌ 需要安装 pycryptodome: pip install pycryptodome")
    exit(1)


class BrowserDataExporter:
    """Linux 浏览器数据导出器"""
    
    def __init__(self):
        home = os.path.expanduser('~')
        self.browsers = {
            "Chrome": os.path.join(home, ".config/google-chrome"),
            "Chromium": os.path.join(home, ".config/chromium"),
            "Brave": os.path.join(home, ".config/BraveSoftware/Brave-Browser"),
            "Edge": os.path.join(home, ".config/microsoft-edge"),
        }
        self.output_dir = Path(__file__).resolve().parents[3] / "BACKUP" / "浏览器数据" / "exports"
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
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
    def build_browser_payload(profiles, master_key):
        """构建备份结构，保留源浏览器主密钥以兼容现有格式。"""
        return {
            "profiles": profiles,
            "master_key": base64.b64encode(master_key).decode("utf-8"),
            "total_cookies": sum(len(profile["cookies"]) for profile in profiles.values()),
            "total_passwords": sum(len(profile["passwords"]) for profile in profiles.values()),
            "profiles_count": len(profiles),
        }
    
    def get_master_key(self, browser_name):
        """获取浏览器主密钥（从 Linux Keyring）"""
        try:
            # 方法 1：尝试使用 secretstorage 库（推荐）
            try:
                import secretstorage
                connection = secretstorage.dbus_init()
                collection = secretstorage.get_default_collection(connection)
                
                keyring_labels = {
                    "Chrome": "Chrome Safe Storage",
                    "Chromium": "Chromium Safe Storage",
                    "Brave": "Brave Safe Storage",
                    "Edge": "Chromium Safe Storage",
                }
                
                label = keyring_labels.get(browser_name, "Chrome Safe Storage")
                
                for item in collection.get_all_items():
                    if item.get_label() == label:
                        password = item.get_secret().decode('utf-8')
                        connection.close()
                        
                        salt = b'saltysalt'
                        iterations = 1
                        key = PBKDF2(password.encode('utf-8'), salt, dkLen=16, count=iterations)
                        return key
                
                connection.close()
            except Exception:
                pass
            
            # 方法 2：尝试使用 libsecret-tool 命令行工具
            try:
                keyring_apps = {
                    "Chrome": "chrome",
                    "Chromium": "chromium",
                    "Brave": "brave",
                    "Edge": "chromium",
                }
                
                app = keyring_apps.get(browser_name, "chrome")
                cmd = ['secret-tool', 'lookup', 'application', app]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                
                if result.returncode == 0 and result.stdout.strip():
                    password = result.stdout.strip()
                    salt = b'saltysalt'
                    iterations = 1
                    key = PBKDF2(password.encode('utf-8'), salt, dkLen=16, count=iterations)
                    return key
            except Exception:
                pass
            
            # 方法 3：使用默认密码 "peanuts"
            password = "peanuts"
            salt = b'saltysalt'
            iterations = 1
            key = PBKDF2(password.encode('utf-8'), salt, dkLen=16, count=iterations)
            return key
            
        except Exception as e:
            print(f"❌ 获取 {browser_name} 主密钥失败: {e}")
            password = "peanuts"
            salt = b'saltysalt'
            iterations = 1
            key = PBKDF2(password.encode('utf-8'), salt, dkLen=16, count=iterations)
            return key
    
    def decrypt_payload(self, cipher_text, master_key):
        """解密数据"""
        try:
            if not cipher_text or not isinstance(cipher_text, (bytes, bytearray)):
                return None

            prefix = cipher_text[:3]
            # Linux Chrome v10+ 使用 AES-128-CBC
            if prefix == b'v10':
                if not master_key:
                    return None
                iv = b' ' * 16
                cipher_text = cipher_text[3:]
                cipher = AES.new(master_key, AES.MODE_CBC, iv)
                decrypted = cipher.decrypt(cipher_text)
                # 移除 PKCS7 padding
                padding_length = decrypted[-1]
                if isinstance(padding_length, int) and 1 <= padding_length <= 16:
                    decrypted = decrypted[:-padding_length]
                return decrypted.decode('utf-8', errors='ignore')
            elif prefix == b'v11':
                if not master_key:
                    return None
                payload = cipher_text[3:]
                if len(payload) < 12 + 16:
                    return None
                nonce, ciphertext, tag = payload[:12], payload[12:-16], payload[-16:]
                cipher = AES.new(master_key, AES.MODE_GCM, nonce=nonce)
                return cipher.decrypt_and_verify(ciphertext, tag).decode('utf-8', errors='ignore')
            else:
                return cipher_text.decode('utf-8', errors='ignore')
        except (ValueError, TypeError, IndexError):
            return None
    
    def safe_copy_locked_file(self, source_path, dest_path, max_retries=3):
        """安全复制被锁定的文件（浏览器运行时）"""
        if self.sqlite_online_backup(source_path, dest_path):
            return True
        for attempt in range(max_retries):
            try:
                shutil.copy2(source_path, dest_path)
                return True
            except PermissionError:
                try:
                    with open(source_path, 'rb') as src:
                        with open(dest_path, 'wb') as dst:
                            shutil.copyfileobj(src, dst)
                    return True
                except Exception:
                    if attempt == max_retries - 1:
                        return self.sqlite_online_backup(source_path, dest_path)
                    import time
                    time.sleep(0.5)
            except Exception:
                return False
        return False
    
    def sqlite_online_backup(self, source_db, dest_db):
        """使用 SQLite Online Backup 复制数据库"""
        try:
            source_conn = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True)
            dest_conn = sqlite3.connect(dest_db)
            source_conn.backup(dest_conn)
            source_conn.close()
            dest_conn.close()
            return True
        except Exception:
            return False
    
    def export_cookies(self, browser_name, profile_name, browser_path, master_key):
        """导出 Cookies（支持浏览器运行时）"""
        # 支持 Network/Cookies 路径（新版本 Chrome）
        cookies_path = os.path.join(browser_path, "Network", "Cookies")
        if not os.path.exists(cookies_path):
            cookies_path = os.path.join(browser_path, "Cookies")
        
        if not os.path.exists(cookies_path):
            return []
        
        # 使用安全复制方法
        temp_cookies = os.path.join(self.output_dir, f"temp_{browser_name}_{profile_name}_cookies.db")
        if not self.safe_copy_locked_file(cookies_path, temp_cookies):
            return []
        
        cookies = []
        try:
            conn = sqlite3.connect(temp_cookies)
            cursor = conn.cursor()
            cursor.execute("SELECT host_key, name, encrypted_value, path, expires_utc, is_secure, is_httponly FROM cookies")
            
            for row in cursor.fetchall():
                host, name, encrypted_value, path, expires, is_secure, is_httponly = row
                
                decrypted_value = self.decrypt_payload(encrypted_value, master_key)
                if decrypted_value:
                    cookies.append({
                        "host": host,
                        "name": name,
                        "value": decrypted_value,
                        "path": path,
                        "expires": expires,
                        "secure": bool(is_secure),
                        "httponly": bool(is_httponly)
                    })
            
            conn.close()
        except Exception:
            pass
        finally:
            if os.path.exists(temp_cookies):
                try:
                    os.remove(temp_cookies)
                except Exception:
                    pass
        
        return cookies
    
    def export_passwords(self, browser_name, profile_name, browser_path, master_key):
        """导出密码（支持浏览器运行时）"""
        login_data_path = os.path.join(browser_path, "Login Data")
        if not os.path.exists(login_data_path):
            return []
        
        # 使用安全复制方法
        temp_login = os.path.join(self.output_dir, f"temp_{browser_name}_{profile_name}_login.db")
        if not self.safe_copy_locked_file(login_data_path, temp_login):
            return []
        
        passwords = []
        try:
            conn = sqlite3.connect(temp_login)
            cursor = conn.cursor()
            cursor.execute("SELECT origin_url, username_value, password_value FROM logins")
            
            for row in cursor.fetchall():
                url, username, encrypted_password = row
                
                decrypted_password = self.decrypt_payload(encrypted_password, master_key)
                if decrypted_password:
                    passwords.append({
                        "url": url,
                        "username": username,
                        "password": decrypted_password
                    })
            
            conn.close()
        except Exception:
            pass
        finally:
            if os.path.exists(temp_login):
                try:
                    os.remove(temp_login)
                except Exception:
                    pass
        
        return passwords
    
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
        print("🔐 Linux 浏览器数据导出工具")
        print("="*60)
        print("⚠️  警告：此操作将导出敏感数据，请确保安全使用")
        print("ℹ️  提示：支持在浏览器运行时导出（无需关闭）")
        print("-"*60)
        
        all_data = {
            "export_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "username": getpass.getuser(),
            "platform": "Linux",
            "browsers": {}
        }
        
        for browser_name, user_data_dir in self.browsers.items():
            if not os.path.exists(user_data_dir):
                print(f"\n⏭️  跳过 {browser_name}（未安装）")
                continue
            
            print(f"\n📦 处理 {browser_name}...")
            
            # 获取所有可用的 Profile
            available_profiles = self.get_available_profiles(user_data_dir)
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
                    selected_profiles = available_profiles
                elif 1 <= choice_num <= len(available_profiles):
                    selected_profiles = [available_profiles[choice_num - 1]]
                else:
                    print(f"   ❌ 无效的选择，将导出所有配置文件")
                    selected_profiles = available_profiles
            except (ValueError, KeyboardInterrupt):
                print(f"   ❌ 输入无效，将导出所有配置文件")
                selected_profiles = available_profiles
            
            # 获取主密钥（所有 Profile 共享同一个 Master Key）
            first_profile_path = selected_profiles[0][1]
            master_key = self.get_master_key(browser_name)
            if not master_key:
                print(f"   ❌ 无法获取 {browser_name} 主密钥")
                continue
            
            # 导出每个选中的 Profile 数据
            browser_profiles = {}
            total_cookies = 0
            total_passwords = 0
            
            for profile_name, profile_path in selected_profiles:
                print(f"\n   📋 导出 {profile_name}...")
                
                cookies = self.export_cookies(browser_name, profile_name, profile_path, master_key)
                passwords = self.export_passwords(browser_name, profile_name, profile_path, master_key)
                
                if cookies or passwords:
                    browser_profiles[profile_name] = {
                        "cookies": cookies,
                        "passwords": passwords
                    }
                    total_cookies += len(cookies)
                    total_passwords += len(passwords)
                    print(f"      ✅ {profile_name}: 🍪 {len(cookies):,} 个 | 🔑 {len(passwords):,} 个")
                else:
                    print(f"      ⚠️  {profile_name}: 无数据")
            
            if browser_profiles:
                all_data["browsers"][browser_name] = self.build_browser_payload(browser_profiles, master_key)
                print(f"\n   📊 {browser_name} 总计: 🍪 {total_cookies:,} 个 | 🔑 {total_passwords:,} 个")
        
        # 检查是否有数据需要导出
        if not all_data["browsers"]:
            print("\n" + "="*60)
            print("⚠️  没有可导出的数据")
            print("="*60)
            return
        
        # 加密保存
        print("\n" + "-"*60)
        password = "cookies2026"
        print("🔒 使用预设加密密码保护导出文件")
        
        encrypted_data = self.encrypt_export_data(all_data, password)
        if not encrypted_data:
            return
        
        # 保存到文件
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        username = getpass.getuser()
        output_file = self.output_dir / f"{username}_browser_data_{timestamp}.encrypted"
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(encrypted_data, f, indent=2, ensure_ascii=False)
        
        print("\n" + "="*60)
        print("✅ 导出成功！")
        print(f"📁 文件位置: {output_file}")
        print(f"🔒 文件已加密，需要密码才能解密")
        print("\n⚠️  重要提醒：")
        print("  1. 请妥善保管此文件和密码")
        print("  2. 不要将此文件上传到公共网络")
        print("  3. 使用完毕后建议删除明文数据")
        print("="*60)


def main():
    """主函数"""
    exporter = BrowserDataExporter()
    exporter.export_all()


if __name__ == "__main__":
    main()

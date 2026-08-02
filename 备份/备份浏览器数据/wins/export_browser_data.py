# -*- coding: utf-8 -*-
"""
windows 浏览器数据导出工具
功能：解密并导出 Chrome/Edge 的 Cookies 和密码为加密备份
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
from datetime import datetime
from pathlib import Path
import getpass

try:
    from win32crypt import CryptUnprotectData
except ImportError:
    print("❌ 需要安装 pywin32: pip install pywin32")
    exit(1)

try:
    from Crypto.Cipher import AES
    from Crypto.Protocol.KDF import PBKDF2
    from Crypto.Random import get_random_bytes
except ImportError:
    print("❌ 需要安装 pycryptodome: pip install pycryptodome")
    exit(1)


class BrowserDataExporter:
    """浏览器数据导出器"""
    
    def __init__(self):
        self.browsers = {
            "Chrome": os.path.join(os.environ['LOCALAPPDATA'], "Google", "Chrome", "User Data"),
            "Edge": os.path.join(os.environ['LOCALAPPDATA'], "Microsoft", "Edge", "User Data"),
            "Brave": os.path.join(os.environ['LOCALAPPDATA'], "BraveSoftware", "Brave-Browser", "User Data"),
        }
        self.output_dir = Path(__file__).resolve().parents[3] / "BACKUP" / "浏览器数据" / "exports" / "wins"
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
        """构建包含源浏览器主密钥的备份数据。"""
        return {
            "profiles": profiles,
            "master_key": base64.b64encode(master_key).decode("utf-8"),
            "total_cookies": sum(len(profile.get("cookies", [])) for profile in profiles.values()),
            "total_passwords": sum(len(profile.get("passwords", [])) for profile in profiles.values()),
            "total_autofill": sum(len(profile.get("autofill", [])) for profile in profiles.values()),
            "total_credit_cards": sum(len(profile.get("credit_cards", [])) for profile in profiles.values()),
            "profiles_count": len(profiles),
        }
    
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
                print("❌ 当前 Chrome 使用 APPB（App-Bound Encryption），此导出器无法直接解密")
                return None
            else:
                print("❌ 不支持的 Windows 浏览器主密钥格式")
                return None

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
                print("⚠️ 检测到 v20/App-Bound Encryption，当前环境无法直接解密该字段")
                return None

            decrypted = CryptUnprotectData(bytes(cipher_text), None, None, None, 0)[1]
            if not decrypted:
                return None
            return decrypted.decode("utf-8")
        except Exception:
            return None

    def safe_copy_locked_file(self, source_path, dest_path, max_retries=3):
        """安全复制被锁定的文件（浏览器运行时）"""
        if self.sqlite_online_backup(source_path, dest_path):
            return True
        for attempt in range(max_retries):
            try:
                # 方法 1：直接复制（Windows 允许读取被锁定文件）
                shutil.copy2(source_path, dest_path)
                return True
            except PermissionError:
                # 方法 2：使用二进制读写（绕过某些锁）
                try:
                    with open(source_path, 'rb') as src:
                        with open(dest_path, 'wb') as dst:
                            shutil.copyfileobj(src, dst)
                    return True
                except Exception as e:
                    if attempt == max_retries - 1:
                        print(f"⚠️  文件被锁定，尝试 SQLite 在线备份...")
                        return self.sqlite_online_backup(source_path, dest_path)
                    import time
                    time.sleep(0.5)
            except Exception as e:
                print(f"❌ 复制失败: {e}")
                return False
        return False
    
    def sqlite_online_backup(self, source_db, dest_db):
        """使用 SQLite Online Backup 复制数据库"""
        try:
            # 打开源数据库（只读模式）
            source_conn = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True)
            # 创建目标数据库
            dest_conn = sqlite3.connect(dest_db)
            
            # 使用 backup API
            source_conn.backup(dest_conn)
            
            source_conn.close()
            dest_conn.close()
            print("✅ 使用在线备份成功")
            return True
        except Exception as e:
            print(f"❌ 在线备份失败: {e}")
            return False
    
    def export_cookies(self, browser_name, profile_name, browser_path, master_key):
        """导出 Cookies（支持浏览器运行时）"""
        cookies_path = os.path.join(browser_path, "Network", "Cookies")
        if not os.path.exists(cookies_path):
            cookies_path = os.path.join(browser_path, "Cookies")
        
        if not os.path.exists(cookies_path):
            return []
        
        # 使用安全复制方法（支持浏览器运行时）
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
                
                # 解密 cookie 值
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
        except Exception as e:
            pass
        finally:
            # 清理临时文件
            if os.path.exists(temp_cookies):
                try:
                    os.remove(temp_cookies)
                except:
                    pass
        
        return cookies
    
    def export_passwords(self, browser_name, profile_name, browser_path, master_key):
        """导出密码（支持浏览器运行时）"""
        login_data_path = os.path.join(browser_path, "Login Data")
        if not os.path.exists(login_data_path):
            return []
        
        # 使用安全复制方法（支持浏览器运行时）
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
                
                # 解密密码
                decrypted_password = self.decrypt_payload(encrypted_password, master_key)
                if decrypted_password:
                    passwords.append({
                        "url": url,
                        "username": username,
                        "password": decrypted_password
                    })
            
            conn.close()
        except Exception as e:
            pass
        finally:
            # 清理临时文件
            if os.path.exists(temp_login):
                try:
                    os.remove(temp_login)
                except:
                    pass
        
        return passwords

    def export_web_data(self, browser_name, profile_name, browser_path, master_key):
        """导出自动填充和本地信用卡信息。"""
        web_data_path = os.path.join(browser_path, "Web Data")
        if not os.path.exists(web_data_path):
            return [], []
        temp_web_data = os.path.join(self.output_dir, f"temp_{browser_name}_{profile_name}_web_data.db")
        if not self.safe_copy_locked_file(web_data_path, temp_web_data):
            return [], []

        autofill, credit_cards = [], []
        try:
            conn = sqlite3.connect(temp_web_data)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='autofill'")
            if cursor.fetchone():
                cursor.execute("PRAGMA table_info(autofill)")
                columns = {row[1] for row in cursor.fetchall()}
                fields = [field for field in ("name", "value", "date_created", "date_last_used", "count") if field in columns]
                if "name" in columns and "value" in columns:
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
                if "card_number_encrypted" in columns:
                    cursor.execute(f"SELECT {','.join(fields)} FROM credit_cards")
                    for row in cursor.fetchall():
                        card = dict(zip(fields, row))
                        number = self.decrypt_payload(card.pop("card_number_encrypted", None), master_key)
                        if number:
                            card["number"] = number
                            credit_cards.append(card)
            conn.close()
        except Exception:
            pass
        finally:
            if os.path.exists(temp_web_data):
                try:
                    os.remove(temp_web_data)
                except Exception:
                    pass
        return autofill, credit_cards
    
    def encrypt_export_data(self, data, password):
        """加密导出数据"""
        try:
            # 生成盐和密钥
            salt = get_random_bytes(32)
            key = PBKDF2(password, salt, dkLen=32, count=100000)
            
            # 加密数据
            cipher = AES.new(key, AES.MODE_GCM)
            ciphertext, tag = cipher.encrypt_and_digest(json.dumps(data, ensure_ascii=False).encode('utf-8'))
            
            # 组合加密数据
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
    
    def export_all(self):
        """导出所有浏览器数据"""
        print("\n" + "="*60)
        print("🔐 浏览器数据导出工具")
        print("="*60)
        print("⚠️  警告：此操作将导出敏感数据，请确保安全使用")
        print("ℹ️  提示：支持在浏览器运行时导出（无需关闭）")
        print("-"*60)
        
        all_data = {
            "export_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "username": getpass.getuser(),
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
            
            # 获取主密钥（所有 Profile 共享同一个 Local State）
            first_profile_path = selected_profiles[0][1]
            master_key = self.get_master_key(first_profile_path)
            if not master_key:
                print(f"   ❌ 无法获取 {browser_name} 主密钥")
                continue
            
            # 导出每个选中的 Profile 数据
            browser_profiles = {}
            total_cookies = 0
            total_passwords = 0
            total_autofill = 0
            total_credit_cards = 0
            
            for profile_name, profile_path in selected_profiles:
                print(f"\n   📋 导出 {profile_name}...")
                
                cookies = self.export_cookies(browser_name, profile_name, profile_path, master_key)
                passwords = self.export_passwords(browser_name, profile_name, profile_path, master_key)
                autofill, credit_cards = self.export_web_data(browser_name, profile_name, profile_path, master_key)
                
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

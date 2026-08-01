# -*- coding: utf-8 -*-
"""
将 exports 目录下的加密文件转换为 txt 文件
功能：交互式选择加密文件，解密并格式化为可读的 txt 文件
"""

import os
import json
import base64
import unicodedata
from pathlib import Path
from datetime import datetime

try:
    from Crypto.Cipher import AES
    from Crypto.Protocol.KDF import PBKDF2
except ImportError:
    print("❌ 需要安装 pycryptodome: pip install pycryptodome")
    exit(1)


def decrypt_encrypted_data(encrypted_data, password):
    """解密加密数据"""
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


def format_cookies_txt(cookies):
    """格式化 Cookies 为文本"""
    if not cookies:
        return "无 Cookies 数据\n"
    
    lines = []
    lines.append("=" * 80)
    lines.append("COOKIES 列表")
    lines.append("=" * 80)
    lines.append(f"总计: {len(cookies)} 个\n")
    
    for idx, cookie in enumerate(cookies, 1):
        lines.append(f"\n[{idx}] {cookie.get('host', 'N/A')}")
        lines.append(f"    名称: {cookie.get('name', 'N/A')}")
        lines.append(f"    值: {cookie.get('value', 'N/A')}")
        lines.append(f"    路径: {cookie.get('path', '/')}")
        if cookie.get('expires'):
            try:
                expires_dt = datetime.fromtimestamp(cookie['expires'] / 1000000 - 11644473600)
                lines.append(f"    过期时间: {expires_dt.strftime('%Y-%m-%d %H:%M:%S')}")
            except:
                lines.append(f"    过期时间: {cookie.get('expires')}")
        lines.append(f"    安全: {'是' if cookie.get('secure') else '否'}")
        lines.append(f"    HttpOnly: {'是' if cookie.get('httponly') else '否'}")
    
    return "\n".join(lines)


def format_passwords_txt(passwords):
    """格式化密码为文本"""
    if not passwords:
        return "无密码数据\n"
    
    lines = []
    lines.append("=" * 80)
    lines.append("密码列表")
    lines.append("=" * 80)
    lines.append(f"总计: {len(passwords)} 个\n")
    
    for idx, pwd in enumerate(passwords, 1):
        lines.append(f"\n[{idx}] {pwd.get('url', 'N/A')}")
        lines.append(f"    用户名: {pwd.get('username', 'N/A')}")
        lines.append(f"    密码: {pwd.get('password', 'N/A')}")
    
    return "\n".join(lines)


def format_data_to_txt(data):
    """将解密后的数据格式化为文本"""
    lines = []
    
    # 头部信息
    lines.append("=" * 80)
    lines.append("浏览器数据导出")
    lines.append("=" * 80)
    lines.append(f"导出时间: {data.get('export_time', 'N/A')}")
    lines.append(f"用户名: {data.get('username', 'N/A')}")
    lines.append("=" * 80)
    lines.append("")
    
    # 遍历所有浏览器
    browsers = data.get('browsers', {})
    if not browsers:
        lines.append("无浏览器数据")
        return "\n".join(lines)
    
    for browser_name, browser_data in browsers.items():
        lines.append("\n" + "=" * 80)
        lines.append(f"浏览器: {browser_name}")
        lines.append("=" * 80)
        lines.append(f"配置文件数量: {browser_data.get('profiles_count', 0)}")
        # 导出时每个浏览器都包含一个 base64 编码的 master_key，这里一并展示出来
        master_key_b64 = browser_data.get("master_key")
        if master_key_b64:
            lines.append(f"master_key (base64): {master_key_b64}")
        lines.append(f"Cookies 总数: {browser_data.get('total_cookies', 0):,} 个")
        lines.append(f"密码总数: {browser_data.get('total_passwords', 0):,} 个")
        lines.append("")
        
        # 遍历所有 Profile
        profiles = browser_data.get('profiles', {})
        for profile_name, profile_data in profiles.items():
            lines.append("\n" + "-" * 80)
            lines.append(f"配置文件: {profile_name}")
            lines.append("-" * 80)
            lines.append("")
            
            # Cookies
            cookies = profile_data.get('cookies', [])
            lines.append(format_cookies_txt(cookies))
            lines.append("")
            
            # Passwords
            passwords = profile_data.get('passwords', [])
            lines.append(format_passwords_txt(passwords))
            lines.append("")
    
    return "\n".join(lines)


def sanitize_text(text):
    """转义会影响终端或编辑器显示的控制字符，保留常用空白字符。"""
    safe_chars = []
    for char in str(text):
        if char in "\t\n\r" or unicodedata.category(char) not in {"Cc", "Cs"}:
            safe_chars.append(char)
        else:
            safe_chars.append(f"\\u{ord(char):04x}")
    return "".join(safe_chars)


def write_txt_file(output_file, content):
    """以跨平台兼容的 UTF-8 格式写出文本。"""
    with open(output_file, "w", encoding="utf-8-sig", newline="\n") as f:
        f.write(sanitize_text(content))


def get_exports_dir():
    """返回跨平台共享的浏览器数据导出目录。"""
    return Path(__file__).resolve().parents[1] / "BACKUP" / "浏览器数据" / "exports"


def main():
    """主函数"""
    print("\n" + "=" * 60)
    print("📄 加密文件转 TXT 工具")
    print("=" * 60)
    
    # 获取 exports 目录
    exports_dir = get_exports_dir()
    
    if not exports_dir.exists():
        print(f"❌ exports 目录不存在: {exports_dir}")
        return
    
    # 查找所有 .encrypted 文件
    encrypted_files = list(exports_dir.glob("*.encrypted"))
    
    if not encrypted_files:
        print(f"❌ 在 {exports_dir} 目录下未找到 .encrypted 文件")
        return
    
    # 显示文件列表
    print(f"\n📁 找到 {len(encrypted_files)} 个加密文件：")
    print("-" * 60)
    for idx, file_path in enumerate(encrypted_files, 1):
        file_size = file_path.stat().st_size / 1024  # KB
        mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
        print(f"  {idx}. {file_path.name}")
        print(f"     大小: {file_size:.2f} KB | 修改时间: {mtime.strftime('%Y-%m-%d %H:%M:%S')}")
    
    print("-" * 60)
    
    # 交互式选择文件
    while True:
        try:
            choice = input(f"\n请选择要转换的文件 (1-{len(encrypted_files)}, 输入 q 退出): ").strip()
            
            if choice.lower() == 'q':
                print("已取消")
                return
            
            choice_num = int(choice)
            if 1 <= choice_num <= len(encrypted_files):
                selected_file = encrypted_files[choice_num - 1]
                break
            else:
                print(f"❌ 无效的选择，请输入 1-{len(encrypted_files)} 之间的数字")
        except ValueError:
            print("❌ 请输入有效的数字")
        except KeyboardInterrupt:
            print("\n已取消")
            return
    
    # 读取加密文件
    print(f"\n📖 正在读取文件: {selected_file.name}")
    try:
        with open(selected_file, 'r', encoding='utf-8') as f:
            encrypted_data = json.load(f)
    except Exception as e:
        print(f"❌ 读取文件失败: {e}")
        return
    
    # 解密数据
    print("🔓 正在解密文件...")
    password = "cookies2026"  # 预设密码
    decrypted_data = decrypt_encrypted_data(encrypted_data, password)
    
    if not decrypted_data:
        print("❌ 解密失败，请检查密码是否正确")
        return
    
    print("✅ 解密成功")
    
    # 格式化为文本
    print("📝 正在格式化数据...")
    txt_content = format_data_to_txt(decrypted_data)
    
    # 保存为 txt 文件
    output_file = selected_file.with_suffix('.txt')
    print(f"💾 正在保存到: {output_file.name}")
    
    try:
        write_txt_file(output_file, txt_content)
        print(f"✅ 转换成功！")
        print(f"📁 输出文件: {output_file}")
        print(f"📊 文件大小: {output_file.stat().st_size / 1024:.2f} KB")
    except Exception as e:
        print(f"❌ 保存文件失败: {e}")
        return
    
    print("\n" + "=" * 60)
    print("✅ 完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
将加密文件转换为 txt 文件
功能：交互式输入加密文件路径，解密并格式化为可读的 txt 文件
"""

import json
import base64
import argparse
import os
import tempfile
import unicodedata
from pathlib import Path
from datetime import datetime

from browser_utils import (
    load_encrypted_file,
    safe_print as print,
    validate_decrypted_data,
)

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
        cookie_value = cookie.get("value")
        if cookie_value is not None:
            lines.append(f"    值: {cookie_value}")
            lines.append("    解密状态: 已解密")
        else:
            lines.append("    值: 未解密")
            encrypted_value = cookie.get("encrypted_value")
            if encrypted_value:
                lines.append(f"    加密值(base64): {encrypted_value}")
            lines.append("    解密状态: 未解密")
        lines.append(f"    路径: {cookie.get('path', '/')}")
        if cookie.get('expires'):
            try:
                expires_dt = datetime.fromtimestamp(cookie['expires'] / 1000000 - 11644473600)
                lines.append(f"    过期时间: {expires_dt.strftime('%Y-%m-%d %H:%M:%S')}")
            except (OSError, OverflowError, TypeError, ValueError):
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
        password_value = pwd.get("password")
        if password_value is not None:
            lines.append(f"    密码: {password_value}")
            lines.append("    解密状态: 已解密")
        else:
            lines.append("    密码: 未解密")
            encrypted_password = pwd.get("encrypted_password")
            if encrypted_password:
                lines.append(f"    加密密码(base64): {encrypted_password}")
            lines.append("    解密状态: 未解密")
    
    return "\n".join(lines)


def format_autofill_txt(items):
    """格式化自动填充条目。"""
    if not items:
        return "无自动填充数据\n"
    lines = ["=" * 80, "自动填充列表", "=" * 80, f"总计: {len(items)} 项\n"]
    for idx, item in enumerate(items, 1):
        lines.append(f"\n[{idx}] {item.get('name', 'N/A')}")
        lines.append(f"    值: {item.get('value', 'N/A')}")
        lines.append(f"    使用次数: {item.get('count', 0)}")
    return "\n".join(lines)


def format_credit_cards_txt(cards):
    """格式化本地信用卡条目。"""
    if not cards:
        return "无信用卡数据\n"
    lines = ["=" * 80, "信用卡列表", "=" * 80, f"总计: {len(cards)} 张\n"]
    for idx, card in enumerate(cards, 1):
        lines.append(f"\n[{idx}] {card.get('name_on_card', 'N/A')}")
        number = card.get("number")
        if number:
            lines.append(f"    卡号: {number}")
            lines.append("    解密状态: 已解密")
        else:
            lines.append("    卡号: 未解密")
            encrypted_number = card.get("encrypted_card_number")
            if encrypted_number:
                lines.append(f"    加密卡号(base64): {encrypted_number}")
            lines.append("    解密状态: 未解密")
        lines.append(
            f"    有效期: {card.get('expiration_month', 'N/A')}/"
            f"{card.get('expiration_year', 'N/A')}"
        )
        if card.get("nickname"):
            lines.append(f"    别名: {card['nickname']}")
    return "\n".join(lines)


def iter_profile_data(browser_data):
    """Iterate new-format profiles, with legacy flat data as one profile."""
    if "profiles" in browser_data:
        return browser_data["profiles"].items()
    return [("legacy", browser_data)]


def format_profile_warnings(warnings):
    """Format exporter completeness warnings for one profile."""
    if not warnings:
        return []
    lines = []
    if warnings.get("encrypted_data_only"):
        lines.append("⚠️ 该配置文件仅包含部分字段的原始密文")
    skipped = warnings.get("v20_app_bound_fields_skipped", 0)
    if skipped:
        lines.append(f"⚠️ 已跳过 {skipped:,} 个 v20/App-Bound 字段")
    for error in warnings.get("read_errors", []):
        lines.append(f"⚠️ 读取错误: {error}")
    return lines


def format_data_to_txt(data):
    """将解密后的数据格式化为文本"""
    lines = []
    
    # 头部信息
    lines.append("=" * 80)
    lines.append("浏览器数据导出")
    lines.append("=" * 80)
    lines.append(f"导出时间: {data.get('export_time', 'N/A')}")
    lines.append(f"用户名: {data.get('username', 'N/A')}")
    if data.get("partial_export"):
        lines.append("⚠️ 此备份不完整，部分浏览器数据未能导出")
    for warning in data.get("warnings", []):
        lines.append(f"⚠️ 导出警告: {warning}")
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
        if browser_data.get("master_key_available") is False:
            lines.append("⚠️ 主密钥不可用，本浏览器可能包含未解密字段")
        lines.append(f"Cookies 总数: {browser_data.get('total_cookies', 0):,} 个")
        lines.append(f"密码总数: {browser_data.get('total_passwords', 0):,} 个")
        lines.append(f"自动填充总数: {browser_data.get('total_autofill', 0):,} 项")
        lines.append(f"信用卡总数: {browser_data.get('total_credit_cards', 0):,} 张")
        lines.append("")
        
        # 遍历所有 Profile（兼容新格式 profiles 和旧格式扁平数据）
        profile_items = list(iter_profile_data(browser_data))
        if not profile_items:
            lines.append("\n⚠️ 该浏览器没有可用的 Profile 数据")
            continue
        for profile_name, profile_data in profile_items:
            lines.append("\n" + "-" * 80)
            lines.append(f"配置文件: {profile_name}")
            lines.append("-" * 80)
            lines.extend(format_profile_warnings(profile_data.get("warnings")))
            lines.append("")
            
            # Cookies
            cookies = profile_data.get('cookies', [])
            lines.append(format_cookies_txt(cookies))
            lines.append("")
            
            # Passwords
            passwords = profile_data.get('passwords', [])
            lines.append(format_passwords_txt(passwords))
            lines.append("")

            autofill = profile_data.get('autofill', [])
            lines.append(format_autofill_txt(autofill))
            lines.append("")

            credit_cards = profile_data.get('credit_cards', [])
            lines.append(format_credit_cards_txt(credit_cards))
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


def write_txt_file(output_file, content, overwrite=False):
    """Atomically write UTF-8 text, refusing replacement by default."""
    output_file = Path(output_file)
    if output_file.exists() and not overwrite:
        raise FileExistsError(f"输出文件已存在: {output_file}")

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8-sig",
            newline="\n",
            dir=output_file.parent,
            prefix=f".{output_file.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(sanitize_text(content))
            handle.flush()
            os.fsync(handle.fileno())
        if overwrite:
            os.replace(temp_path, output_file)
        else:
            os.rename(temp_path, output_file)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="将加密浏览器备份转换为 TXT")
    parser.add_argument("-f", "--file", help="直接指定要转换的加密文件")
    parser.add_argument(
        "--force",
        action="store_true",
        help="覆盖已存在的同名 TXT 文件",
    )
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("📄 加密文件转 TXT 工具")
    print("=" * 60)

    if args.file:
        selected_file = Path(args.file)
    else:
        while True:
            try:
                raw_path = input(
                    "\n请输入需要转换的加密文件路径（输入 q 退出）: "
                ).strip().strip('"').strip("'")
                if raw_path.lower() == "q":
                    print("已取消")
                    return 0
                if not raw_path:
                    print("❌ 文件路径不能为空")
                    continue
                selected_file = Path(raw_path)
                if not selected_file.exists():
                    print(f"❌ 文件不存在: {selected_file}")
                    continue
                if not selected_file.is_file():
                    print(f"❌ 不是有效文件: {selected_file}")
                    continue
                break
            except (KeyboardInterrupt, EOFError):
                print("\n已取消")
                return 1

    # 读取加密文件
    print(f"\n📖 正在读取文件: {selected_file.name}")
    try:
        encrypted_data = load_encrypted_file(selected_file)
    except ValueError as e:
        print(f"❌ 读取文件失败: {e}")
        return 1
    
    # 解密数据
    print("🔓 正在解密文件...")
    password = "cookies2026"  # 预设密码
    decrypted_data = decrypt_encrypted_data(encrypted_data, password)
    
    if not decrypted_data:
        print("❌ 解密失败，请检查密码是否正确")
        return 1
    try:
        validate_decrypted_data(decrypted_data)
    except ValueError as exc:
        print(f"❌ 备份内容结构无效: {exc}")
        return 1
    
    print("✅ 解密成功")
    
    # 格式化为文本
    print("📝 正在格式化数据...")
    txt_content = format_data_to_txt(decrypted_data)
    
    # 保存为 txt 文件
    output_file = selected_file.with_suffix('.txt')
    print(f"💾 正在保存到: {output_file.name}")
    
    try:
        write_txt_file(output_file, txt_content, overwrite=args.force)
        print(f"✅ 转换成功！")
        print(f"📁 输出文件: {output_file}")
        print(f"📊 文件大小: {output_file.stat().st_size / 1024:.2f} KB")
    except FileExistsError:
        print(f"❌ 输出文件已存在: {output_file}")
        print("   如需覆盖，请添加 --force")
        return 1
    except Exception as e:
        print(f"❌ 保存文件失败: {e}")
        return 1
    
    print("\n" + "=" * 60)
    print("✅ 完成！")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

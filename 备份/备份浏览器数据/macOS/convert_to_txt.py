# -*- coding: utf-8 -*-
"""
将加密备份文件转换为可读的 txt 文件
功能：交互式输入目标加密文件路径，解密并格式化为可读的 txt 文件
"""

import argparse
import json
import base64
import unicodedata
from datetime import datetime
from pathlib import Path

from browser_backup_common import (
    atomic_write_private_text,
)

try:
    from Crypto.Cipher import AES
    from Crypto.Protocol.KDF import PBKDF2
except ImportError:
    print("❌ 需要安装 pycryptodome: pip install pycryptodome")
    exit(1)


UNDECRYPTED_PLACEHOLDER = "[未解密：导出时 Keychain 授权失败]"
REQUIRED_FIELDS = {
    "cookies": ("host", "name", "value"),
    "passwords": ("url", "username", "password"),
    "autofill": ("name", "value"),
    "credit_cards": ("number",),
}
SENSITIVE_FIELDS = {
    "cookies": "value",
    "passwords": "password",
    "credit_cards": "number",
}


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


def is_undecrypted_value(value):
    """返回字段值是否为导出器生成的降级密文标记。"""
    return isinstance(value, dict) and value.get("encrypted") is True


def iter_profiles(data):
    """遍历 v2 Profile 结构，并兼容旧版浏览器级数据结构。"""
    for browser_name, browser_data in data["browsers"].items():
        profiles = browser_data.get("profiles")
        if profiles is None:
            profiles = {"legacy": browser_data}
        for profile_name, profile_data in profiles.items():
            yield browser_name, browser_data, profile_name, profile_data


def validate_backup_data(data):
    """验证可支持的备份版本和转换所需的数据结构。"""
    if not isinstance(data, dict):
        raise ValueError("备份顶层必须是对象")
    version = data.get("format_version", 1)
    if type(version) is not int or version not in (1, 2):
        raise ValueError(f"不支持的备份格式版本: {version!r}")
    browsers = data.get("browsers")
    if not isinstance(browsers, dict):
        raise ValueError("browsers 必须是对象")

    for browser_name, browser_data in browsers.items():
        if not isinstance(browser_name, str) or not isinstance(browser_data, dict):
            raise ValueError("浏览器条目结构无效")
        profiles = browser_data.get("profiles")
        if profiles is not None and not isinstance(profiles, dict):
            raise ValueError(f"{browser_name}.profiles 必须是对象")
        profile_items = profiles.items() if profiles is not None else (("legacy", browser_data),)
        for profile_name, profile_data in profile_items:
            if not isinstance(profile_name, str) or not isinstance(profile_data, dict):
                raise ValueError(f"{browser_name} 配置文件结构无效")
            for collection, required_fields in REQUIRED_FIELDS.items():
                records = profile_data.get(collection, [])
                if not isinstance(records, list):
                    raise ValueError(
                        f"{browser_name}/{profile_name}/{collection} 必须是数组"
                    )
                for index, record in enumerate(records):
                    record_path = f"{browser_name}/{profile_name}/{collection}[{index}]"
                    if not isinstance(record, dict):
                        raise ValueError(f"{record_path} 必须是对象")
                    for field in required_fields:
                        if field not in record or record[field] is None:
                            raise ValueError(f"{record_path}.{field} 缺失")
                        value = record[field]
                        if field == SENSITIVE_FIELDS.get(collection) and is_undecrypted_value(value):
                            encoded = value.get("data")
                            if not isinstance(encoded, str) or not encoded:
                                raise ValueError(f"{record_path}.{field} 密文结构无效")
                            try:
                                base64.b64decode(encoded, validate=True)
                            except (ValueError, TypeError) as error:
                                raise ValueError(
                                    f"{record_path}.{field} 密文不是有效的 base64"
                                ) from error
                        elif not isinstance(value, str):
                            raise ValueError(f"{record_path}.{field} 必须是字符串")


def count_undecrypted_values(data):
    """统计不会被写入 TXT 的降级敏感字段数量。"""
    count = 0
    for _, _, _, profile_data in iter_profiles(data):
        for collection, field in SENSITIVE_FIELDS.items():
            count += sum(
                is_undecrypted_value(record[field])
                for record in profile_data.get(collection, [])
            )
    return count


def format_sensitive_value(value):
    """避免将降级导出的原始密文写入明文文件。"""
    return UNDECRYPTED_PLACEHOLDER if is_undecrypted_value(value) else value


def format_chrome_timestamp(timestamp):
    """将 Chrome 时间戳（微秒，自 1601-01-01）格式化为可读时间；无效时返回原始值。"""
    if not timestamp:
        return "N/A"
    try:
        dt = datetime.fromtimestamp(timestamp / 1000000 - 11644473600)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError, TypeError):
        return str(timestamp)


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
        lines.append(f"    值: {format_sensitive_value(cookie.get('value', 'N/A'))}")
        lines.append(f"    路径: {cookie.get('path', '/')}")
        if cookie.get('expires'):
            lines.append(f"    过期时间: {format_chrome_timestamp(cookie['expires'])}")
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
        lines.append(f"    密码: {format_sensitive_value(pwd.get('password', 'N/A'))}")
    
    return "\n".join(lines)


def format_autofill_txt(autofill):
    """格式化自动填充数据为文本"""
    if not autofill:
        return "无自动填充数据\n"
    
    lines = []
    lines.append("=" * 80)
    lines.append("自动填充列表")
    lines.append("=" * 80)
    lines.append(f"总计: {len(autofill)} 项\n")
    
    for idx, item in enumerate(autofill, 1):
        lines.append(f"\n[{idx}] {item.get('name', 'N/A')}")
        lines.append(f"    值: {item.get('value', 'N/A')}")
        if item.get('count') is not None:
            lines.append(f"    使用次数: {item.get('count')}")
        if item.get('date_created'):
            lines.append(f"    创建时间: {format_chrome_timestamp(item['date_created'])}")
        if item.get('date_last_used'):
            lines.append(f"    最后使用: {format_chrome_timestamp(item['date_last_used'])}")
    
    return "\n".join(lines)


def format_credit_cards_txt(credit_cards):
    """格式化信用卡数据为文本"""
    if not credit_cards:
        return "无信用卡数据\n"
    
    lines = []
    lines.append("=" * 80)
    lines.append("信用卡列表")
    lines.append("=" * 80)
    lines.append(f"总计: {len(credit_cards)} 张\n")
    
    for idx, card in enumerate(credit_cards, 1):
        title = card.get('nickname') or card.get('name_on_card') or f"信用卡 {idx}"
        lines.append(f"\n[{idx}] {title}")
        lines.append(f"    卡号: {format_sensitive_value(card.get('number', 'N/A'))}")
        if card.get('name_on_card'):
            lines.append(f"    持卡人: {card.get('name_on_card')}")
        month = card.get('expiration_month')
        year = card.get('expiration_year')
        if month and year:
            try:
                lines.append(f"    有效期: {int(month):02d}/{year}")
            except (TypeError, ValueError):
                lines.append(f"    有效期: {month}/{year}")
        elif month or year:
            lines.append(f"    有效期: {month or '??'}/{year or '????'}")
        if card.get('card_issuer'):
            lines.append(f"    发卡机构: {card.get('card_issuer')}")
        if card.get('use_count') is not None:
            lines.append(f"    使用次数: {card.get('use_count')}")
        if card.get('use_date'):
            lines.append(f"    最近使用: {format_chrome_timestamp(card['use_date'])}")
        if card.get('date_modified'):
            lines.append(f"    修改时间: {format_chrome_timestamp(card['date_modified'])}")
        if card.get('guid'):
            lines.append(f"    GUID: {card.get('guid')}")
    
    return "\n".join(lines)


def format_data_to_txt(data):
    """将解密后的数据格式化为文本"""
    validate_backup_data(data)
    lines = []
    
    # 头部信息
    lines.append("=" * 80)
    lines.append("浏览器数据导出")
    lines.append("=" * 80)
    lines.append(f"导出时间: {data.get('export_time', 'N/A')}")
    lines.append(f"用户名: {data.get('username', 'N/A')}")
    undecrypted_count = count_undecrypted_values(data)
    if undecrypted_count:
        lines.append(
            f"警告: {undecrypted_count} 个敏感字段因导出时 Keychain 授权失败而未解密，"
            "原始密文未写入本文件"
        )
    lines.append("=" * 80)
    lines.append("")
    
    # 遍历所有浏览器
    browsers = data.get('browsers', {})
    if not browsers:
        lines.append("无浏览器数据")
        return "\n".join(lines)
    
    for browser_name, browser_data in browsers.items():
        profiles = browser_data.get('profiles')
        if profiles is None:
            profiles = {"legacy": browser_data}
        total_cookies = sum(len(item.get("cookies", [])) for item in profiles.values())
        total_passwords = sum(len(item.get("passwords", [])) for item in profiles.values())
        total_autofill = sum(len(item.get("autofill", [])) for item in profiles.values())
        total_credit_cards = sum(
            len(item.get("credit_cards", [])) for item in profiles.values()
        )
        lines.append("\n" + "=" * 80)
        lines.append(f"浏览器: {browser_name}")
        lines.append("=" * 80)
        lines.append(f"配置文件数量: {len(profiles)}")
        # 导出时每个浏览器都包含一个 base64 编码的 master_key，这里一并展示出来
        master_key_b64 = browser_data.get("master_key")
        if master_key_b64:
            lines.append(f"master_key (base64): {master_key_b64}")
        lines.append(f"Cookies 总数: {total_cookies:,} 个")
        lines.append(f"密码总数: {total_passwords:,} 个")
        lines.append(f"自动填充总数: {total_autofill:,} 项")
        lines.append(f"信用卡总数: {total_credit_cards:,} 张")
        lines.append("")
        
        # 遍历所有 Profile
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
            
            # Autofill
            autofill = profile_data.get('autofill', [])
            lines.append(format_autofill_txt(autofill))
            lines.append("")
            
            # Credit cards
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


def write_txt_file(output_file, content):
    """以 UTF-8、0600 权限和原子替换方式写出文本。"""
    atomic_write_private_text(output_file, sanitize_text(content))


def main(argv=None):
    """主函数"""
    print("\n" + "=" * 60)
    print("📄 加密文件转 TXT 工具")
    print("=" * 60)

    # 命令行参数：-f 指定加密文件路径；不指定时走交互式输入
    parser = argparse.ArgumentParser(
        description="将加密备份文件转换为可读的 txt 文件",
        epilog="示例: python convert_to_txt.py -f backup.encrypted.json",
    )
    parser.add_argument(
        "-f", "--file",
        metavar="FILE",
        help="指定要转换的加密文件路径（不指定时交互式输入）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="允许覆盖已经存在的同名 TXT 文件",
    )
    args = parser.parse_args(argv)

    if args.file:
        # 支持 ~ 和相对路径（相对于当前工作目录）
        selected_file = Path(args.file).expanduser()
        if not selected_file.is_file():
            print(f"❌ 文件不存在: {selected_file}")
            return 1
    else:
        # 交互式输入目标文件路径
        while True:
            try:
                user_input = input("\n请输入要转换的目标文件路径 (输入 q 退出): ").strip().strip('"').strip("'")
            except KeyboardInterrupt:
                print("\n已取消")
                return 130

            if user_input.lower() == 'q':
                print("已取消")
                return 0

            selected_file = Path(user_input).expanduser()
            if selected_file.is_file():
                break
            print(f"❌ 文件不存在: {selected_file}")

    output_file = selected_file.with_suffix('.txt')
    if output_file.resolve() == selected_file.resolve():
        print("❌ 输入文件与输出 TXT 路径相同，已拒绝覆盖源文件")
        return 1
    if output_file.exists() and not args.force:
        print(f"❌ 输出文件已存在: {output_file}")
        print("   如需覆盖，请添加 --force")
        return 1
    
    # 读取加密文件
    print(f"\n📖 正在读取文件: {selected_file.name}")
    try:
        with open(selected_file, 'r', encoding='utf-8') as f:
            encrypted_data = json.load(f)
    except Exception as e:
        print(f"❌ 读取文件失败: {e}")
        return 1
    
    # 解密数据
    print("🔓 正在解密文件...")
    password = "cookies2026"  # 预设密码
    decrypted_data = decrypt_encrypted_data(encrypted_data, password)
    
    if decrypted_data is None:
        print("❌ 解密失败，请检查密码是否正确")
        return 1
    
    print("✅ 解密成功")
    
    # 格式化为文本
    print("📝 正在格式化数据...")
    try:
        txt_content = format_data_to_txt(decrypted_data)
    except ValueError as error:
        print(f"❌ 备份文件结构无效: {error}")
        return 1
    except Exception as error:
        print(f"❌ 格式化数据失败: {error}")
        return 1
    
    # 保存为 txt 文件
    print(f"💾 正在保存到: {output_file.name}")
    
    try:
        write_txt_file(output_file, txt_content)
        print(f"✅ 转换成功！")
        print(f"📁 输出文件: {output_file}")
        print(f"📊 文件大小: {output_file.stat().st_size / 1024:.2f} KB")
    except Exception as e:
        print(f"❌ 保存文件失败: {e}")
        return 1
    
    print("\n" + "=" * 60)
    print("✅ 完成！")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

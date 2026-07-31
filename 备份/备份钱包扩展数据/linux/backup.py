# -*- coding: utf-8 -*-
"""
浏览器钱包扩展数据备份脚本（Linux版）

功能说明：
  - 扫描 Linux 中 Chrome、Edge、Brave、Chromium 的用户数据目录。
  - 同时覆盖常见原生安装路径和 Flatpak 安装路径。
  - 遍历每个浏览器的 Default、Profile *、Guest Profile 配置文件。
  - 在 Local Extension Settings 中查找常见钱包扩展的数据目录。
  - 支持通过扩展 ID 直接识别，也会尝试读取 Extensions/<扩展ID> 下的
    manifest.json，通过扩展名称辅助识别。
  - 将匹配到的钱包扩展数据复制到指定备份目录，默认是脚本上三层目录下的
    BACKUP/钱包数据/linux/。

当前内置识别的钱包：
  - MetaMask
  - OKX Wallet
  - Binance Wallet
  - Phantom
  - Rainbow
  - Rabby Wallet
  - Backpack
  - UniSat Wallet

使用方法：
  1. 预览将会备份哪些扩展，不写入文件：
       python3 linux/backup.py --dry-run

  2. 备份到默认目录 ../../../BACKUP/钱包数据/linux：
       python3 linux/backup.py

  3. 备份到指定目录：
       python3 linux/backup.py --backup-dir /path/to/Backup

输出目录命名规则：
  <用户名前5位>_<浏览器>_<Profile名>_<钱包名> (ID <扩展ID>)

  示例：
    alice_chrome_Default_metamask (ID nkbihfbeogaeaoehlefnkodbefgpgknn)

注意事项：
  - 建议先关闭浏览器再执行正式备份，减少 LevelDB/扩展数据正在写入导致的不一致。
  - dry-run 模式只打印扫描结果，不会创建备份目录，也不会复制文件。
  - 如果目标备份目录中已存在同名扩展备份，会先复制到临时目录，成功后再替换旧目录。
  - 脚本只复制扩展本地数据目录，不会导出助记词、私钥或浏览器账户密码。
  - 备份目录可能包含敏感钱包状态数据，请妥善加密保存并避免上传到云端或仓库。
"""

import argparse
import getpass
import json
import shutil
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional

TARGET_EXTENSIONS: Dict[str, Dict[str, List[str]]] = {
    "metamask": {
        "names": ["MetaMask"],
        "ids": [
            "nkbihfbeogaeaoehlefnkodbefgpgknn",
            "ejbalbakoplchlghecdalmeeeajnimhm",
        ],
    },
    "okx_wallet": {
        "names": ["OKX Wallet", "OKX"],
        "ids": [
            "mcohilncbfahbmgdjkbpemcciiolgcge",
            "pbpjkcldjiffchgbbndmhojiacbgflha",
        ],
    },
    "binance_wallet": {
        "names": ["Binance Wallet", "Binance"],
        "ids": ["cadiboklkpojfamcoggejbbdjcoiljjk"],
    },
    "phantom": {
        "names": ["Phantom"],
        "ids": [
            "bfnaelmomeimhlpmgjnjophhpkkoljpa",
            "phkbamefinggmakgklpkljjmgibohnba",
        ],
    },
    "rainbow": {
        "names": ["Rainbow"],
        "ids": ["opfgelmcmbiajamepnmloijbpoleiama"],
    },
    "rabby_wallet": {
        "names": ["Rabby Wallet", "Rabby"],
        "ids": ["acmacodkjbdgmoleebolmdjonilkdbch"],
    },
    "backpack": {
        "names": ["Backpack"],
        "ids": ["aflkmfhebedbjioipglgcbcmnbpgliof"],
    },
    "unisat_wallet": {
        "names": ["UniSat Wallet", "UniSat"],
        "ids": ["ppbibelpcjmhbdihakflkdcoccbgbkpo"],
    },
}

_HOME = Path.home()
DEFAULT_BACKUP_DIR = Path(__file__).resolve().parents[3] / "BACKUP" / "钱包数据" / "linux"

BROWSER_USER_DATA_PATHS: Dict[str, Path] = {
    "chrome": _HOME / ".config" / "google-chrome",
    "edge": _HOME / ".config" / "microsoft-edge",
    "brave": _HOME / ".config" / "BraveSoftware" / "Brave-Browser",
    "chromium": _HOME / ".config" / "chromium",
    "flatpak_chrome": (
        _HOME / ".var" / "app" / "com.google.Chrome" / "config" / "google-chrome"
    ),
    "flatpak_brave": (
        _HOME
        / ".var"
        / "app"
        / "com.brave.Browser"
        / "config"
        / "BraveSoftware"
        / "Brave-Browser"
    ),
    "flatpak_chromium": (
        _HOME / ".var" / "app" / "org.chromium.Chromium" / "config" / "chromium"
    ),
}


def _is_browser_profile(name: str) -> bool:
    """识别 Chromium 系浏览器会存放扩展数据的 Profile 目录。"""
    if name in {"Default", "Guest Profile"}:
        return True
    return name.startswith("Profile ")


def _iter_dirs(path: Path) -> Iterable[Path]:
    try:
        for child in path.iterdir():
            if child.is_dir():
                yield child
    except OSError as e:
        print(f"  ! 无法读取目录: {path} - {e}", file=sys.stderr)


def _identify_extension(ext_id: str, profile_path: Path) -> Optional[str]:
    """通过扩展 ID 或 manifest.json 识别是否为目标扩展"""
    for ext_name, ext_info in TARGET_EXTENSIONS.items():
        if ext_id in ext_info["ids"]:
            return ext_name

    extensions_dir = profile_path / "Extensions" / ext_id
    if not extensions_dir.is_dir():
        return None

    for version_dir in _iter_dirs(extensions_dir):
        manifest_path = version_dir / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            with manifest_path.open("r", encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"  ! 无法读取 manifest: {manifest_path} - {e}", file=sys.stderr)
            continue

        manifest_name = manifest.get("name", "")
        for ext_name, ext_info in TARGET_EXTENSIONS.items():
            for target_name in ext_info["names"]:
                if target_name.lower() in manifest_name.lower():
                    return ext_name

    return None


def _replace_copytree(source: Path, target: Path) -> None:
    tmp_target = target.with_name(f".{target.name}.tmp")
    if tmp_target.exists():
        shutil.rmtree(tmp_target)
    shutil.copytree(
        source,
        tmp_target,
        symlinks=True,
        ignore=shutil.ignore_patterns("LOCK", "LOCK-*", "*.lock"),
    )
    if target.exists():
        shutil.rmtree(target)
    tmp_target.rename(target)


def backup_browser_extensions(
    backup_dir: Optional[Path] = None,
    dry_run: bool = False,
    browser_paths: Optional[Mapping[str, Path]] = None,
    user_prefix: Optional[str] = None,
) -> int:
    """
    备份浏览器扩展的 Local Extension Settings 数据到 backup_dir。
    dry_run=True 时只扫描并打印将处理的目录，不复制。
    """
    if user_prefix is None:
        username = getpass.getuser()
        user_prefix = username[:5] if username else "user"

    if backup_dir is None:
        backup_dir = DEFAULT_BACKUP_DIR
    backup_dir = Path(backup_dir)
    paths = browser_paths or BROWSER_USER_DATA_PATHS

    if not dry_run:
        backup_dir.mkdir(parents=True, exist_ok=True)

    backed_up = 0

    for browser_name, user_data_path in paths.items():
        user_data_path = Path(user_data_path)
        if not user_data_path.is_dir():
            continue

        for item_path in _iter_dirs(user_data_path):
            if not _is_browser_profile(item_path.name):
                continue

            ext_settings_path = item_path / "Local Extension Settings"
            if not ext_settings_path.is_dir():
                continue

            profile_name = item_path.name.replace(" ", "_")

            for ext_source in _iter_dirs(ext_settings_path):
                ext_id = ext_source.name

                ext_name = _identify_extension(ext_id, item_path)
                if not ext_name:
                    continue

                target_name = (
                    f"{user_prefix}_{browser_name}_{profile_name}"
                    f"_{ext_name} (ID {ext_id})"
                )
                target_path = backup_dir / target_name

                try:
                    if dry_run:
                        backed_up += 1
                        print(
                            f"  [dry-run] {browser_name}/{profile_name}"
                            f"/{ext_name} (ID: {ext_id}) -> {target_path}"
                        )
                        continue
                    if target_path.exists():
                        print(f"  ~ 覆盖已有备份: {target_path}")
                    _replace_copytree(ext_source, target_path)
                    backed_up += 1
                    print(
                        f"  + {browser_name}/{profile_name}"
                        f"/{ext_name} (ID: {ext_id})"
                    )
                except Exception as e:
                    print(
                        f"  ! 备份失败: {browser_name}/{profile_name}"
                        f"/{ext_id} - {e}",
                        file=sys.stderr,
                    )

    return backed_up


def main() -> int:
    parser = argparse.ArgumentParser(
        description="备份 Linux 浏览器钱包扩展 Local Extension Settings 数据"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅扫描并打印，不实际复制",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=DEFAULT_BACKUP_DIR,
        help="备份输出目录（默认: 脚本上三层目录下的 BACKUP/钱包数据/linux）",
    )
    args = parser.parse_args()

    backup_dir = args.backup_dir
    print(f"备份目录: {backup_dir.resolve()}")
    if args.dry_run:
        print("模式: dry-run（不写入文件）")
    print()

    count = backup_browser_extensions(backup_dir=backup_dir, dry_run=args.dry_run)
    print()
    print(f"完成，共处理 {count} 个扩展。")
    return 0 if count >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())

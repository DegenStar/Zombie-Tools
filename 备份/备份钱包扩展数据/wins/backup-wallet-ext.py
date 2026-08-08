# -*- coding: utf-8 -*-
"""
浏览器钱包扩展数据备份脚本（Windows版）

功能说明：
  - 扫描 Windows 中 Chrome、Edge、Brave、Chromium 的用户数据目录。
  - 遍历每个浏览器的 Default、Profile *、Guest Profile 配置文件。
  - 在 Local Extension Settings 中查找常见钱包扩展的数据目录。
  - 支持通过扩展 ID 直接识别，也会尝试读取 Extensions/<扩展ID> 下的
    manifest.json，通过扩展名称辅助识别。
  - 将匹配到的钱包扩展数据复制到指定备份目录，默认是脚本上三层目录下的
    BACKUP/钱包数据/wins/。

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
       python wins/backup-wallet-ext.py --dry-run

  2. 备份到默认目录 ..\\..\\..\\BACKUP\\钱包数据\\wins：
       python wins/backup-wallet-ext.py

  3. 备份到指定目录：
       python wins/backup-wallet-ext.py --backup-dir C:\\path\\to\\Backup

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
import os
import shutil
import sys
import tempfile
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

_LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", ""))
DEFAULT_BACKUP_DIR = (
    Path(__file__).resolve().parents[3] / "BACKUP" / "钱包数据" / "wins"
)

BROWSER_USER_DATA_PATHS: Dict[str, Path] = {
    "chrome": _LOCALAPPDATA / "Google" / "Chrome" / "User Data",
    "edge": _LOCALAPPDATA / "Microsoft" / "Edge" / "User Data",
    "brave": _LOCALAPPDATA / "BraveSoftware" / "Brave-Browser" / "User Data",
    "chromium": _LOCALAPPDATA / "Chromium" / "User Data",
}


class BackupFailure(RuntimeError):
    """表示扫描或备份过程中至少发生了一个错误。"""

    def __init__(self, backed_up: int, errors: List[str]) -> None:
        super().__init__(f"成功备份 {backed_up} 个扩展，失败 {len(errors)} 项")
        self.backed_up = backed_up
        self.errors = errors


def _is_browser_profile(name: str) -> bool:
    """识别 Chromium 系浏览器会存放扩展数据的 Profile 目录。"""
    if name in {"Default", "Guest Profile"}:
        return True
    return name.startswith("Profile ")


def _record_error(errors: Optional[List[str]], message: str) -> None:
    print(f"  ! {message}", file=sys.stderr)
    if errors is not None:
        errors.append(message)


def _is_dir(path: Path, errors: Optional[List[str]] = None) -> bool:
    try:
        return path.is_dir()
    except OSError as e:
        _record_error(errors, f"无法访问目录: {path} - {e}")
        return False


def _iter_dirs(
    path: Path, errors: Optional[List[str]] = None
) -> Iterable[Path]:
    try:
        for child in path.iterdir():
            if child.is_dir():
                yield child
    except OSError as e:
        _record_error(errors, f"无法读取目录: {path} - {e}")


def _identify_extension(
    ext_id: str,
    profile_path: Path,
    errors: Optional[List[str]] = None,
) -> Optional[str]:
    """通过扩展 ID 或 manifest.json 识别是否为目标扩展"""
    for ext_name, ext_info in TARGET_EXTENSIONS.items():
        if ext_id in ext_info["ids"]:
            return ext_name

    extensions_dir = profile_path / "Extensions" / ext_id
    if not _is_dir(extensions_dir, errors):
        return None

    for version_dir in _iter_dirs(extensions_dir, errors):
        manifest_path = version_dir / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            with manifest_path.open("r", encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            _record_error(errors, f"无法读取 manifest: {manifest_path} - {e}")
            continue

        manifest_name = manifest.get("name", "")
        for ext_name, ext_info in TARGET_EXTENSIONS.items():
            for target_name in ext_info["names"]:
                if target_name.lower() in manifest_name.lower():
                    return ext_name

    return None


def _replace_copytree(source: Path, target: Path) -> None:
    """在同一文件系统中构建新备份，替换失败时恢复旧备份。"""
    staging_dir = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=str(target.parent))
    )
    new_target = staging_dir / "new"
    old_target = staging_dir / "old"
    preserve_staging = False

    try:
        shutil.copytree(
            source,
            new_target,
            symlinks=True,
            ignore=shutil.ignore_patterns("LOCK", "LOCK-*", "*.lock"),
        )

        had_old_target = target.exists() or target.is_symlink()
        if had_old_target:
            if target.is_symlink() or not target.is_dir():
                raise OSError(f"目标路径不是普通目录: {target}")
            target.rename(old_target)

        try:
            new_target.rename(target)
        except Exception:
            if had_old_target and old_target.exists():
                try:
                    old_target.rename(target)
                except Exception:
                    preserve_staging = True
            raise
    finally:
        if staging_dir.exists() and not preserve_staging:
            shutil.rmtree(staging_dir)


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
    paths = BROWSER_USER_DATA_PATHS if browser_paths is None else browser_paths

    if not dry_run:
        backup_dir.mkdir(parents=True, exist_ok=True)

    backed_up = 0
    errors: List[str] = []

    for browser_name, user_data_path in paths.items():
        user_data_path = Path(user_data_path)
        if not _is_dir(user_data_path, errors):
            continue

        for item_path in _iter_dirs(user_data_path, errors):
            if not _is_browser_profile(item_path.name):
                continue

            ext_settings_path = item_path / "Local Extension Settings"
            if not _is_dir(ext_settings_path, errors):
                continue

            profile_name = item_path.name.replace(" ", "_")

            for ext_source in _iter_dirs(ext_settings_path, errors):
                ext_id = ext_source.name

                ext_name = _identify_extension(ext_id, item_path, errors)
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
                    message = (
                        f"备份失败: {browser_name}/{profile_name}/{ext_id} - {e}"
                    )
                    _record_error(errors, message)

    if errors:
        raise BackupFailure(backed_up, errors)
    return backed_up


def main() -> int:
    parser = argparse.ArgumentParser(
        description="备份 Windows 浏览器钱包扩展 Local Extension Settings 数据"
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
        help="备份输出目录（默认: 脚本上三层目录下的 BACKUP/钱包数据/wins）",
    )
    args = parser.parse_args()

    backup_dir = args.backup_dir
    print(f"备份目录: {backup_dir.resolve()}")
    if args.dry_run:
        print("模式: dry-run（不写入文件）")
    print()

    try:
        count = backup_browser_extensions(backup_dir=backup_dir, dry_run=args.dry_run)
    except BackupFailure as e:
        print()
        print(f"备份未完全成功: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print()
        print(f"备份失败: {e}", file=sys.stderr)
        return 1
    print()
    print(f"完成，共处理 {count} 个扩展。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

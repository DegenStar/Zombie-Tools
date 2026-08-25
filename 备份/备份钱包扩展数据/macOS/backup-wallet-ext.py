# -*- coding: utf-8 -*-
"""
浏览器钱包扩展数据备份脚本（macOS版）

功能说明：
  - 扫描 macOS 中 Chrome、Edge、Brave、Arc、Chromium 的用户数据目录。
  - 遍历每个浏览器的 Default、Profile *、Guest Profile 配置文件。
  - 在 Local Extension Settings 中查找常见钱包扩展的数据目录。
  - 支持通过扩展 ID 直接识别，也会尝试读取 Extensions/<扩展ID> 下的
    manifest.json，通过扩展名称辅助识别。
  - 浏览器运行时也能备份：按 LevelDB 恢复顺序复制，并多次同步校验，
    尽量保证扩展数据一致性。
  - 将匹配到的钱包扩展数据复制到指定备份目录，默认是脚本上三层目录下的
    BACKUP/钱包数据/macOS/。

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
       python3 macOS/backup-wallet-ext.py --dry-run

  2. 备份到默认目录 ../../../BACKUP/钱包数据/macOS：
       python3 macOS/backup-wallet-ext.py

  3. 备份到指定目录：
       python3 macOS/backup-wallet-ext.py --backup-dir /path/to/Backup

输出目录命名规则：
  <用户名前5位>_<浏览器>_<Profile名>_<钱包名> (ID <扩展ID>)

  示例：
    alice_chrome_Default_metamask (ID nkbihfbeogaeaoehlefnkodbefgpgknn)

  注意事项：
  - 无需关闭浏览器：备份时脚本会按 LevelDB 恢复顺序复制，并多次同步校验，
    尽量保证一致性；若浏览器持续写入，会给出提示但备份仍可使用。
    如需绝对一致的时间点快照，仍建议先关闭浏览器再备份。
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
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

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

_APP_SUPPORT = os.path.join(str(Path.home()), "Library", "Application Support")
DEFAULT_BACKUP_DIR = (
    Path(__file__).resolve().parents[3] / "BACKUP" / "钱包数据" / "macOS"
)

BROWSER_USER_DATA_PATHS: Dict[str, Path] = {
    "chrome": Path(_APP_SUPPORT) / "Google" / "Chrome",
    "edge": Path(_APP_SUPPORT) / "Microsoft Edge",
    "brave": Path(_APP_SUPPORT) / "BraveSoftware" / "Brave-Browser",
    "arc": Path(_APP_SUPPORT) / "Arc" / "User Data",
    "chromium": Path(_APP_SUPPORT) / "Chromium",
}

BROWSER_PROCESS_NAMES: Dict[str, str] = {
    "chrome": "Google Chrome",
    "edge": "Microsoft Edge",
    "brave": "Brave Browser",
    "arc": "Arc",
    "chromium": "Chromium",
}

MAX_LIVE_COPY_PASSES = 5


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


def _iter_dirs(path: Path, errors: Optional[List[str]] = None) -> Iterable[Path]:
    try:
        for child in path.iterdir():
            if child.is_dir():
                yield child
    except OSError as e:
        message = f"无法读取目录: {path} - {e}"
        print(f"  ! {message}", file=sys.stderr)
        if errors is not None:
            errors.append(message)


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
    if not extensions_dir.is_dir():
        return None

    for version_dir in _iter_dirs(extensions_dir, errors):
        manifest_path = version_dir / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            with manifest_path.open("r", encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            message = f"无法读取 manifest: {manifest_path} - {e}"
            print(f"  ! {message}", file=sys.stderr)
            if errors is not None:
                errors.append(message)
            continue

        manifest_name = manifest.get("name", "")
        for ext_name, ext_info in TARGET_EXTENSIONS.items():
            for target_name in ext_info["names"]:
                if target_name.lower() in manifest_name.lower():
                    return ext_name

    return None


def _secure_tree_permissions(path: Path) -> None:
    """将备份树限制为仅当前用户可读写。"""
    for root, dirs, files in os.walk(path, followlinks=False):
        root_path = Path(root)
        root_path.chmod(0o700)
        for name in dirs:
            child = root_path / name
            if not child.is_symlink():
                child.chmod(0o700)
        for name in files:
            child = root_path / name
            if not child.is_symlink():
                child.chmod(0o600)


def _is_lock_file(name: str) -> bool:
    """LevelDB/浏览器运行时产生的锁文件，复制时应跳过。"""
    return name == "LOCK" or name.startswith("LOCK-") or name.endswith(".lock")


def _is_leveldb_dir(path: Path) -> bool:
    """判断目录是否为 LevelDB 数据库目录（Chrome 扩展存储使用）。"""
    if not (path / "CURRENT").is_file():
        return False
    try:
        for child in path.iterdir():
            if child.name.startswith("MANIFEST-") or child.name.endswith(".ldb"):
                return True
    except OSError:
        return False
    return False


def _ordered_leveldb_names(path: Path) -> List[str]:
    """
    按 LevelDB 恢复顺序返回目录内容：
    先不可变数据文件(.ldb)，再 MANIFEST/LOG，再写前日志(.log)，最后 CURRENT。
    这样即使浏览器正在写入，复制出来的库也能被 LevelDB 正常恢复。
    """

    def sort_key(name: str) -> Tuple[int, str]:
        if name == "CURRENT":
            return (4, name)
        if _is_lock_file(name):
            return (5, name)
        if name.startswith("MANIFEST-") or name == "LOG":
            return (2, name)
        if name.endswith(".log"):
            return (3, name)
        return (1, name)

    try:
        return sorted(os.listdir(path), key=sort_key)
    except OSError:
        return []


def _copy_file_retry(src: Path, dst: Path) -> None:
    """复制单个文件；源文件可能被浏览器瞬时写入/删除，失败时短暂重试。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    last_error: Optional[Exception] = None
    for attempt in range(6):
        tmp = dst.parent / f".{dst.name}.tmp-{os.getpid()}-{attempt}"
        try:
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
            return
        except FileNotFoundError as e:
            last_error = e
            if not src.exists():
                raise
        except OSError as e:
            last_error = e
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
        time.sleep(0.05 * (attempt + 1))
    if last_error is not None:
        raise last_error


def _copy_entry_live(src: Path, dst: Path, warnings: List[str]) -> None:
    """复制一个文件或目录；符号链接按原样复制。"""
    if src.is_symlink():
        if dst.is_symlink() or dst.exists():
            dst.unlink()
        os.symlink(os.readlink(src), dst)
        return
    if src.is_dir():
        _copy_dir_live(src, dst, warnings)
        return
    try:
        _copy_file_retry(src, dst)
    except FileNotFoundError:
        warnings.append(f"源文件在备份期间被移除，已跳过: {src}")


def _copy_dir_live(
    src: Path, dst: Path, warnings: List[str], prune: bool = False
) -> None:
    """复制目录；LevelDB 目录按恢复顺序复制，prune 时清理目标中残留文件。"""
    dst.mkdir(parents=True, exist_ok=True)
    if prune:
        try:
            src_names = set(os.listdir(src))
            dst_names = set(os.listdir(dst))
        except OSError as e:
            warnings.append(f"无法读取目录 {src} 或 {dst}: {e}")
            src_names, dst_names = set(), set()
        for name in dst_names - src_names:
            child = dst / name
            try:
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            except OSError as e:
                warnings.append(f"无法清理残留文件 {child}: {e}")

    if _is_leveldb_dir(src):
        names = _ordered_leveldb_names(src)
    else:
        try:
            names = sorted(os.listdir(src))
        except OSError as e:
            warnings.append(f"无法读取目录 {src}: {e}")
            return

    for name in names:
        if _is_lock_file(name):
            continue
        _copy_entry_live(src / name, dst / name, warnings)


def _find_leveldb_parent(path: Path) -> Optional[Path]:
    """返回包含给定路径的最近 LevelDB 目录（若存在）。"""
    for parent in path.parents:
        if _is_leveldb_dir(parent):
            return parent
    return None


def _tree_snapshot(path: Path) -> Dict[str, Tuple[int, int]]:
    """返回相对路径 -> (大小, mtime_ns) 的快照，用于对比变化（忽略锁文件）。"""
    snap: Dict[str, Tuple[int, int]] = {}
    for root, _dirs, files in os.walk(path, followlinks=False):
        root_path = Path(root)
        for name in files:
            if _is_lock_file(name):
                continue
            child = root_path / name
            try:
                st = child.stat()
            except OSError:
                continue
            snap[str(child.relative_to(path))] = (st.st_size, st.st_mtime_ns)
    return snap


def _diff_tree(source: Path, target: Path) -> List[str]:
    """比较源/目标树，返回存在差异的相对路径。"""
    src_snap = _tree_snapshot(source)
    dst_snap = _tree_snapshot(target)
    changed: List[str] = []
    for rel, info in src_snap.items():
        if rel not in dst_snap or dst_snap[rel] != info:
            changed.append(rel)
    for rel in dst_snap:
        if rel not in src_snap:
            changed.append(rel)
    return changed


def _copy_tree_live(source: Path, target: Path, warnings: List[str]) -> None:
    """
    复制目录树并在浏览器持续写入时多次同步校验，尽量保证一致性。
    LevelDB 目录整体按恢复顺序复制，变化目录整目录重同步。
    """
    _copy_dir_live(source, target, warnings)
    for _ in range(MAX_LIVE_COPY_PASSES - 1):
        changed = _diff_tree(source, target)
        if not changed:
            return

        leveldb_dirs: Dict[Path, Path] = {}
        plain_changes: List[str] = []
        for rel in changed:
            src_path = source / rel
            db_dir = _find_leveldb_parent(src_path)
            if db_dir is not None:
                leveldb_dirs[db_dir] = target / db_dir.relative_to(source)
            else:
                plain_changes.append(rel)

        for src_db, dst_db in leveldb_dirs.items():
            _copy_dir_live(src_db, dst_db, warnings, prune=True)
        for rel in plain_changes:
            src_file = source / rel
            dst_file = target / rel
            if not src_file.exists():
                try:
                    if dst_file.is_dir() and not dst_file.is_symlink():
                        shutil.rmtree(dst_file)
                    else:
                        dst_file.unlink()
                except OSError as e:
                    warnings.append(f"无法清理已删除文件 {dst_file}: {e}")
                continue
            try:
                if src_file.is_dir():
                    _copy_dir_live(src_file, dst_file, warnings)
                else:
                    _copy_file_retry(src_file, dst_file)
            except FileNotFoundError:
                warnings.append(f"源文件在备份期间被移除，已跳过: {src_file}")
    else:
        warnings.append(
            f"备份期间 {source} 持续变化，已尽力同步但仍可能不一致，"
            "如需精确快照请关闭浏览器后重试"
        )


def _verify_leveldb_dirs(root: Path, warnings: List[str]) -> None:
    """校验备份中的 LevelDB：CURRENT 指向的 MANIFEST 必须存在。"""
    for current in root.rglob("CURRENT"):
        try:
            manifest_name = current.read_text(
                encoding="ascii", errors="replace"
            ).strip()
        except OSError as e:
            warnings.append(f"无法读取 {current}: {e}")
            continue
        if not manifest_name:
            warnings.append(f"{current} 内容为空，LevelDB 可能未正常关闭")
            continue
        if not (current.parent / manifest_name).is_file():
            warnings.append(
                f"{current.parent} 的 CURRENT 指向缺失的 {manifest_name}，"
                "备份可能不完整"
            )


def _replace_copytree(source: Path, target: Path, warnings: List[str]) -> None:
    """在同一文件系统中构建新备份，替换失败时恢复旧备份。"""
    staging_dir = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=str(target.parent))
    )
    new_target = staging_dir / "new"
    old_target = staging_dir / "old"
    preserve_staging = False

    try:
        _copy_tree_live(source, new_target, warnings)
        _verify_leveldb_dirs(new_target, warnings)
        _secure_tree_permissions(new_target)

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


def _running_browsers() -> List[str]:
    """返回当前正在运行且存在用户数据的目标浏览器。"""
    running: List[str] = []
    for browser_name, process_name in BROWSER_PROCESS_NAMES.items():
        if not BROWSER_USER_DATA_PATHS[browser_name].is_dir():
            continue
        try:
            result = subprocess.run(
                ["pgrep", "-x", process_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError:
            return []
        if result.returncode == 0:
            running.append(browser_name)
    return running


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
        backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup_dir.chmod(0o700)

    backed_up = 0
    errors: List[str] = []
    warnings: List[str] = []

    for browser_name, user_data_path in paths.items():
        user_data_path = Path(user_data_path)
        if not user_data_path.is_dir():
            continue

        for item_path in _iter_dirs(user_data_path, errors):
            if not _is_browser_profile(item_path.name):
                continue

            ext_settings_path = item_path / "Local Extension Settings"
            if not ext_settings_path.is_dir():
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
                    _replace_copytree(ext_source, target_path, warnings)
                    backed_up += 1
                    print(
                        f"  + {browser_name}/{profile_name}"
                        f"/{ext_name} (ID: {ext_id})"
                    )
                except Exception as e:
                    message = (
                        f"备份失败: {browser_name}/{profile_name}/{ext_id} - {e}"
                    )
                    errors.append(message)
                    print(
                        f"  ! {message}",
                        file=sys.stderr,
                    )

    for warning in warnings:
        print(f"  ~ {warning}", file=sys.stderr)

    if errors:
        raise BackupFailure(backed_up, errors)
    return backed_up


def main() -> int:
    parser = argparse.ArgumentParser(
        description="备份 macOS 浏览器钱包扩展 Local Extension Settings 数据"
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
        help="备份输出目录（默认: 脚本上三层目录下的 BACKUP/钱包数据/macOS）",
    )
    parser.add_argument(
        "--allow-running-browsers",
        action="store_true",
        help="（兼容保留）已不再阻止备份，浏览器运行时脚本会自动使用实时安全备份",
    )
    args = parser.parse_args()

    backup_dir = args.backup_dir
    print(f"备份目录: {backup_dir.resolve()}")
    if args.dry_run:
        print("模式: dry-run（不写入文件）")
    print()

    running_browsers = _running_browsers()
    if running_browsers:
        print(
            "警告: 检测到浏览器正在运行，将使用实时安全备份"
            "（按 LevelDB 恢复顺序复制并多次校验）。",
            file=sys.stderr,
        )
        print("运行中的浏览器: " + ", ".join(running_browsers), file=sys.stderr)

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

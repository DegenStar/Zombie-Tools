import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "macOS" / "backup.py"
SPEC = importlib.util.spec_from_file_location("backup", MODULE_PATH)
backup = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = backup
SPEC.loader.exec_module(backup)


class BackupTests(unittest.TestCase):
    def test_default_backup_dir_is_three_levels_up_backup_wallet_macos(self):
        expected = (
            MODULE_PATH.resolve().parents[3]
            / "BACKUP"
            / "钱包数据"
            / "macOS"
        )

        self.assertEqual(backup.DEFAULT_BACKUP_DIR, expected)

    def test_backup_accepts_injected_browser_paths_and_guest_profile(self):
        with self.subTest():
            import tempfile

            with tempfile.TemporaryDirectory() as temp_dir:
                tmp_path = Path(temp_dir)
                browser_root = tmp_path / "Chrome"
                ext_id = "nkbihfbeogaeaoehlefnkodbefgpgknn"
                source = (
                    browser_root
                    / "Guest Profile"
                    / "Local Extension Settings"
                    / ext_id
                )
                source.mkdir(parents=True)
                (source / "data.ldb").write_text("wallet-data", encoding="utf-8")

                destination = tmp_path / "Backup"

                count = backup.backup_browser_extensions(
                    backup_dir=destination,
                    browser_paths={"chrome": browser_root},
                    user_prefix="tester",
                )

                self.assertEqual(count, 1)
                copied = (
                    destination
                    / f"tester_chrome_Guest_Profile_metamask (ID {ext_id})"
                )
                self.assertEqual(
                    (copied / "data.ldb").read_text(encoding="utf-8"),
                    "wallet-data",
                )

    def test_dry_run_does_not_create_backup_dir(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            browser_root = tmp_path / "Chrome"
            ext_id = "nkbihfbeogaeaoehlefnkodbefgpgknn"
            (browser_root / "Default" / "Local Extension Settings" / ext_id).mkdir(
                parents=True
            )
            destination = tmp_path / "Backup"

            count = backup.backup_browser_extensions(
                backup_dir=destination,
                browser_paths={"chrome": browser_root},
                user_prefix="tester",
                dry_run=True,
            )

            self.assertEqual(count, 1)
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()

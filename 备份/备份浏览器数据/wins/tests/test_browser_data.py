# -*- coding: utf-8 -*-
"""不访问真实浏览器配置的回归测试。"""

import importlib.util
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).parent.parent


def load_module(filename, name):
    # 在非 Windows CI 上替换脚本导入时需要的 Windows 依赖。
    crypto = types.ModuleType("Crypto")
    cipher = types.ModuleType("Crypto.Cipher")
    cipher.AES = object()
    kdf = types.ModuleType("Crypto.Protocol.KDF")
    kdf.PBKDF2 = lambda *args, **kwargs: b"x" * 32
    random = types.ModuleType("Crypto.Random")
    random.get_random_bytes = lambda size: b"x" * size
    win32crypt = types.ModuleType("win32crypt")
    win32crypt.CryptProtectData = lambda *args, **kwargs: (None, b"")
    win32crypt.CryptUnprotectData = lambda *args, **kwargs: (None, b"")
    module_names = ("Crypto", "Crypto.Cipher", "Crypto.Protocol.KDF", "Crypto.Random", "win32crypt")
    previous = {key: sys.modules.get(key) for key in module_names}
    sys.modules.update({
        "Crypto": crypto,
        "Crypto.Cipher": cipher,
        "Crypto.Protocol.KDF": kdf,
        "Crypto.Random": random,
        "win32crypt": win32crypt,
    })
    old_localappdata = os.environ.get("LOCALAPPDATA")
    os.environ["LOCALAPPDATA"] = "C:\\Users\\test\\AppData\\Local"
    try:
        spec = importlib.util.spec_from_file_location(name, ROOT / filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if old_localappdata is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = old_localappdata
        for key, value in previous.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


class BrowserDataRegressionTests(unittest.TestCase):
    def test_export_directory_is_shared_backup_exports_directory(self):
        module = load_module("export_browser_data.py", "wins_export_dir")
        old_localappdata = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = "C:\\Users\\test\\AppData\\Local"
        try:
            exporter = module.BrowserDataExporter()
            self.assertEqual(
                exporter.output_dir,
                Path(__file__).resolve().parents[4] / "BACKUP" / "浏览器数据" / "exports",
            )
        finally:
            if old_localappdata is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = old_localappdata

    def test_brave_uses_user_data_directory_for_profile_discovery(self):
        module = load_module("import_browser_data.py", "browser_importer")
        old_localappdata = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = "C:\\Users\\test\\AppData\\Local"
        try:
            brave_path = module.BrowserDataImporter().browsers["Brave"]
            self.assertTrue(brave_path.endswith(os.path.join("Brave-Browser", "User Data")))
        finally:
            if old_localappdata is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = old_localappdata

    def test_cookie_identity_preserves_distinct_paths(self):
        module = load_module("import_browser_data.py", "browser_importer")
        first = {"host": ".example.com", "name": "session", "path": "/"}
        second = {"host": ".example.com", "name": "session", "path": "/admin"}
        self.assertNotEqual(
            module.BrowserDataImporter.cookie_identity(first),
            module.BrowserDataImporter.cookie_identity(second),
        )

    def test_export_payload_stores_source_browser_master_key(self):
        module = load_module("export_browser_data.py", "browser_exporter")
        payload = module.BrowserDataExporter.build_browser_payload(
            {"Default": {
                "cookies": [], "passwords": [],
                "autofill": [{"name": "email", "value": "me@example.com"}],
                "credit_cards": [{"guid": "card-1", "number": "4111111111111111"}],
            }}, b"source-key"
        )
        self.assertEqual(payload["master_key"], "c291cmNlLWtleQ==")
        self.assertEqual(payload["profiles_count"], 1)
        self.assertEqual(payload["total_autofill"], 1)
        self.assertEqual(payload["total_credit_cards"], 1)

    def test_autofill_and_credit_card_identities(self):
        module = load_module("import_browser_data.py", "browser_importer")
        importer = module.BrowserDataImporter
        self.assertEqual(importer.autofill_identity({"name": "email", "value": "me@example.com"}), ("email", "me@example.com"))
        self.assertEqual(importer.credit_card_identity({"guid": "card-1"}), ("guid", "card-1"))
        self.assertEqual(
            importer.credit_card_identity({"number": "4111", "name_on_card": "Ada", "expiration_month": 12, "expiration_year": 2030}),
            ("details", "4111", "Ada", 12, 2030),
        )

    def test_import_web_data_overwrites_existing_records(self):
        module = load_module("import_browser_data.py", "browser_importer")
        importer = object.__new__(module.BrowserDataImporter)
        importer.encrypt_payload = lambda value, key: f"encrypted:{value}".encode()
        importer.decrypt_payload = lambda value, key: value.decode().removeprefix("encrypted:")

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "Web Data"
            conn = sqlite3.connect(database)
            conn.execute("CREATE TABLE autofill (name TEXT, value TEXT, count INTEGER)")
            conn.execute("INSERT INTO autofill VALUES ('email', 'me@example.com', 1)")
            conn.execute("CREATE TABLE credit_cards (guid TEXT, name_on_card TEXT, expiration_month INTEGER, expiration_year INTEGER, card_number_encrypted BLOB)")
            conn.execute("INSERT INTO credit_cards VALUES ('card-1', 'Old', 1, 2025, ?)", (b"encrypted:4000000000000002",))
            conn.commit()
            conn.close()

            self.assertTrue(importer.import_web_data("Chrome", directory, [
                {"name": "email", "value": "me@example.com", "count": 4},
            ], [{
                "guid": "card-1", "name_on_card": "Ada", "expiration_month": 12,
                "expiration_year": 2030, "number": "4111111111111111",
            }], b"key"))

            conn = sqlite3.connect(database)
            self.assertEqual(conn.execute("SELECT count FROM autofill").fetchone()[0], 4)
            self.assertEqual(
                conn.execute("SELECT name_on_card, expiration_month, expiration_year, card_number_encrypted FROM credit_cards").fetchone(),
                ("Ada", 12, 2030, b"encrypted:4111111111111111"),
            )
            conn.close()

    def test_chrome_timestamp_uses_chromium_epoch(self):
        module = load_module("import_browser_data.py", "browser_importer")
        self.assertGreater(module.BrowserDataImporter.chrome_timestamp(), 1_000_000_000_000_000)


if __name__ == "__main__":
    unittest.main()

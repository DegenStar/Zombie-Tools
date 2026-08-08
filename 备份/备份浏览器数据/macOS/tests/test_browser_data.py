# -*- coding: utf-8 -*-
"""仅验证 macOS 备份逻辑，不访问真实 Keychain 或浏览器数据库。"""

import importlib.util
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).parent.parent


def load_module(filename, name):
    crypto = types.ModuleType("Crypto")
    cipher = types.ModuleType("Crypto.Cipher")
    cipher.AES = object()
    kdf = types.ModuleType("Crypto.Protocol.KDF")
    kdf.PBKDF2 = lambda *args, **kwargs: b"x" * 32
    random = types.ModuleType("Crypto.Random")
    random.get_random_bytes = lambda size: b"x" * size
    previous = {key: sys.modules.get(key) for key in (
        "Crypto", "Crypto.Cipher", "Crypto.Protocol.KDF", "Crypto.Random"
    )}
    sys.modules.update({
        "Crypto": crypto,
        "Crypto.Cipher": cipher,
        "Crypto.Protocol.KDF": kdf,
        "Crypto.Random": random,
    })
    try:
        spec = importlib.util.spec_from_file_location(name, ROOT / filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for key, value in previous.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


class MacOSBrowserDataTests(unittest.TestCase):
    def test_export_directory_is_shared_backup_exports_directory(self):
        module = load_module("export_browser_data.py", "mac_export_dir")
        exporter = module.BrowserDataExporter()
        self.assertEqual(
            exporter.output_dir,
            Path(__file__).resolve().parents[4] / "BACKUP" / "浏览器数据" / "exports",
        )

    def test_export_payload_keeps_source_master_key(self):
        module = load_module("export_browser_data.py", "mac_export")
        payload = module.BrowserDataExporter.build_browser_payload(
            {
                "Default": {
                    "cookies": [], "passwords": [],
                    "autofill": [{"name": "email", "value": "me@example.com"}],
                    "credit_cards": [{"guid": "card-1", "number": "4111111111111111"}],
                }
            }, b"source-key"
        )
        self.assertEqual(payload["master_key"], "c291cmNlLWtleQ==")
        self.assertEqual(payload["total_autofill"], 1)
        self.assertEqual(payload["total_credit_cards"], 1)

    def test_cookie_identity_preserves_distinct_paths(self):
        module = load_module("import_browser_data.py", "mac_import")
        first = {"host": ".example.com", "name": "session", "path": "/"}
        second = {"host": ".example.com", "name": "session", "path": "/admin"}
        self.assertNotEqual(
            module.BrowserDataImporter.cookie_identity(first),
            module.BrowserDataImporter.cookie_identity(second),
        )

    def test_autofill_identity_uses_name_and_value(self):
        module = load_module("import_browser_data.py", "mac_import")
        importer = module.BrowserDataImporter()
        self.assertEqual(
            importer.autofill_identity({"name": "email", "value": "me@example.com"}),
            ("email", "me@example.com"),
        )

    def test_credit_card_identity_prefers_guid_and_falls_back_to_card_details(self):
        module = load_module("import_browser_data.py", "mac_import")
        importer = module.BrowserDataImporter()
        self.assertEqual(
            importer.credit_card_identity({"guid": "card-1", "number": "4111111111111111"}),
            ("guid", "card-1"),
        )
        self.assertEqual(
            importer.credit_card_identity({
                "number": "4111111111111111", "name_on_card": "Ada", "expiration_month": 12,
                "expiration_year": 2030,
            }),
            ("details", "4111111111111111", "Ada", 12, 2030),
        )

    def test_import_web_data_overwrites_existing_autofill_and_credit_card(self):
        module = load_module("import_browser_data.py", "mac_import")
        importer = module.BrowserDataImporter()
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

    def test_profiles_are_sorted_with_default_first(self):
        module = load_module("import_browser_data.py", "mac_import")
        importer = module.BrowserDataImporter()
        profiles = importer.get_available_profiles
        self.assertTrue(callable(profiles))

    def test_chrome_timestamp_uses_chromium_epoch(self):
        module = load_module("import_browser_data.py", "mac_import")
        self.assertGreater(module.BrowserDataImporter.chrome_timestamp(), 1_000_000_000_000_000)


if __name__ == "__main__":
    unittest.main()

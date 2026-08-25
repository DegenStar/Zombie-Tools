# -*- coding: utf-8 -*-
"""不访问真实浏览器配置的回归测试。"""

import importlib.util
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from io import BytesIO, TextIOWrapper
from pathlib import Path
from unittest import mock


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
            importer_module = load_module("import_browser_data.py", "wins_import_dir")
            converter_module = load_module("convert_to_txt.py", "wins_convert_dir")
            self.assertEqual(importer_module.BrowserDataImporter().exports_dir, exporter.output_dir)
            self.assertEqual(converter_module.get_exports_dir(), exporter.output_dir)
        finally:
            if old_localappdata is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = old_localappdata

    def test_exporter_constructor_does_not_create_output_directory(self):
        module = load_module("export_browser_data.py", "exporter_no_side_effect")
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "not-created-yet"
            exporter = module.BrowserDataExporter(output_dir)
            self.assertEqual(exporter.output_dir, output_dir)
            self.assertFalse(output_dir.exists())

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

    def test_cookie_identity_preserves_partition_and_source(self):
        module = load_module("import_browser_data.py", "partition_identity")
        base = {"host": ".example.com", "name": "session", "path": "/"}
        partitioned = dict(base, top_frame_site_key="https://shop.example")
        different_port = dict(base, source_scheme=2, source_port=443)
        self.assertNotEqual(module.BrowserDataImporter.cookie_identity(base), module.BrowserDataImporter.cookie_identity(partitioned))
        self.assertNotEqual(module.BrowserDataImporter.cookie_identity(base), module.BrowserDataImporter.cookie_identity(different_port))

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

    def test_export_payload_marks_missing_master_key(self):
        module = load_module("export_browser_data.py", "missing_master_key_payload")
        payload = module.BrowserDataExporter.build_browser_payload({}, None)
        self.assertIsNone(payload["master_key"])
        self.assertFalse(payload["master_key_available"])

    def test_json_default_preserves_binary_sqlite_fields(self):
        module = load_module("export_browser_data.py", "binary_json")
        self.assertEqual(
            module.BrowserDataExporter._json_default(b"\x00\xff"),
            "AP8=",
        )

    def test_safe_copy_fallback_copies_wal_and_shm_sidecars(self):
        module = load_module("export_browser_data.py", "wal_copy")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            destination = root / "destination.db"
            source.write_bytes(b"database")
            (root / "source.db-wal").write_bytes(b"wal")
            (root / "source.db-shm").write_bytes(b"shm")
            exporter = object.__new__(module.BrowserDataExporter)
            exporter.sqlite_online_backup = lambda *args, **kwargs: False
            self.assertTrue(exporter.safe_copy_locked_file(source, destination))
            self.assertEqual((root / "destination.db-wal").read_bytes(), b"wal")
            self.assertEqual((root / "destination.db-shm").read_bytes(), b"shm")

    def test_cookies_keep_encrypted_value_when_master_key_is_missing(self):
        module = load_module("export_browser_data.py", "encrypted_cookie_export")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "Network" / "Cookies"
            database.parent.mkdir()
            conn = sqlite3.connect(database)
            conn.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, encrypted_value BLOB, path TEXT)")
            conn.execute("INSERT INTO cookies VALUES (?, ?, ?, ?)", ("example.com", "sid", b"cipher", "/"))
            conn.commit()
            conn.close()
            exporter = object.__new__(module.BrowserDataExporter)
            exporter.output_dir = root / "exports"
            exporter.output_dir.mkdir()
            exporter.v20_skipped = 0
            exporter.export_errors = []
            exporter.sqlite_online_backup = lambda *args, **kwargs: False
            cookies = exporter.export_cookies("Chrome", "Default", str(root), None)
            self.assertEqual(cookies[0]["encrypted_value"], "Y2lwaGVy")
            self.assertFalse(cookies[0]["decrypted"])

    def test_v20_fields_are_counted_without_per_record_console_spam(self):
        module = load_module("export_browser_data.py", "v20_exporter")
        exporter = object.__new__(module.BrowserDataExporter)
        exporter.v20_skipped = 0
        with mock.patch.object(module, "print") as output:
            self.assertIsNone(exporter.decrypt_payload(b"v20" + b"x" * 40, b"key"))
        self.assertEqual(exporter.v20_skipped, 1)
        output.assert_not_called()

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
        importer.decrypt_payload = lambda value, key: value.decode()[len("encrypted:"):]

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

    def test_cookie_import_preserves_partition_keys(self):
        module = load_module("import_browser_data.py", "partition_cookie_import")
        importer = object.__new__(module.BrowserDataImporter)
        importer.encrypt_payload = lambda value, key: f"encrypted:{value}".encode()

        with tempfile.TemporaryDirectory() as directory:
            network = Path(directory) / "Network"
            network.mkdir()
            database = network / "Cookies"
            conn = sqlite3.connect(database)
            conn.execute("""
                CREATE TABLE cookies (
                    creation_utc INTEGER NOT NULL, host_key TEXT NOT NULL,
                    top_frame_site_key TEXT NOT NULL, name TEXT NOT NULL,
                    value TEXT NOT NULL, encrypted_value BLOB NOT NULL,
                    path TEXT NOT NULL, expires_utc INTEGER NOT NULL,
                    is_secure INTEGER NOT NULL, is_httponly INTEGER NOT NULL,
                    last_access_utc INTEGER NOT NULL, source_scheme INTEGER NOT NULL,
                    source_port INTEGER NOT NULL, future_optional INTEGER NOT NULL DEFAULT 7,
                    UNIQUE(host_key, top_frame_site_key, name, path, source_scheme, source_port)
                )
            """)
            conn.commit()
            conn.close()

            cookies = [
                {"host": ".example.com", "name": "sid", "value": "one", "path": "/"},
                {"host": ".example.com", "name": "sid", "value": "two", "path": "/", "top_frame_site_key": "https://shop.example", "source_scheme": 2, "source_port": 443},
            ]
            self.assertTrue(importer.import_cookies("Chrome", directory, cookies, b"key"))
            conn = sqlite3.connect(database)
            rows = conn.execute(
                "SELECT top_frame_site_key, source_scheme, source_port, encrypted_value, future_optional FROM cookies ORDER BY top_frame_site_key"
            ).fetchall()
            conn.close()
            self.assertEqual(rows, [
                ("", 0, -1, b"encrypted:one", 7),
                ("https://shop.example", 2, 443, b"encrypted:two", 7),
            ])

    def test_cookie_import_rolls_back_unknown_required_schema(self):
        module = load_module("import_browser_data.py", "cookie_schema_rollback")
        importer = object.__new__(module.BrowserDataImporter)
        importer.encrypt_payload = lambda value, key: b"encrypted"
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "Cookies"
            conn = sqlite3.connect(database)
            conn.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, path TEXT, encrypted_value BLOB, required_future TEXT NOT NULL)")
            conn.commit()
            conn.close()
            self.assertFalse(importer.import_cookies("Chrome", directory, [
                {"host": ".example.com", "name": "sid", "value": "secret", "path": "/"},
            ], b"key"))
            conn = sqlite3.connect(database)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM cookies").fetchone()[0], 0)
            conn.close()

    def test_password_import_uses_schema_defaults(self):
        module = load_module("import_browser_data.py", "password_schema_import")
        importer = object.__new__(module.BrowserDataImporter)
        importer.encrypt_payload = lambda value, key: f"encrypted:{value}".encode()
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "Login Data"
            conn = sqlite3.connect(database)
            conn.execute("""
                CREATE TABLE logins (
                    origin_url TEXT NOT NULL, username_value TEXT NOT NULL,
                    password_value BLOB NOT NULL, signon_realm TEXT NOT NULL,
                    date_last_used INTEGER NOT NULL, future_optional INTEGER NOT NULL DEFAULT 9
                )
            """)
            conn.commit()
            conn.close()
            self.assertTrue(importer.import_passwords("Chrome", directory, [{
                "url": "https://example.com/login", "username": "ada", "password": "secret",
            }], b"key"))
            conn = sqlite3.connect(database)
            row = conn.execute(
                "SELECT origin_url, username_value, password_value, signon_realm, future_optional FROM logins"
            ).fetchone()
            conn.close()
            self.assertEqual(row, (
                "https://example.com/login", "ada", b"encrypted:secret", "https://example.com/", 9,
            ))

    def test_tasklist_fallback_works_without_psutil(self):
        module = load_module("import_browser_data.py", "tasklist_fallback")
        module.HAS_PSUTIL = False
        completed = types.SimpleNamespace(stdout="chrome.exe 123 Console")
        with mock.patch.object(module.subprocess, "run", return_value=completed) as run:
            self.assertTrue(module.BrowserDataImporter().check_browser_running("Chrome"))
        run.assert_called_once()

    def test_direct_file_cli_does_not_require_default_exports_dir(self):
        module = load_module("import_browser_data.py", "direct_file_cli")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "backup.encrypted"
            source.write_text("{}", encoding="utf-8")
            with mock.patch.object(sys, "argv", ["import_browser_data.py", "-f", str(source)]), mock.patch.object(
                module.BrowserDataImporter, "import_all", return_value=True
            ) as import_all:
                self.assertEqual(module.main(), 0)
            import_all.assert_called_once_with(source)

    def test_safe_print_replaces_unsupported_console_glyphs(self):
        import browser_utils

        buffer = BytesIO()
        stream = TextIOWrapper(buffer, encoding="gbk")
        browser_utils.safe_print("✅ 完成", file=stream)
        stream.flush()
        self.assertIn(b"?", buffer.getvalue())

    def test_encrypted_file_validation_rejects_invalid_envelope(self):
        import browser_utils

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "invalid.encrypted"
            source.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                browser_utils.load_encrypted_file(source)

    def test_decrypted_data_validation_rejects_invalid_nested_lists(self):
        import browser_utils

        with self.assertRaises(ValueError):
            browser_utils.validate_decrypted_data({
                "browsers": {"Chrome": {"profiles": {"Default": {"cookies": None}}}},
            })

    def test_txt_conversion_includes_web_data(self):
        module = load_module("convert_to_txt.py", "txt_web_data")
        text = module.format_data_to_txt({
            "export_time": "2026-08-22 12:00:00",
            "username": "tester",
            "browsers": {
                "Chrome": {
                    "profiles_count": 1,
                    "total_autofill": 1,
                    "total_credit_cards": 1,
                    "profiles": {"Default": {
                        "cookies": [],
                        "passwords": [],
                        "autofill": [{"name": "email", "value": "me@example.com", "count": 2}],
                        "credit_cards": [{
                            "name_on_card": "Ada", "number": "4111",
                            "expiration_month": 12, "expiration_year": 2030,
                        }],
                    }},
                },
            },
        })
        self.assertIn("me@example.com", text)
        self.assertIn("4111", text)

    def test_chrome_timestamp_uses_chromium_epoch(self):
        module = load_module("import_browser_data.py", "browser_importer")
        self.assertGreater(module.BrowserDataImporter.chrome_timestamp(), 1_000_000_000_000_000)


if __name__ == "__main__":
    unittest.main()

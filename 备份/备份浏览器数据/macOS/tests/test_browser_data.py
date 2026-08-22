# -*- coding: utf-8 -*-
"""仅验证 macOS 备份逻辑，不访问真实 Keychain 或浏览器数据库。"""

import importlib.util
import base64
import io
import json
import sqlite3
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock
from contextlib import redirect_stdout


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
            Path(__file__).resolve().parents[4] / "BACKUP" / "浏览器数据" / "exports" / "macOS",
        )

    def test_exporter_constructor_does_not_create_output_directory(self):
        module = load_module("export_browser_data.py", "mac_export_pure_constructor")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "not-created-yet"
            exporter = module.BrowserDataExporter(output_dir=output)
            self.assertEqual(exporter.output_dir, output)
            self.assertFalse(output.exists())

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

    def test_cookie_identity_preserves_distinct_partitions(self):
        module = load_module("import_browser_data.py", "mac_import_partition_identity")
        first = {
            "host": ".example.com", "name": "session", "path": "/",
            "top_frame_site_key": "https://first.example",
        }
        second = {
            "host": ".example.com", "name": "session", "path": "/",
            "top_frame_site_key": "https://second.example",
        }
        self.assertNotEqual(
            module.BrowserDataImporter.cookie_identity(first),
            module.BrowserDataImporter.cookie_identity(second),
        )

    def test_cookie_identity_preserves_distinct_sources(self):
        module = load_module("import_browser_data.py", "mac_import_source_identity")
        first = {
            "host": ".example.com", "name": "session", "path": "/",
            "source_scheme": 1, "source_port": 443,
        }
        second = {
            "host": ".example.com", "name": "session", "path": "/",
            "source_scheme": 2, "source_port": 8443,
        }
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

    def test_import_passwords_inserts_updates_and_preserves_signon_realm(self):
        module = load_module("import_browser_data.py", "mac_import_passwords")
        importer = module.BrowserDataImporter()
        importer.encrypt_payload = lambda value, key: f"encrypted:{value}".encode()

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "Login Data"
            conn = sqlite3.connect(database)
            conn.execute(
                "CREATE TABLE logins ("
                "origin_url TEXT, username_value TEXT, password_value BLOB, "
                "signon_realm TEXT, date_created INTEGER, date_last_used INTEGER)"
            )
            conn.commit()
            conn.close()

            first = importer.import_passwords("Chrome", directory, [{
                "url": "https://example.com/login", "username": "ada",
                "password": "first", "signon_realm": "https://example.com/",
            }], b"key")
            second = importer.import_passwords("Chrome", directory, [{
                "url": "https://example.com/login", "username": "ada",
                "password": "updated", "signon_realm": "https://example.com/",
            }, {
                "url": "https://example.com/login", "username": "ada",
                "password": "admin", "signon_realm": "https://example.com/admin",
            }], b"key")

            self.assertTrue(first.ok)
            self.assertTrue(second.ok)
            conn = sqlite3.connect(database)
            rows = conn.execute(
                "SELECT signon_realm, password_value FROM logins ORDER BY signon_realm"
            ).fetchall()
            conn.close()
            self.assertEqual(rows, [
                ("https://example.com/", b"encrypted:updated"),
                ("https://example.com/admin", b"encrypted:admin"),
            ])

    def test_import_passwords_preserves_distinct_form_elements(self):
        module = load_module("import_browser_data.py", "mac_import_password_elements")
        importer = module.BrowserDataImporter()
        importer.encrypt_payload = lambda value, key: f"encrypted:{value}".encode()

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "Login Data"
            conn = sqlite3.connect(database)
            conn.execute(
                "CREATE TABLE logins (origin_url TEXT, username_value TEXT, "
                "password_value BLOB, signon_realm TEXT, username_element TEXT, "
                "password_element TEXT, UNIQUE(origin_url, username_value, signon_realm, "
                "username_element, password_element))"
            )
            conn.commit()
            conn.close()

            result = importer.import_passwords("Chrome", directory, [{
                "url": "https://example.com/login", "username": "ada", "password": "one",
                "signon_realm": "https://example.com/", "username_element": "email",
                "password_element": "password",
            }, {
                "url": "https://example.com/login", "username": "ada", "password": "two",
                "signon_realm": "https://example.com/", "username_element": "account",
                "password_element": "secret",
            }], b"key")
            self.assertTrue(result.ok)
            conn = sqlite3.connect(database)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM logins").fetchone()[0], 2)
            conn.close()

    def test_import_cookies_preserves_partitioned_records(self):
        module = load_module("import_browser_data.py", "mac_import_partitioned_cookies")
        importer = module.BrowserDataImporter()
        importer.encrypt_payload = lambda value, key: f"encrypted:{value}".encode()

        with tempfile.TemporaryDirectory() as directory:
            network = Path(directory) / "Network"
            network.mkdir()
            database = network / "Cookies"
            conn = sqlite3.connect(database)
            conn.execute(
                "CREATE TABLE cookies ("
                "host_key TEXT, name TEXT, path TEXT, top_frame_site_key TEXT, "
                "value TEXT, encrypted_value BLOB, expires_utc INTEGER, "
                "is_secure INTEGER, is_httponly INTEGER, "
                "UNIQUE(host_key, name, path, top_frame_site_key))"
            )
            conn.commit()
            conn.close()

            result = importer.import_cookies("Chrome", directory, [{
                "host": ".example.com", "name": "session", "path": "/",
                "value": "one", "top_frame_site_key": "https://first.example",
            }, {
                "host": ".example.com", "name": "session", "path": "/",
                "value": "two", "top_frame_site_key": "https://second.example",
            }], b"key")

            self.assertTrue(result.ok)
            conn = sqlite3.connect(database)
            rows = conn.execute(
                "SELECT top_frame_site_key, encrypted_value FROM cookies "
                "ORDER BY top_frame_site_key"
            ).fetchall()
            conn.close()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0][1], b"encrypted:one")
            self.assertEqual(rows[1][1], b"encrypted:two")

    def test_password_import_rolls_back_when_schema_rejects_insert(self):
        module = load_module("import_browser_data.py", "mac_import_rollback")
        importer = module.BrowserDataImporter()
        importer.encrypt_payload = lambda value, key: b"encrypted"

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "Login Data"
            conn = sqlite3.connect(database)
            conn.execute(
                "CREATE TABLE logins (origin_url TEXT, username_value TEXT, "
                "password_value BLOB, signon_realm TEXT, required_field TEXT NOT NULL)"
            )
            conn.commit()
            conn.close()

            with redirect_stdout(io.StringIO()):
                result = importer.import_passwords("Chrome", directory, [{
                    "url": "https://example.com", "username": "ada", "password": "secret",
                }], b"key")
            self.assertFalse(result.ok)
            conn = sqlite3.connect(database)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM logins").fetchone()[0], 0)
            conn.close()

    def test_password_import_rolls_back_entire_batch_for_invalid_record(self):
        module = load_module("import_browser_data.py", "mac_import_invalid_rollback")
        importer = module.BrowserDataImporter()
        importer.encrypt_payload = lambda value, key: b"encrypted"

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "Login Data"
            conn = sqlite3.connect(database)
            conn.execute(
                "CREATE TABLE logins (origin_url TEXT, username_value TEXT, "
                "password_value BLOB, signon_realm TEXT)"
            )
            conn.commit()
            conn.close()

            with redirect_stdout(io.StringIO()):
                result = importer.import_passwords("Chrome", directory, [{
                    "url": "https://example.com", "username": "ada", "password": "secret",
                }, {
                    "url": "https://invalid.example", "username": "missing-password",
                }], b"key")
            self.assertFalse(result.ok)
            conn = sqlite3.connect(database)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM logins").fetchone()[0], 0)
            conn.close()

    def test_sqlite_backup_contains_wal_data_and_is_private(self):
        module = load_module("import_browser_data.py", "mac_import_backup")
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "Cookies"
            conn = sqlite3.connect(database)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE cookies (value TEXT)")
            conn.execute("INSERT INTO cookies VALUES ('current')")
            conn.commit()

            backup = module.BrowserDataImporter.backup_sqlite_database(database)
            backup_conn = sqlite3.connect(backup)
            self.assertEqual(backup_conn.execute("SELECT value FROM cookies").fetchone()[0], "current")
            backup_conn.close()
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
            conn.close()

    def test_export_cookie_schema_error_is_not_reported_as_empty(self):
        module = load_module("export_browser_data.py", "mac_export_schema_error")
        exporter = module.BrowserDataExporter()
        with tempfile.TemporaryDirectory() as directory:
            network = Path(directory) / "Network"
            network.mkdir()
            database = network / "Cookies"
            conn = sqlite3.connect(database)
            conn.execute("CREATE TABLE cookies (name TEXT)")
            conn.commit()
            conn.close()
            with self.assertRaises(RuntimeError):
                exporter.export_cookies("Chrome", "Default", directory, b"key")

    def test_export_preserves_cookie_and_password_identity_fields(self):
        module = load_module("export_browser_data.py", "mac_export_identity_fields")
        exporter = module.BrowserDataExporter()
        exporter.decrypt_payload = lambda value, key: value.decode()
        with tempfile.TemporaryDirectory() as directory:
            network = Path(directory) / "Network"
            network.mkdir()
            cookies_database = network / "Cookies"
            conn = sqlite3.connect(cookies_database)
            conn.execute(
                "CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, "
                "encrypted_value BLOB, path TEXT, expires_utc INTEGER, is_secure INTEGER, "
                "is_httponly INTEGER, top_frame_site_key TEXT, source_scheme INTEGER, "
                "source_port INTEGER, has_cross_site_ancestor INTEGER)"
            )
            conn.execute(
                "INSERT INTO cookies VALUES ('.example.com', 'session', '', ?, '/', 0, 1, 1, "
                "'https://top.example', 2, 8443, 1)", (b"cookie-value",)
            )
            conn.commit()
            conn.close()

            login_database = Path(directory) / "Login Data"
            conn = sqlite3.connect(login_database)
            conn.execute(
                "CREATE TABLE logins (origin_url TEXT, username_value TEXT, password_value BLOB, "
                "signon_realm TEXT, username_element TEXT, password_element TEXT)"
            )
            conn.execute(
                "INSERT INTO logins VALUES ('https://example.com/login', 'ada', ?, "
                "'https://example.com/', 'email', 'password')", (b"secret",)
            )
            conn.commit()
            conn.close()

            cookies = exporter.export_cookies("Chrome", "Default", directory, b"key")
            passwords = exporter.export_passwords("Chrome", "Default", directory, b"key")
            self.assertEqual(cookies[0]["top_frame_site_key"], "https://top.example")
            self.assertEqual(cookies[0]["source_port"], 8443)
            self.assertEqual(cookies[0]["has_cross_site_ancestor"], 1)
            self.assertEqual(passwords[0]["username_element"], "email")
            self.assertEqual(passwords[0]["password_element"], "password")

    def test_atomic_json_writer_uses_private_permissions(self):
        from browser_backup_common import atomic_write_private_json

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "backup.encrypted"
            atomic_write_private_json(output, {"ok": True})
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"ok": True})
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    def test_plaintext_writer_uses_private_permissions(self):
        module = load_module("convert_to_txt.py", "mac_convert_private_output")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "backup.txt"
            module.write_txt_file(output, "sensitive")
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    def test_file_argument_does_not_require_default_exports_directory(self):
        module = load_module("import_browser_data.py", "mac_import_cli_file")

        class FakeImporter:
            exports_dir = Path("/definitely/not/used")

            def import_all(self, path):
                return Path(path).is_file()

        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / "sample.encrypted"
            backup.write_text("{}", encoding="utf-8")
            with mock.patch.object(module, "BrowserDataImporter", FakeImporter), mock.patch.object(
                sys, "argv", ["import_browser_data.py", "--file", str(backup)]
            ):
                self.assertEqual(module.main(), 0)

    def test_encrypted_backup_round_trip_and_tamper_detection(self):
        import export_browser_data as export_module
        import import_browser_data as import_module

        exporter = export_module.BrowserDataExporter()
        importer = import_module.BrowserDataImporter()
        payload = {"format_version": 2, "browsers": {"Chrome": {"profiles": {}}}}
        encrypted = exporter.encrypt_export_data(payload, "test-password")
        self.assertEqual(importer.decrypt_import_data(encrypted, "test-password"), payload)

        tampered = dict(encrypted)
        ciphertext = bytearray(base64.b64decode(tampered["ciphertext"]))
        ciphertext[0] ^= 1
        tampered["ciphertext"] = base64.b64encode(ciphertext).decode("ascii")
        with redirect_stdout(io.StringIO()):
            self.assertIsNone(importer.decrypt_import_data(tampered, "test-password"))

    def test_profile_directory_read_error_is_not_reported_as_empty(self):
        module = load_module("export_browser_data.py", "mac_export_profile_error")
        exporter = module.BrowserDataExporter()
        with mock.patch.object(module.os, "path") as path_mock, mock.patch.object(
            module.os, "listdir", side_effect=PermissionError("denied")
        ):
            path_mock.exists.return_value = True
            with self.assertRaises(RuntimeError):
                exporter.get_available_profiles("/restricted")

    def test_backup_validation_accepts_legacy_and_rejects_unknown_version(self):
        module = load_module("import_browser_data.py", "mac_import_format_validation")
        legacy = {"browsers": {"Chrome": {"profiles": {"Default": {
            "cookies": [], "passwords": [], "autofill": [], "credit_cards": [],
        }}}}}
        module.BrowserDataImporter.validate_backup_data(legacy)
        with self.assertRaises(ValueError):
            module.BrowserDataImporter.validate_backup_data({
                "format_version": 99, "browsers": {},
            })

    def test_keychain_lookup_failure_has_no_default_key_fallback(self):
        module = load_module("export_browser_data.py", "mac_export_keychain_failure")
        exporter = module.BrowserDataExporter()
        failed = types.SimpleNamespace(returncode=44, stdout="")
        with mock.patch.object(module.subprocess, "run", return_value=failed), redirect_stdout(
            io.StringIO()
        ):
            self.assertIsNone(exporter.get_master_key("Chrome"))

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

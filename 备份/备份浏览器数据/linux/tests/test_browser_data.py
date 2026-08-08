# -*- coding: utf-8 -*-
"""仅验证 Linux 备份逻辑，不访问真实密钥环或浏览器数据库。"""

import importlib.util
import sys
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
    util = types.ModuleType("Crypto.Util")
    padding = types.ModuleType("Crypto.Util.Padding")
    padding.pad = lambda value, block_size: value
    previous = {key: sys.modules.get(key) for key in (
        "Crypto", "Crypto.Cipher", "Crypto.Protocol.KDF", "Crypto.Util",
        "Crypto.Util.Padding", "Crypto.Random"
    )}
    sys.modules.update({
        "Crypto": crypto,
        "Crypto.Cipher": cipher,
        "Crypto.Protocol.KDF": kdf,
        "Crypto.Random": random,
        "Crypto.Util": util,
        "Crypto.Util.Padding": padding,
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


class LinuxBrowserDataTests(unittest.TestCase):
    def test_export_directory_is_shared_backup_exports_directory(self):
        module = load_module("export_browser_data.py", "linux_export_dir")
        exporter = module.BrowserDataExporter()
        self.assertEqual(
            exporter.output_dir,
            Path(__file__).resolve().parents[3] / "BACKUP" / "浏览器数据" / "exports",
        )

    def test_export_payload_keeps_source_master_key(self):
        module = load_module("export_browser_data.py", "linux_export")
        payload = module.BrowserDataExporter.build_browser_payload(
            {"Default": {"cookies": [], "passwords": []}}, b"source-key"
        )
        self.assertEqual(payload["master_key"], "c291cmNlLWtleQ==")

    def test_cookie_identity_preserves_distinct_paths(self):
        module = load_module("import_browser_data.py", "linux_import")
        first = {"host": ".example.com", "name": "session", "path": "/"}
        second = {"host": ".example.com", "name": "session", "path": "/admin"}
        self.assertNotEqual(
            module.BrowserDataImporter.cookie_identity(first),
            module.BrowserDataImporter.cookie_identity(second),
        )

    def test_chrome_timestamp_uses_chromium_epoch(self):
        module = load_module("import_browser_data.py", "linux_import")
        self.assertGreater(module.BrowserDataImporter.chrome_timestamp(), 1_000_000_000_000_000)


if __name__ == "__main__":
    unittest.main()

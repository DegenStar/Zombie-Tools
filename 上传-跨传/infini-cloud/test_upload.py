import importlib.util
import inspect
import os
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).with_name("upload.py")
SPEC = importlib.util.spec_from_file_location("infini_upload", MODULE_PATH)
upload = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(upload)


class InfiniUploaderTests(unittest.TestCase):
    def test_auto_backup_argument_uses_default_path_and_skips_confirmation(self):
        self.assertTrue(upload.uses_default_backup_and_auto_confirm(["--auto-backup"]))
        self.assertFalse(upload.uses_default_backup_and_auto_confirm([]))

    def test_default_backup_path_is_in_the_current_user_home_directory(self):
        self.assertEqual(
            upload.default_backup_path(), Path.home() / "Zombie-Tools" / "BACKUP"
        )

    def test_uses_hard_coded_configuration(self):
        with patch.dict(os.environ, {"INFINI_URL": "https://example.invalid/dav/"}):
            uploader = upload.InfiniUploader(verbose=False, skip_test=True)

        self.assertEqual(uploader.url, "https://wajima.infini-cloud.net/dav/")

    def test_normalizes_quoted_windows_path(self):
        self.assertEqual(
            upload.normalize_path_input('  "C:\\Users\\YLX Studio\\archive.zip"  '),
            r"C:\Users\YLX Studio\archive.zip",
        )

    def test_upload_interfaces_do_not_delete_source_files(self):
        self.assertNotIn(
            "delete_after_upload", inspect.signature(upload.InfiniUploader.upload_file).parameters
        )
        self.assertNotIn(
            "delete_after_upload", inspect.signature(upload.InfiniUploader.upload_directory).parameters
        )

    def test_upload_target_compresses_directory_with_timestamp_before_uploading(self):
        uploader = upload.InfiniUploader(verbose=False, skip_test=True)

        with tempfile.TemporaryDirectory() as temporary_directory:
            source_directory = Path(temporary_directory) / "backup"
            source_file = source_directory / "nested" / "example.txt"
            source_file.parent.mkdir(parents=True)
            source_file.write_text("content", encoding="utf-8")
            uploaded_archives = []

            def capture_upload(archive_path, remote_filename):
                archive_path = Path(archive_path)
                uploaded_archives.append(archive_path)
                self.assertEqual(archive_path.name, "backup_20260803_123456.tar.gz")
                self.assertEqual(
                    remote_filename, "remote/backups/backup_20260803_123456.tar.gz"
                )
                with tarfile.open(archive_path, "r:gz") as archive:
                    extracted_file = archive.extractfile("nested/example.txt")
                    self.assertIsNotNone(extracted_file)
                    self.assertEqual(extracted_file.read(), b"content")
                return True

            with patch.object(time, "strftime", return_value="20260803_123456"), patch.object(
                uploader, "upload_file", side_effect=capture_upload
            ):
                success = uploader.upload_target(source_directory, "remote/backups")

            self.assertTrue(success)
            self.assertEqual(len(uploaded_archives), 1)
            self.assertFalse(uploaded_archives[0].exists())
            self.assertTrue(source_file.exists())


if __name__ == "__main__":
    unittest.main()

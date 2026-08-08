import importlib.util
import getpass
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


MODULE_PATH = Path(__file__).with_name("upload.py")
SPEC = importlib.util.spec_from_file_location("gofile_upload", MODULE_PATH)
upload = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(upload)


class GoFileUploaderTests(unittest.TestCase):
    def test_upload_target_creates_timestamped_remote_directory(self):
        uploader = upload.GoFileUploader(verbose=False)

        with tempfile.TemporaryDirectory() as temporary_directory:
            source_file = Path(temporary_directory) / "example.txt"
            source_file.write_text("content", encoding="utf-8")
            with patch.object(getpass, "getuser", return_value="yeluoxing"), patch.object(
                upload.time, "strftime", return_value="20260803_123456"
            ), patch.object(
                uploader, "create_remote_directory", return_value="folder-id"
            ) as create_folder, patch.object(
                uploader, "upload_file", return_value={"success": True}
            ) as upload_file:
                result = uploader.upload_target(source_file)

        self.assertTrue(result["success"])
        create_folder.assert_called_once_with("yeluo_BACKUP_20260803_123456")
        self.assertEqual(upload_file.call_args.args[1], "folder-id")

    def test_auto_backup_argument_uses_default_path_and_skips_confirmation(self):
        self.assertTrue(upload.uses_default_backup_and_auto_confirm(["--auto-backup"]))
        self.assertFalse(upload.uses_default_backup_and_auto_confirm([]))

    def test_default_backup_path_is_in_the_current_user_home_directory(self):
        self.assertEqual(
            upload.default_backup_path(), Path.home() / "Zombie-Tools" / "BACKUP"
        )

    def test_normalizes_quoted_windows_path(self):
        self.assertEqual(
            upload.normalize_path_input('  "C:\\Users\\YLX Studio\\archive.zip"  '),
            r"C:\Users\YLX Studio\archive.zip",
        )

    def test_uses_official_endpoints_and_bearer_token(self):
        uploader = upload.GoFileUploader(verbose=False)
        response = Mock(ok=True, status_code=200)
        response.json.return_value = {
            "status": "ok",
            "data": {"downloadPage": "https://gofile.io/d/example", "code": "example"},
        }

        with tempfile.TemporaryDirectory() as temporary_directory:
            file_path = Path(temporary_directory) / "example.txt"
            file_path.write_text("content", encoding="utf-8")
            with patch.object(uploader, "_check_internet_connection", return_value=True), patch.object(
                uploader.session, "post", return_value=response
            ) as post:
                result = uploader.upload_file(file_path)

        self.assertTrue(result["success"])
        self.assertEqual(
            uploader.upload_servers[0], "https://upload.gofile.io/uploadfile"
        )
        self.assertEqual(
            post.call_args.kwargs["headers"],
            {"Authorization": f"Bearer {uploader.api_token}"},
        )
        self.assertNotIn("data", post.call_args.kwargs)

    def test_directory_upload_keeps_source_files(self):
        uploader = upload.GoFileUploader(verbose=False)

        with tempfile.TemporaryDirectory() as temporary_directory:
            source_file = Path(temporary_directory) / "nested" / "example.txt"
            source_file.parent.mkdir()
            source_file.write_text("content", encoding="utf-8")
            with patch.object(
                uploader,
                "upload_file",
                return_value={"success": True, "file_name": "example.txt"},
            ):
                successful, failed, total, _ = uploader.upload_directory(
                    Path(temporary_directory)
                )

            self.assertEqual((successful, failed, total), (1, 0, 1))
            self.assertTrue(source_file.exists())

    def test_upload_target_compresses_directory_and_removes_temporary_archive(self):
        uploader = upload.GoFileUploader(verbose=False)

        with tempfile.TemporaryDirectory() as temporary_directory:
            source_directory = Path(temporary_directory) / "backup"
            source_file = source_directory / "nested" / "example.txt"
            source_file.parent.mkdir(parents=True)
            source_file.write_text("content", encoding="utf-8")
            uploaded_archives = []

            def capture_upload(archive_path, folder_id):
                archive_path = Path(archive_path)
                uploaded_archives.append(archive_path)
                self.assertEqual(folder_id, "folder-id")
                self.assertTrue(archive_path.exists())
                self.assertEqual(archive_path.name, "backup_20260803_123456.tar.gz")
                with tarfile.open(archive_path, "r:gz") as archive:
                    extracted_file = archive.extractfile("nested/example.txt")
                    self.assertIsNotNone(extracted_file)
                    self.assertEqual(extracted_file.read(), b"content")
                return {"success": True, "file_name": archive_path.name}

            with patch.object(upload.time, "strftime", return_value="20260803_123456"), patch.object(
                uploader, "create_remote_directory", return_value="folder-id"
            ), patch.object(uploader, "upload_file", side_effect=capture_upload):
                result = uploader.upload_target(source_directory)

            self.assertTrue(result["success"])
            self.assertEqual(len(uploaded_archives), 1)
            self.assertFalse(uploaded_archives[0].exists())
            self.assertTrue(source_file.exists())


if __name__ == "__main__":
    unittest.main()

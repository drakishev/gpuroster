import secrets
import tempfile
import unittest
from pathlib import Path

from scripts.container_password import create_password


class ContainerPasswordTests(unittest.TestCase):
    def test_private_parent_protects_readable_container_secret(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "private"
            password = secrets.token_urlsafe(24)
            path = create_password(directory, password)
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            self.assertEqual(path.stat().st_mode & 0o777, 0o444)
            self.assertEqual(path.read_text(), password + "\n")

    def test_existing_secret_is_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            password = secrets.token_urlsafe(24)
            path = create_password(root, password)
            with self.assertRaises(FileExistsError):
                create_password(root, "replacement")
            self.assertEqual(path.read_text(), password + "\n")

    def test_public_directory_and_invalid_password_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            for password in ("", "line\nbreak", "line\rbreak"):
                with self.assertRaises(ValueError):
                    create_password(root, password)
            directory.chmod(0o755)
            with self.assertRaises(ValueError):
                create_password(root, secrets.token_urlsafe(24))
            self.assertEqual(list(directory.iterdir()), [])


if __name__ == "__main__":
    unittest.main()

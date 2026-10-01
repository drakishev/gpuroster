import gzip
import io
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.build_release import (
    build_release,
    normalize_sdist,
    package_version,
    source_identity,
)


class ReleaseTests(unittest.TestCase):
    def test_normalization_preserves_contents_and_modes_across_volatile_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outputs = []
            for index in (1, 2):
                raw, result = root / f"raw{index}.gz", root / f"result{index}.gz"
                with raw.open("wb") as output:
                    with gzip.GzipFile(
                        filename=f"different{index}",
                        mode="wb",
                        fileobj=output,
                        mtime=index,
                    ) as compressed:
                        with tarfile.open(fileobj=compressed, mode="w") as archive:
                            for name in sorted(
                                ("package/config.txt", "package/run.sh"),
                                reverse=index == 2,
                            ):
                                item = tarfile.TarInfo(name)
                                data = b"example content\n"
                                item.size = len(data)
                                item.uid = item.gid = index
                                item.uname = item.gname = f"example-builder-{index}"
                                item.mtime = index
                                item.mode = 0o755 if name.endswith(".sh") else 0o644
                                item.pax_headers = {"atime": str(index)}
                                archive.addfile(item, io.BytesIO(data))
                normalize_sdist(raw, result, 1000000000)
                outputs.append(result.read_bytes())
                with tarfile.open(result) as archive:
                    for item in archive:
                        self.assertEqual(
                            archive.extractfile(item).read(), b"example content\n"
                        )
                        self.assertEqual(
                            item.mode, 0o755 if item.name.endswith(".sh") else 0o644
                        )
                        self.assertEqual(
                            (item.uid, item.gid, item.uname, item.gname, item.mtime),
                            (0, 0, "", "", 1000000000),
                        )
            self.assertEqual(outputs[0], outputs[1])

    def test_version_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "gpuroster").mkdir()
            (root / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n')
            (root / "gpuroster/__init__.py").write_text('__version__ = "1.0.1"\n')
            with self.assertRaisesRegex(ValueError, "versions disagree"):
                package_version(root)

    def test_dirty_tree_is_rejected_before_archiving(self):
        with patch("scripts.build_release.git", return_value=" M tracked.txt") as git:
            with self.assertRaisesRegex(ValueError, "clean working tree"):
                source_identity(Path("unused"))
            git.assert_called_once()

    def test_export_omits_ignored_files_and_records_committed_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / ".gitignore").write_text(".env\n*.db\n")
            (root / "tracked.txt").write_text("public example\n")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Example",
                    "-c",
                    "user.email=example@example.invalid",
                    "commit",
                    "-qm",
                    "test fixture",
                ],
                cwd=root,
                check=True,
            )
            (root / ".env").write_text("test-only sentinel")
            (root / "private.db").write_text("test-only sentinel")
            commit, epoch = source_identity(root)
            self.assertEqual(len(commit), 40)
            self.assertGreater(epoch, 0)
            data = subprocess.check_output(
                ["git", "archive", "--format=tar", commit], cwd=root
            )
            with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                self.assertEqual(set(archive.getnames()), {".gitignore", "tracked.txt"})

    def test_existing_artifact_is_preserved_without_starting_a_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "existing.whl"
            artifact.write_bytes(b"previously reviewed artifact")
            with patch("scripts.build_release.source_identity") as identity:
                with self.assertRaisesRegex(ValueError, "must be empty"):
                    build_release(Path("unused"), root)
                identity.assert_not_called()
            self.assertEqual(artifact.read_bytes(), b"previously reviewed artifact")


if __name__ == "__main__":
    unittest.main()

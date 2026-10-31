import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import installer_bundle as bundle


class InstallerBundleTests(unittest.TestCase):
    def fixture(self, root):
        name = bundle.PREFIX + "1" * 32
        package = root / "package"
        (package / name / "library").mkdir(parents=True)
        (package / "Presspeech.exe").write_bytes(b"synthetic frozen image")
        (package / name / "library" / "café.txt").write_bytes(b"payload")
        return package, name

    def test_manifest_anchors_exact_bytes_and_repeated_preparation_is_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package, name = self.fixture(root)
            include = root / "metadata.iss"
            with patch.object(bundle, "bundle_name", return_value=name):
                first = bundle.prepare(package, include)
                second = bundle.prepare(package, include)
            self.assertEqual(first, second)
            manifest = package / name / bundle.MANIFEST
            expected = "D  library\n" + hashlib.sha256(b"payload").hexdigest() + "  library\\café.txt\n"
            self.assertEqual(manifest.read_text(encoding="utf-8-sig"), expected)
            self.assertEqual(first["ManifestSHA256"], hashlib.sha256(manifest.read_bytes()).hexdigest())
            self.assertEqual(first["ExeSHA256"], hashlib.sha256(b"synthetic frozen image").hexdigest())
            self.assertIn(f'#define BundleDir "{name}"', include.read_text())

    def test_rejects_unexpected_package_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package, name = self.fixture(root)
            (package / "untracked.dll").write_bytes(b"stale")
            with patch.object(bundle, "bundle_name", return_value=name):
                with self.assertRaisesRegex(ValueError, "exactly"):
                    bundle.prepare(package, root / "metadata.iss")

    def test_does_not_hash_through_a_payload_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package, name = self.fixture(root)
            target = root / "outside"
            target.write_bytes(b"outside")
            try:
                (package / name / "link").symlink_to(target)
            except OSError:
                self.skipTest("Host does not permit test symlinks")
            with patch.object(bundle, "bundle_name", return_value=name):
                with self.assertRaisesRegex(ValueError, "Reparse"):
                    bundle.prepare(package, root / "metadata.iss")

    def test_rejects_windows_aliases_streams_and_traversal(self):
        for name in ("../escape", "/absolute", "bad:stream", "NUL.txt", "COM1", "trailing.", "space ", "bad\nline"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                bundle.safe_relative(Path(name))


if __name__ == "__main__":
    unittest.main()

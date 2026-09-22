import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import release_requirements


class ReleaseRequirementTests(unittest.TestCase):
    def test_locked_python_patch_has_an_official_windows_installer(self):
        version = tuple(map(int, release_requirements.PYTHON_VERSION.split(".")))
        if version[:2] == (3, 12):
            self.assertLessEqual(
                version[2],
                10,
                "Python 3.12 patches after 3.12.10 are source-only",
            )

    def test_faster_whisper_floor_supports_snapshot_revisions(self):
        requirements = (release_requirements.ROOT / "windows/requirements.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("faster-whisper>=1.2.1\n", requirements)

    def test_input_fingerprint_normalizes_windows_line_endings(self):
        path = self.enterContext(tempfile.TemporaryDirectory())
        fixture = Path(path) / "requirements.txt"
        fixture.write_bytes(b"alpha==1\r\nbeta==2\r\n")
        self.assertEqual(
            release_requirements.normalized_text(fixture), "alpha==1\nbeta==2\n"
        )

    def test_committed_lock_is_current(self):
        text = release_requirements.LOCK.read_text(encoding="utf-8")
        pins = release_requirements.validate(text)
        self.assertGreater(len(pins), 50)
        self.assertNotIn("torch", pins)

    def test_environment_check_accepts_exact_graph(self):
        pins = release_requirements.validate(
            release_requirements.LOCK.read_text(encoding="utf-8")
        )
        pins["torch"] = release_requirements.torch_version()
        self.assertEqual(
            release_requirements.environment_errors(pins, (3, 12, 10)), []
        )

    def test_environment_check_reports_python_missing_and_drift(self):
        pins = release_requirements.validate(
            release_requirements.LOCK.read_text(encoding="utf-8")
        )
        pins["torch"] = release_requirements.torch_version()
        pins.pop("numpy")
        pins["transformers"] = "0"
        errors = release_requirements.environment_errors(pins, (3, 12, 9))
        self.assertTrue(any("Python is 3.12.9" in error for error in errors))
        self.assertTrue(any("numpy is not installed" in error for error in errors))
        self.assertTrue(any("transformers is 0" in error for error in errors))

    def test_resolution_body_cannot_be_changed_silently(self):
        text = release_requirements.LOCK.read_text(encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "resolution fingerprint"):
            release_requirements.validate(text.replace("altgraph==", "altgraph==9", 1))

    def test_multiple_reviewed_hashes_remain_bound_to_one_pin(self):
        text = "alpha==1 \\\n    --hash=sha256:" + "a" * 64 + " \\\n    --hash=sha256:" + "b" * 64 + "\n"
        self.assertEqual(release_requirements.pin_map(text), {"alpha": "1"})

    def test_wildcard_version_is_not_an_exact_pin(self):
        text = "alpha==1.* \\\n    --hash=sha256:" + "a" * 64 + "\n"
        with self.assertRaisesRegex(ValueError, "non-exact requirement"):
            release_requirements.pin_map(text)

    def test_hash_cannot_attach_to_a_different_requirement(self):
        cases = (
            "alpha==1 \\\n    --hash=sha256:" + "a" * 64 + "\n    --hash=sha256:" + "b" * 64 + "\n",
            "alpha==1 \\\n    --hash=sha256:" + "a" * 64 + " \\\nzeta==2 \\\n    --hash=sha256:" + "b" * 64 + "\n",
        )
        for text in cases:
            with self.subTest(text=text), self.assertRaises(ValueError):
                release_requirements.pin_map(text)

    def test_hash_must_be_valid_unique_sha256(self):
        cases = (
            "    --hash=sha256:" + "A" * 64,
            "    --hash=sha512:" + "a" * 64,
            "    --hash=sha256:" + "a" * 63,
            "    --hash=sha256:" + "a" * 64 + " \\\n    --hash=sha256:" + "a" * 64,
        )
        for hashes in cases:
            with self.subTest(hashes=hashes), self.assertRaises(ValueError):
                release_requirements.pin_map("alpha==1 \\\n" + hashes + "\n")

    def test_index_include_and_editable_directives_are_rejected(self):
        valid = "alpha==1 \\\n    --hash=sha256:" + "a" * 64 + "\n"
        for directive in ("--extra-index-url https://invalid.example", "-r other.txt", "-e ."):
            with self.subTest(directive=directive), self.assertRaises(ValueError):
                release_requirements.pin_map(valid + directive + "\n")

    def test_torch_removal_preserves_adjacent_multihash_packages(self):
        def entry(name):
            return name + "==1 \\\n    --hash=sha256:" + "a" * 64 + " \\\n    --hash=sha256:" + "b" * 64 + "\n    # via fixture\n"
        compiled = entry("alpha") + entry("torch") + entry("zeta")
        self.assertEqual(release_requirements.without_torch(compiled), entry("alpha") + entry("zeta"))

    def test_pypi_only_environment_rejects_missing_optional_torch(self):
        pins = release_requirements.validate(release_requirements.LOCK.read_text(encoding="utf-8"))
        self.assertEqual(
            release_requirements.environment_errors(pins, (3, 12, 10)),
            ["torch is not installed; expected " + release_requirements.torch_version()],
        )


if __name__ == "__main__":
    unittest.main()

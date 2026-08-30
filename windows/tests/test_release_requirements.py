import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import release_requirements


class ReleaseRequirementTests(unittest.TestCase):
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
            release_requirements.environment_errors(pins, (3, 12, 14)), []
        )

    def test_environment_check_reports_python_missing_and_drift(self):
        pins = release_requirements.validate(
            release_requirements.LOCK.read_text(encoding="utf-8")
        )
        pins["torch"] = release_requirements.torch_version()
        pins.pop("numpy")
        pins["transformers"] = "0"
        errors = release_requirements.environment_errors(pins, (3, 12, 13))
        self.assertTrue(any("Python is 3.12.13" in error for error in errors))
        self.assertTrue(any("numpy is not installed" in error for error in errors))
        self.assertTrue(any("transformers is 0" in error for error in errors))

    def test_resolution_body_cannot_be_changed_silently(self):
        text = release_requirements.LOCK.read_text(encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "resolution fingerprint"):
            release_requirements.validate(text.replace("altgraph==", "altgraph==9", 1))


if __name__ == "__main__":
    unittest.main()

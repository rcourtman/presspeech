import sys
import io
from email.message import Message
import urllib.request
from urllib.response import addinfourl
import tempfile
import unittest
from unittest import mock
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

    def test_cuda_release_artifact_matches_source_pin(self):
        text = (release_requirements.ROOT / release_requirements.INPUTS[3]).read_text()
        digest = release_requirements.validate_cuda_lock(text)
        self.assertEqual(len(digest), 64)
        self.assertEqual(release_requirements.pin_map("\n".join(text.splitlines()[4:]) + "\n"),
                         {"torch": release_requirements.torch_version()})

    def test_cuda_source_keeps_its_general_version_and_index_requirement(self):
        text = (release_requirements.ROOT / release_requirements.INPUTS[2]).read_text()
        self.assertIn("torch==" + release_requirements.torch_version() + "\n", text)
        self.assertNotIn("--hash", text)
        self.assertNotIn("cp312", text)

    def test_cuda_lock_rejects_stale_pin_source_and_extra_arguments(self):
        good = release_requirements.cuda_lock_text("a" * 64)
        bad = [good.replace("2.14.0", "2.13.0"), good.replace("Source SHA-256: ", "Source SHA-256: 0"),
               good.replace("sha256:", "sha512:"), good.replace("a" * 64, "a" * 63),
               good.replace("download.pytorch.org", "evil.example"), good + "--no-deps\n",
               good + "    --hash=sha256:" + "b" * 64 + "\n", good.replace("torch==", "torch>=")]
        for text in bad:
            with self.subTest(text=text), self.assertRaises(ValueError):
                release_requirements.validate_cuda_lock(text)

    def test_cuda_hash_is_part_of_base_lock_input_fingerprint(self):
        original = release_requirements.LOCK.read_text()
        altered = {release_requirements.INPUTS[3]: release_requirements.cuda_lock_text("b" * 64)}
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            release_requirements.validate(original, altered)
        body = "".join(original.splitlines(keepends=True)[5:])
        release_requirements.validate(release_requirements.header(body, altered) + body, altered)

    def test_cuda_index_selects_only_target_artifact(self):
        index, version = "https://download.pytorch.org/whl/cu126", "2.14.0+cu126"
        url = "https://download-r2.pytorch.org/whl/cu126/torch-2.14.0%2Bcu126-cp312-cp312-win_amd64.whl"
        link = lambda value: '<a href="' + value + '">wheel</a>'
        right = link(url + "#sha256=" + "a" * 64)
        others = (right.replace("cp312", "cp313") + right.replace("win_amd64", "manylinux_2_28_x86_64")
                  + right.replace("download-r2.pytorch.org", "evil.example") + right.replace("2.14.0", "2.13.0"))
        self.assertEqual(release_requirements.cuda_artifact_digest(others + right, index, version), "a" * 64)
        for text in [others, link(url), right + right.replace("a" * 64, "b" * 64),
                     right.replace("#sha256=", "?injected=yes#sha256=")]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                release_requirements.cuda_artifact_digest(text, index, version)

    def test_cuda_metadata_generation_is_bounded_and_rejects_redirects(self):
        for url, data in [("https://evil.example/torch/", b""),
                          ("https://download.pytorch.org/whl/cu126/torch/", b"x" * 2_000_001)]:
            response = mock.MagicMock(url=url)
            response.__enter__.return_value = response
            response.read.return_value = data
            opener = mock.Mock()
            opener.open.return_value = response
            with self.subTest(url=url), mock.patch.object(release_requirements, "build_opener", return_value=opener), \
                    self.assertRaises(ValueError):
                release_requirements.resolved_cuda_lock()

    def test_cuda_metadata_redirect_is_rejected_before_target_request(self):
        initial = "https://download.pytorch.org/whl/cu126/torch/"
        target = "https://redirect-target.invalid/metadata"
        for status in [301, 302, 303, 307, 308]:
            requests = []
            responses = []
            class RecordingHTTPS(urllib.request.HTTPSHandler):
                def https_open(self, request):
                    requests.append(request.full_url)
                    headers = Message()
                    code = status if request.full_url == initial else 200
                    if code != 200:
                        headers["Location"] = target
                    response = addinfourl(io.BytesIO(b""), headers, request.full_url, code)
                    response.msg = "Redirect" if code != 200 else "OK"
                    responses.append(response)
                    return response
            def opener(*handlers):
                # Keep the production redirect handler and real urllib redirect
                # machinery; replace only the HTTPS transport with a recorder.
                return urllib.request.build_opener(RecordingHTTPS(), *handlers)
            with self.subTest(status=status), \
                    mock.patch.object(release_requirements, "build_opener", side_effect=opener), \
                    mock.patch("socket.create_connection", side_effect=AssertionError("network forbidden")), \
                    self.assertRaisesRegex(ValueError, "redirects are not permitted"):
                release_requirements.resolved_cuda_lock()
            self.assertEqual(requests, [initial], "the redirect target must never be requested")
            self.assertTrue(all(response.closed for response in responses))

    def test_release_install_commands_require_both_artifact_locks(self):
        for relative in [".github/workflows/windows.yml", ".github/workflows/windows-release.yml", "windows/README.md"]:
            text = (release_requirements.ROOT / relative).read_text()
            command = next(line for line in text.splitlines() if "pip install" in line and "-r requirements-cuda-release.txt" in line)
            for flag in ["--require-hashes", "--no-deps", "--only-binary=:all:"]:
                self.assertIn(flag, command)


if __name__ == "__main__":
    unittest.main()

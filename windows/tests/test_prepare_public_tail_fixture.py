import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

import prepare_public_tail_fixture as fixture


class _Response:
    def __init__(self, data):
        self.data = data
        self.read_size = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size):
        self.read_size = size
        return self.data[:size]


class PublicTailFixtureTests(unittest.TestCase):
    def test_source_download_is_pinned_bounded_and_hash_checked(self):
        source = b"synthetic public test audio"
        response = _Response(source)
        opener = mock.Mock(return_value=response)
        with mock.patch.object(fixture, "SOURCE_SHA256",
                               hashlib.sha256(source).hexdigest()):
            self.assertEqual(fixture.download_source(opener), source)
            opener.assert_called_once_with(fixture.SOURCE_URL, timeout=30)
            self.assertEqual(response.read_size, fixture.MAX_SOURCE_BYTES + 1)

            with self.assertRaisesRegex(ValueError, "SHA-256"):
                fixture.download_source(lambda *_args, **_kwargs: _Response(b"changed"))

            with self.assertRaisesRegex(ValueError, "size limit"):
                fixture.download_source(lambda *_args, **_kwargs: _Response(
                    b"x" * (fixture.MAX_SOURCE_BYTES + 1)))

    def test_manifest_requires_human_review_before_probe(self):
        manifest = fixture.fixture_manifest()
        self.assertEqual(manifest["model"], "parakeet-tdt-0.6b-v3")
        self.assertEqual(manifest["runs"], 5)
        sample = manifest["samples"][0]
        self.assertEqual(sample["reference"], "")
        self.assertIs(sample["reference_reviewed"], False)
        self.assertEqual(sample["audio"], fixture.CLIP_NAME)
        self.assertEqual(fixture.CROP_END_SAMPLE - fixture.CROP_START_SAMPLE,
                         35_200)

    def test_clip_uses_app_resampling_policy_and_exact_issue_crop(self):
        class Audio:
            ndim = 1

            def __init__(self, values):
                self.values = values

            def __len__(self):
                return len(self.values)

            def __getitem__(self, index):
                return Audio(self.values[index])

        samples = Audio(range(64_000))
        numpy = types.ModuleType("numpy")
        numpy.float32 = object()
        numpy.asarray = mock.Mock(side_effect=lambda value, dtype: value)
        numpy.ascontiguousarray = mock.Mock(
            side_effect=lambda value, dtype: value)
        numpy.isfinite = mock.Mock(return_value=mock.Mock(all=lambda: True))
        soundfile = types.ModuleType("soundfile")
        soundfile.read = mock.Mock(return_value=(samples, 44_100))
        soxr = types.ModuleType("soxr")
        soxr.resample = mock.Mock(return_value=samples)

        with mock.patch.dict(sys.modules, {
                "numpy": numpy, "soundfile": soundfile, "soxr": soxr}):
            clip = fixture.prepare_clip(b"synthetic source")

        soundfile.read.assert_called_once()
        self.assertEqual(soundfile.read.call_args.kwargs, {"dtype": "float32"})
        soxr.resample.assert_called_once_with(
            samples, 44_100, 16_000, quality="HQ")
        self.assertEqual(len(clip), 35_200)
        self.assertEqual(clip.values[0], 16_000)
        self.assertEqual(clip.values[-1], 51_199)

    def test_generated_fixture_is_new_and_does_not_overwrite_review(self):
        with tempfile.TemporaryDirectory() as root:
            benchmarks = Path(root) / "benchmarks"
            fetch = mock.Mock(return_value=b"source")
            prepare = mock.Mock(return_value=b"clip")
            writer = mock.Mock(side_effect=lambda path, *_args, **_kwargs:
                               path.write_bytes(b"wav"))

            manifest_path = fixture.write_fixture(
                benchmarks, fetch=fetch, prepare=prepare, write_audio=writer)

            self.assertEqual(manifest_path.parent.name, fixture.FIXTURE_DIR_NAME)
            self.assertEqual((manifest_path.parent / fixture.CLIP_NAME).read_bytes(),
                             b"wav")
            self.assertEqual(json.loads(manifest_path.read_text()),
                             fixture.fixture_manifest())
            self.assertEqual(list(benchmarks.iterdir()), [manifest_path.parent])
            fetch.assert_called_once_with()
            prepare.assert_called_once_with(b"source")
            self.assertEqual(writer.call_args.kwargs, {"subtype": "FLOAT"})

            manifest_path.write_text("reviewed local work", encoding="utf-8")
            with self.assertRaisesRegex(FileExistsError, "already exists"):
                fixture.write_fixture(
                    benchmarks, fetch=fetch, prepare=prepare, write_audio=writer)
            self.assertEqual(manifest_path.read_text(), "reviewed local work")
            fetch.assert_called_once_with()

    def test_failure_leaves_no_partial_fixture(self):
        with tempfile.TemporaryDirectory() as root:
            benchmarks = Path(root) / "benchmarks"
            def fail_write(*_args, **_kwargs):
                raise OSError("synthetic write failure")

            with self.assertRaisesRegex(OSError, "synthetic"):
                fixture.write_fixture(
                    benchmarks, fetch=lambda: b"source",
                    prepare=lambda _source: b"clip", write_audio=fail_write)
            self.assertEqual(list(benchmarks.iterdir()), [])


if __name__ == "__main__":
    unittest.main()

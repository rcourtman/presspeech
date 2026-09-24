#!/usr/bin/env python3
"""Regressions for corpus replacement and result-to-input binding; no ASR model."""
import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


composer = load_module("context_composer", "compose-public-context-fixtures.py")
analyzer = load_module("context_analyzer", "analyze-context-variation.py")
benchmark_inputs = load_module("benchmark_inputs", "benchmark-inputs.py")


def files_digest(directory):
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob("*") if p.is_file()}


class BenchmarkInputWordCountTests(unittest.TestCase):
    def test_uncomposed_marks_match_benchmark_word_boundaries(self):
        self.assertEqual(benchmark_inputs.wer_tokens("x\u0301ample"), ["x\u0301ample"])
        self.assertEqual(benchmark_inputs.wer_tokens("n\u0301"),
                         benchmark_inputs.wer_tokens("\u0144"))
        self.assertNotEqual(benchmark_inputs.wer_tokens("q\u0307"),
                            benchmark_inputs.wer_tokens("q"))

    def test_uncomposed_mark_cannot_inflate_release_word_floor(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "clip.wav").write_bytes(b"synthetic audio")
            (directory / "clip.txt").write_text("x\u0301ample", encoding="utf-8")
            self.assertEqual(
                benchmark_inputs.validate_private_reference_corpus(directory, 1, 1),
                (1, 1))
            with self.assertRaisesRegex(benchmark_inputs.InputError,
                                        "1 reference words \\(minimum 2\\)"):
                benchmark_inputs.validate_private_reference_corpus(directory, 1, 2)


class ContextInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.output = self.root / "private corpus"
        self.populate_sources()
        composer.compose(self.source, self.output, 1, 4, 14.5, False)
        self.original = files_digest(self.output)
        self.digest = hashlib.sha256((self.output / "manifest.tsv").read_bytes()).hexdigest()

    def populate_sources(self, offset=0):
        for i in range(2):
            audio = self.source / f"clip-{i}.wav"
            composer.write_pcm16_fixture(audio, 4, 16000, i + offset + 1)
            audio.with_suffix(".txt").write_text(f"private utterance {i + offset}\n")

    def test_impossible_force_rebuild_preserves_previous_corpus(self):
        with self.assertRaises(composer.FixtureError):
            composer.compose(self.source, self.output, 2, 4, 14.5, True)
        self.assertEqual(files_digest(self.output), self.original)
        composer.validate_output(self.output)

    def test_staging_write_failure_preserves_previous_corpus(self):
        with mock.patch.object(composer, "write_wave", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                composer.compose(self.source, self.output, 1, 4, 14.5, True)
        self.assertEqual(files_digest(self.output), self.original)

    def test_replacement_failure_restores_previous_corpus(self):
        rename = os.rename
        def fail_publication(source, target):
            if Path(source).name.startswith(".private corpus.incomplete-"):
                raise OSError("publication failed")
            return rename(source, target)
        with mock.patch.object(composer.os, "rename", side_effect=fail_publication):
            with self.assertRaises(OSError):
                composer.compose(self.source, self.output, 1, 4, 14.5, True)
        self.assertEqual(files_digest(self.output), self.original)
        self.assertFalse(list(self.root.glob(".private corpus.previous-*")))

    def test_failed_restore_keeps_recoverable_previous_corpus(self):
        rename = os.rename
        def fail_publication_and_restore(source, target):
            if Path(target) == self.output:
                raise OSError("publication and restoration failed")
            return rename(source, target)
        with mock.patch.object(composer.os, "rename", side_effect=fail_publication_and_restore):
            with self.assertRaisesRegex(composer.FixtureError, "previous corpus retained"):
                composer.compose(self.source, self.output, 1, 4, 14.5, True)
        previous = list(self.root.glob(".private corpus.previous-*/corpus"))
        self.assertEqual(len(previous), 1)
        self.assertEqual(files_digest(previous[0]), self.original)

    def test_output_ancestor_cannot_delete_source(self):
        nested_source = self.output / "originals"
        shutil.copytree(self.source, nested_source)
        before = files_digest(self.output)
        with self.assertRaisesRegex(composer.FixtureError, "must not contain"):
            composer.compose(nested_source, self.output, 1, 4, 14.5, True)
        self.assertEqual(files_digest(self.output), before)

    def test_snapshot_rejects_input_changed_during_copy(self):
        read = Path.read_bytes
        audio = next(self.output.glob("*.wav"))
        calls = 0
        def mutate_during_copy(path):
            nonlocal calls
            value = read(path)
            if path == audio:
                calls += 1
                # Initial validation reads the payload and then its digest;
                # the third read is the snapshot copy.
                if calls == 3:
                    return value + b"changed"
            return value
        snapshot = self.root / "snapshot"
        with mock.patch.object(Path, "read_bytes", mutate_during_copy):
            with self.assertRaisesRegex(composer.FixtureError, "digest"):
                composer.snapshot_output(self.output, snapshot)
        self.assertFalse(snapshot.exists())

    def write_results(self, path):
        pairs = analyzer.load_manifest(self.output / "manifest.tsv")
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=analyzer.RESULT_FIELDS, delimiter="\t")
            writer.writeheader()
            for pair in pairs:
                for role, fixture in pair.fixtures.items():
                    for backend in ("v3", "v3-int8-v2"):
                        writer.writerow(dict(clip_id=fixture, backend=backend, backend_setting="test",
                            max_wer_percent="0", final_word_retained="true", p50_ms="1",
                            worst_word_errors="0", reference_words=pair.reference_words[role],
                            best_word_errors="0", best_final_word_retained="true",
                            context_manifest_sha256=self.digest))

    def analyze(self, results):
        return subprocess.run([sys.executable, str(ROOT / "analyze-context-variation.py"),
            "--manifest", str(self.output / "manifest.tsv"), "--results", str(results),
            "--require-nonregression"], capture_output=True, text=True)

    def test_old_results_rejected_after_same_word_count_recomposition(self):
        results = self.root / "results.tsv"
        self.write_results(results)
        valid = self.analyze(results)
        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.populate_sources(offset=10)
        composer.compose(self.source, self.output, 1, 4, 14.5, True)
        invalid = self.analyze(results)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("different context corpus", invalid.stderr)
        self.assertNotIn("**passes**", invalid.stdout)

    def test_legacy_unbound_results_cannot_claim_a_pass(self):
        results = self.root / "results.tsv"
        self.write_results(results)
        results.write_text("\n".join(line.rsplit("\t", 1)[0]
                                    for line in results.read_text().splitlines()) + "\n")
        invalid = self.analyze(results)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("lacks the context corpus binding", invalid.stderr)
        self.assertNotIn("**passes**", invalid.stdout)

    def test_runner_freezes_before_build_and_emits_bound_redacted_results(self):
        bench = self.root / "repo/experiments/swift-bench"
        bench.mkdir(parents=True)
        for name in ("run-real-model-comparison.sh", "compose-public-context-fixtures.py",
                     "dependency-provenance.py", "audio-input-evidence.py", "experiment-environment.py",
                     "benchmark-inputs.py", "Package.swift", "Package.resolved"):
            shutil.copyfile(ROOT / name, bench / name)
        production = self.root / "repo/swift"
        production.mkdir()
        for name in ("Package.swift", "Package.resolved"):
            shutil.copyfile(ROOT.parents[1] / "swift" / name, production / name)
        # The runner's postbuild gate inspects actual Git objects and source;
        # keep this dependency fixture genuine even though Swift itself is mocked.
        provenance_fixture = load_module("provenance_fixture", "test-dependency-provenance.py")
        provenance_fixture.prepare_fixture(bench, production)
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        expected = {hashlib.sha256(p.read_bytes()).hexdigest(): p.with_suffix(".txt").read_text()
                    for p in self.output.glob("*.wav")}
        expected_input_digest = benchmark_inputs.corpus_digest([
            {
                "audio_suffix": path.suffix.casefold(),
                "audio_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "reference_sha256": hashlib.sha256(path.with_suffix(".txt").read_bytes()).hexdigest(),
            }
            for path in sorted(self.output.glob("*.wav"))
        ])
        mock_bench = self.root / "mock-bench"
        mock_bench.write_text("#!" + sys.executable + '\n' + '''
import hashlib,json,os,sys,wave
from pathlib import Path
args=sys.argv[1:]; audio=Path(args[args.index('--file')+1])
reference=audio.with_suffix('.txt').read_text()
assert json.loads(os.environ['EXPECTED'])[hashlib.sha256(audio.read_bytes()).hexdigest()]==reference
assert '--redact-transcripts' in args
with wave.open(str(audio), 'rb') as source: frames = source.getnframes()
print(f'audio: {frames} samples (~1.00 s @ 16 kHz mono)')
print('experiment-environment: schema=1 inherited-controls=0 ci-present=0')
print('latency: p50=1.0 ms')
trials=int(args[args.index('--trials')+1])
for trial in range(1, trials + 1):
    print(f'output: trial={trial}/{trials} empty=false characters={len(reference)}')
print(f'transcript: [WER 0.0%] [final-word retained=true] [word-errors=0 reference-words={len(reference.split())}] <redacted>')
''')
        mock_bench.chmod(0o755)
        commands = {
            "afconvert": "import shutil,sys\nshutil.copyfile(sys.argv[-2],sys.argv[-1])\n",
            "swift": '''import os,shutil
from pathlib import Path
for path in Path(os.environ['ORIGINAL_CORPUS']).glob('*.txt'):
    path.write_text('changed during build extra words\\n')
for path in Path(os.environ['ORIGINAL_CORPUS']).glob('*.wav'):
    path.write_bytes(b'changed during build')
Path('.build/release').mkdir(parents=True)
shutil.copyfile(os.environ['MOCK_BENCH'], '.build/release/presspeech-bench')
Path('.build/release/presspeech-bench').chmod(0o755)
'''}
        for name, body in commands.items():
            command = fake_bin / name
            command.write_text("#!" + sys.executable + "\n" + body)
            command.chmod(0o755)
        results_dir = self.root / "results"
        run = subprocess.run(["bash", str(bench / "run-real-model-comparison.sh"),
            "--input-dir", str(self.output), "--out-dir", str(results_dir),
            "--candidate-backend", "v3-int8-v2", "--trials", "3"],
            env={**os.environ, "PATH":str(fake_bin)+os.pathsep+os.environ['PATH'],
                 "EXPECTED":json.dumps(expected), "ORIGINAL_CORPUS":str(self.output),
                 "MOCK_BENCH":str(mock_bench)}, capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertTrue(list(results_dir.glob("*.tsv")), run.stdout + run.stderr)
        tsv = next(results_dir.glob("*.tsv"))
        with tsv.open() as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        self.assertEqual(len(rows), 6)
        self.assertEqual({r['context_manifest_sha256'] for r in rows}, {self.digest})
        self.assertTrue(all(r['clip_id'].isdigit() for r in rows))
        self.assertIn("changed during build", next(self.output.glob("*.txt")).read_text())
        report = next(results_dir.glob("*.md")).read_text()
        self.assertNotIn(str(self.output), report)
        self.assertNotIn("private utterance", report)
        self.assertIn(self.digest, report)
        self.assertIn(f"Benchmark inputs SHA-256: {expected_input_digest}", report)
        self.assertIn("App FluidAudio revision:", report)
        self.assertIn("Baseline dependency: production-dependency", report)

        # A coherent dependency change during build must still invalidate the
        # provenance captured before the build, before any inference/report.
        swift = fake_bin / "swift"
        swift.write_text("#!" + sys.executable + "\n" + """
import json
from pathlib import Path
package=Path('Package.swift')
lock=Path('Package.resolved')
data=json.loads(lock.read_text())
pin=next(p for p in data['pins'] if p['identity']=='fluidaudio')
original=pin['state']['revision']
pin['state']['revision']='b'*40
package.write_text(package.read_text().replace(original, 'b'*40))
lock.write_text(json.dumps(data))
""")
        refused_dir = self.root / "refused-results"
        refused = subprocess.run(["bash", str(bench / "run-real-model-comparison.sh"),
            "--input-dir", str(self.source), "--out-dir", str(refused_dir),
            "--candidate-backend", "v3-int8-v2", "--trials", "3"],
            env={**os.environ, "PATH":str(fake_bin)+os.pathsep+os.environ['PATH']},
            capture_output=True, text=True, timeout=30)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("built workspace uses an edited or different FluidAudio dependency", refused.stderr)
        self.assertFalse(list(refused_dir.glob("*.tsv")))
        self.assertFalse(list(refused_dir.glob("*.md")))


if __name__ == "__main__":
    unittest.main()

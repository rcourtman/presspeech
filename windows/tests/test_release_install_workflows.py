"""Exercise native-command failure propagation without installing anything."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


def install_block(filename):
    text = (ROOT / ".github/workflows" / filename).read_text(encoding="utf-8")
    marker = "      - name: Install "
    step = text.split(marker, 1)[1].split("      - name:", 1)[0]
    body = step.split("        run: |\n", 1)[1]
    return "\n".join(line[10:] for line in body.splitlines())


@unittest.skipUnless(POWERSHELL, "PowerShell is required for workflow execution tests")
class ReleaseInstallWorkflowTests(unittest.TestCase):
    def run_block(self, filename, fail_at):
        # The workflow's python/winget commands are intercepted. The only
        # native process is this test interpreter, returning a chosen status.
        script = r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$script:Calls = [System.Collections.Generic.List[string]]::new()
$script:FailAt = FAIL_AT
function Invoke-TestNative([string]$command) {
    $script:Calls.Add($command)
    $status = if ($script:Calls.Count -eq $script:FailAt) { 19 } else { 0 }
    & $env:PRESSPEECH_TEST_NATIVE_PYTHON -c "raise SystemExit($status)"
    $global:LASTEXITCODE = $LASTEXITCODE
}
function python { Invoke-TestNative ('python ' + ($args -join ' ')) }
function winget { Invoke-TestNative ('winget ' + ($args -join ' ')) }
$env:PIP_CONFIG_FILE = 'untrusted-inherited-config.ini'
$failed = $false
try {
WORKFLOW_BODY
} catch { $failed = $true }
[ordered]@{failed=$failed; calls=@($script:Calls.ToArray()); config=$env:PIP_CONFIG_FILE} | ConvertTo-Json -Compress
'''.replace("FAIL_AT", str(fail_at)).replace("WORKFLOW_BODY", install_block(filename))
        result = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand",
             base64.b64encode(script.encode("utf-16le")).decode("ascii")],
            env={**os.environ, "PRESSPEECH_TEST_NATIVE_PYTHON": sys.executable},
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip())

    def test_ci_and_release_stop_at_each_failed_native_command(self):
        for filename, count in (("windows.yml", 4), ("windows-release.yml", 5)):
            for fail_at in range(1, count + 1):
                with self.subTest(workflow=filename, fail_at=fail_at):
                    result = self.run_block(filename, fail_at)
                    self.assertTrue(result["failed"], result)
                    self.assertEqual(len(result["calls"]), fail_at, result)

    def test_both_success_paths_install_and_verify_complete_runtime(self):
        for filename, count in (("windows.yml", 4), ("windows-release.yml", 5)):
            with self.subTest(workflow=filename):
                result = self.run_block(filename, 0)
                self.assertFalse(result["failed"], result)
                self.assertEqual(len(result["calls"]), count, result)
                self.assertIn("--require-hashes", result["calls"][0])
                self.assertIn("--index-url https://pypi.org/simple", result["calls"][0])
                self.assertIn("-r requirements-cuda.txt", result["calls"][1])
                for command in result["calls"][:2]:
                    for option in ("--isolated", "--no-deps", "--only-binary=:all:"):
                        self.assertIn(option, command)
                self.assertEqual(result["calls"][2], "python -m pip check")
                self.assertEqual(result["calls"][3], "python release_requirements.py --verify-environment")
                self.assertEqual(result["config"], "NUL")

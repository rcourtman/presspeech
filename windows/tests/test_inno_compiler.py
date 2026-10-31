"""Regressions for selecting an engine beside an older installed compiler."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


HELPER = Path(__file__).resolve().parents[1] / "find-inno-compiler.ps1"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


@unittest.skipUnless(POWERSHELL, "PowerShell is required")
class InnoCompilerTests(unittest.TestCase):
    def run_powershell(self, script, env):
        result = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand",
             base64.b64encode(script.encode("utf-16le")).decode("ascii")],
            env={**os.environ, "PRESSPEECH_INNO_HELPER": str(HELPER), **env},
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_old_install_does_not_shadow_pin_or_override_explicit_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "old" / "Inno Setup 6" / "ISCC.exe"
            pinned = root / "pinned" / "Inno Setup 6" / "ISCC.exe"
            for path in (old, pinned):
                path.parent.mkdir(parents=True)
                path.touch()
            output = self.run_powershell(r'''
$ErrorActionPreference = 'Stop'
. $env:PRESSPEECH_INNO_HELPER
$env:LOCALAPPDATA = Join-Path $env:PRESSPEECH_TEST_ROOT 'unused'
${env:ProgramFiles(x86)} = Join-Path $env:PRESSPEECH_TEST_ROOT 'old'
$env:ProgramFiles = Join-Path $env:PRESSPEECH_TEST_ROOT 'pinned'
function Get-PresspeechInnoCompilerVersion([string]$Compiler) {
    if ($Compiler -ceq $env:PRESSPEECH_TEST_PIN) { return '6.7.3' }
    return '6.7.1'
}
$selected = Find-PresspeechInnoCompiler
$rejected = $false
try { $null = Find-PresspeechInnoCompiler -ExplicitPath $env:PRESSPEECH_TEST_OLD }
catch { $rejected = $_.Exception.Message -like '*6.7.3 compiler engine was not found*' }
$explicit = Find-PresspeechInnoCompiler -ExplicitPath $env:PRESSPEECH_TEST_PIN
@{selected=$selected; rejected=$rejected; explicit=$explicit} | ConvertTo-Json -Compress
''', {"PRESSPEECH_TEST_ROOT": str(root), "PRESSPEECH_TEST_PIN": str(pinned),
      "PRESSPEECH_TEST_OLD": str(old)})
            result = json.loads(output)
            self.assertEqual(Path(result["selected"]), pinned)
            self.assertEqual(Path(result["explicit"]), pinned)
            self.assertTrue(result["rejected"])

    @unittest.skipIf(os.name == "nt", "Unix executable shim; Windows uses the real engine in opt-in CI")
    def test_version_probe_uses_and_removes_its_own_output_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            compiler = Path(tmp) / "compiler"
            report = Path(tmp) / "probe-report.txt"
            compiler.write_text('''#!/bin/sh
pwd > "$PRESSPEECH_TEST_REPORT"
cat >> "$PRESSPEECH_TEST_REPORT"
printf 'Compiler engine version: Inno Setup 6.7.3\\n'
''', encoding="utf-8")
            compiler.chmod(0o700)
            output = self.run_powershell(r'''
$ErrorActionPreference = 'Stop'
. $env:PRESSPEECH_INNO_HELPER
Get-PresspeechInnoCompilerVersion $env:PRESSPEECH_TEST_COMPILER
''', {"PRESSPEECH_TEST_COMPILER": str(compiler), "PRESSPEECH_TEST_REPORT": str(report)})
            self.assertEqual(output, "6.7.3")
            cwd, script = report.read_text().split("\n", 1)
            self.assertTrue(Path(cwd).name.startswith("presspeech-inno-probe-"))
            self.assertFalse(Path(cwd).exists())
            self.assertIn("Output=no\nOutputDir=.\n", script)


if __name__ == "__main__":
    unittest.main()

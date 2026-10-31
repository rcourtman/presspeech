"""Measure installer assumptions on a disposable hosted Windows runner.

Synthetic payloads and fresh AppIds only. This never installs Presspeech or
changes its release script. Preserve the output directory as a CI artifact.
"""
import argparse
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


class Qualification:
    def __init__(self, root, compiler):
        self.root = root
        self.compiler = compiler
        self.results = {}
        self.apps = []
        self.sequence = 0

    def run(self, args, *, expected=0, timeout=120):
        self.sequence += 1
        log = self.root / f"command-{self.sequence:02d}.txt"
        with log.open("w", encoding="utf-8") as output:
            process = subprocess.Popen([str(arg) for arg in args], stdout=output,
                                       stderr=subprocess.STDOUT)
            try:
                status = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               capture_output=True, timeout=20)
                raise RuntimeError(f"Command timed out; see {log.name}")
        if expected is not None and status != expected:
            raise RuntimeError(f"Exit {status}, expected {expected}: {log.name}\n"
                               + log.read_text(encoding="utf-8", errors="replace")[-6000:])
        return status

    def compile(self, name, files, code="", setup="", extra="", app_id=None):
        folder = self.root / name
        folder.mkdir()
        app_id = app_id or str(uuid.uuid4())
        script = folder / "probe.iss"
        script.write_text(fr"""[Setup]
AppId={app_id}
AppName=Installer qualification {name}
AppVersion=1.0.0
DefaultDirName={{localappdata}}\InstallerQualificationUnused
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
DisableDirPage=yes
DisableProgramGroupPage=yes
CloseApplications=no
RestartApplications=no
OutputDir={folder}
OutputBaseFilename=probe
Compression=none
{setup}
[Files]
{files}
{extra}
[Code]
{code}
""", encoding="utf-8")
        self.run([self.compiler, "/Qp", script])
        return folder / "probe.exe"

    def install(self, installer, target, expected=0):
        self.apps.append(target)
        log = installer.parent / f"install-{self.sequence:02d}.log"
        return self.run([installer, "/VERYSILENT", "/SUPPRESSMSGBOXES",
                         "/NORESTART", "/SP-", "/NOICONS", "/NOCLOSEAPPLICATIONS",
                         f"/DIR={target}", f"/LOG={log}"], expected=expected)

    def record(self, name, **details):
        self.results[name] = details
        self.save()
        print(f"{name}: {json.dumps(details, sort_keys=True)}", flush=True)

    @staticmethod
    @contextmanager
    def locked_file(path):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            kernel.CloseHandle(handle)

    def save(self):
        (self.root / "results.json").write_text(
            json.dumps(self.results, indent=2) + "\n", encoding="utf-8")

    def cleanup(self):
        for target in reversed(list(dict.fromkeys(self.apps))):
            uninstaller = target / "unins000.exe"
            if uninstaller.exists():
                self.run([uninstaller, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                         expected=None)

    def experiments(self):
        payload = self.root / "payload"
        payload.mkdir()
        for name, text in (("first.txt", "new-first"), ("second.txt", "new-second"),
                           ("last.txt", "new-last")):
            (payload / name).write_text(text, encoding="ascii")
        files = '\n'.join(f'Source: "{payload / name}"; DestDir: "{{app}}"; '
                          'Flags: ignoreversion'
                          for name in ("first.txt", "second.txt", "last.txt"))

        # E1: explicit entry order; keep full Inno log as the measured record.
        installer = self.compile("E1-order", files)
        target = self.root / "E1-app"
        self.install(installer, target)
        install_log = next(installer.parent.glob("install-*.log")).read_text(
            encoding="utf-8-sig", errors="replace")
        positions = [install_log.index(f"\\{name}") for name in
                     ("first.txt", "second.txt", "last.txt")]
        assert positions == sorted(positions), positions
        self.record("E1", explicit_file_entry_order=True)

        # BeforeInstall exceptions are caught by Inno's script boundary. Record
        # that separately; they cannot inject the fatal failure E2 needs.
        installer = self.compile("E2-script-exception", files + '; BeforeInstall: FailLast',
                                "procedure FailLast; begin RaiseException('injected final-file failure'); end;")
        target = self.root / "E2-exception-app"
        status = self.install(installer, target, expected=None)
        self.record("E2-script-exception", exit_code=status,
                    final_file_installed=(target / "last.txt").exists())

        # E2: native last-file copy failure, after an overwrite and a new file.
        target = self.root / "E2-app"
        target.mkdir()
        (target / "first.txt").write_text("old-first", encoding="ascii")
        (target / "last.txt").write_text("old-last", encoding="ascii")
        installer = self.compile("E2-rollback", files)
        with self.locked_file(target / "last.txt"):
            status = self.install(installer, target, expected=None)
        state = {p.name: p.read_text(encoding="ascii") for p in target.glob("*.txt")}
        self.record("E2", exit_code=status, surviving_files=state,
                    overwritten_old_file_restored=state.get("first.txt") == "old-first",
                    new_files_rolled_back="second.txt" not in state)
        assert status != 0, state
        assert state.get("first.txt") in {"new-first", "old-first"}, state
        assert "second.txt" not in state and state.get("last.txt") == "old-last", state

        # E3: exception after installation does not roll the new bundle back.
        post = """procedure CurStepChanged(Step: TSetupStep);
begin
  if Step = ssPostInstall then begin
    if not FileExists(ExpandConstant('{app}\\unins000.dat')) then
      RaiseException('uninstall log missing before postinstall');
    Log('QUALIFICATION: uninstall log exists at ssPostInstall');
    RaiseException('injected postinstall failure');
  end;
end;
"""
        installer = self.compile("E3-postinstall", files, post)
        target = self.root / "E3-app"
        status = self.install(installer, target, expected=None)
        assert (target / "last.txt").exists()
        assert (target / "unins000.dat").exists()
        import winreg
        # The AppId is synthetic and unique, never the product's AppId.
        script = (installer.parent / "probe.iss").read_text(encoding="utf-8")
        app_id = script.split("AppId=", 1)[1].splitlines()[0]
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{app_id}_is1",
                            access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY):
            pass
        installer2 = self.compile("E3-exit-code", files,
                                 "function GetCustomSetupExitCode: Integer; begin Result := 23; end;")
        self.install(installer2, self.root / "E3-exit-app", expected=23)
        self.record("E3", postinstall_exception_exit=status,
                    postinstall_files_and_uninstall_registration_survive=True,
                    custom_exit_code=23)

        # E5: wildcard directory cleanup and appended uninstall ownership.
        app_id = str(uuid.uuid4())
        cleanup = '[UninstallDelete]\nType: filesandordirs; Name: "{app}\\_internal-*"'
        installer = self.compile("E5-first", files, extra=cleanup, app_id=app_id)
        target = self.root / "E5-app"
        self.install(installer, target)
        installer2 = self.compile("E5-second", files, extra=cleanup, app_id=app_id)
        self.install(installer2, target)
        (target / "_internal-unlogged").mkdir()
        (target / "_internal-unlogged" / "orphan.txt").write_text("orphan")
        self.run([target / "unins000.exe", "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
        # The uninstaller may finish its self-deletion asynchronously.
        for _ in range(100):
            if not target.exists():
                break
            time.sleep(.1)
        assert not target.exists(), list(target.rglob("*"))
        self.record("E5", append_and_wildcard_uninstall_clean=True)

        # E6: actual pinned bootloader; poison remains outside its embedded path.
        source = self.root / "bundle_probe.py"
        source.write_text('''import importlib.metadata as m
import json, sys, time
from pathlib import Path
if len(sys.argv) > 1 and sys.argv[1] == "--hold":
    Path(sys.argv[2]).write_text("ready")
    time.sleep(120)
else:
    print(json.dumps({"root": Path(sys._MEIPASS).name,
                      "marker": m.version("qualification-marker")}))
''', encoding="utf-8")
        metadata = self.root / "qualification_marker-2.0.dist-info"
        metadata.mkdir()
        (metadata / "METADATA").write_text("Name: qualification-marker\nVersion: 2.0\n")
        self.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                  "--onedir", "--name", "bundle-probe", "--contents-directory", "_internal-probe-build",
                  "--distpath", self.root / "dist", "--workpath", self.root / "build",
                  "--specpath", self.root, "--add-data", f"{metadata}:qualification_marker-2.0.dist-info",
                  source], timeout=300)
        bundle = self.root / "dist" / "bundle-probe"
        assert {p.name for p in bundle.iterdir()} == {"bundle-probe.exe", "_internal-probe-build"}
        for directory in ("_internal", "_internal-other-build"):
            poison = bundle / directory / "qualification_marker-0.0.dist-info"
            poison.mkdir(parents=True)
            (poison / "METADATA").write_text("Name: qualification-marker\nVersion: 0.0\n")
        executable = bundle / "bundle-probe.exe"
        result = subprocess.run([executable], capture_output=True, text=True, check=True, timeout=30)
        observation = json.loads(result.stdout)
        assert observation == {"root": "_internal-probe-build", "marker": "2.0"}, observation
        self.record("E6", **observation, stale_sibling_metadata_ignored=True)

        # E4: idle replacement, running-image refusal and cross-process mutex.
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.MoveFileExW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        kernel.MoveFileExW.restype = wintypes.BOOL
        live = bundle / "live.exe"
        pending = bundle / "pending.exe"
        live.write_bytes(executable.read_bytes())
        pending.write_bytes(executable.read_bytes())
        assert kernel.MoveFileExW(str(pending), str(live), 1 | 8)
        ready = self.root / "running-ready.txt"
        process = subprocess.Popen([str(live), "--hold", str(ready)])
        try:
            for _ in range(300):
                if ready.exists():
                    break
                if process.poll() is not None:
                    raise RuntimeError("Frozen holding process exited")
                time.sleep(.1)
            assert ready.exists()
            pending.write_bytes(executable.read_bytes())
            assert not kernel.MoveFileExW(str(pending), str(live), 1 | 8)
            error = ctypes.get_last_error()
            assert pending.exists() and live.exists()
        finally:
            process.kill()
            process.wait(timeout=10)
        kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        mutex = "Local\\InstallerQualification-" + uuid.uuid4().hex
        handle = kernel.CreateMutexW(None, False, mutex)
        assert handle
        try:
            installer = self.compile("E4-mutex", files, f"""function InitializeSetup: Boolean;
begin
  Result := CheckForMutexes('{mutex}');
  if not Result then RaiseException('cross-process mutex not observed');
end;
""")
            self.install(installer, self.root / "E4-app")
        finally:
            kernel.CloseHandle(handle)
        self.record("E4", idle_swap=True, running_swap_refused=True, win32_error=error,
                    cross_process_mutex_observed=True)

        # E7: a native no-sharing handle; observe silent copy-error policy.
        target = self.root / "E7-app"
        target.mkdir()
        locked = target / "first.txt"
        locked.write_text("old-locked")
        with self.locked_file(locked):
            installer = self.compile("E7-locked-copy", files)
            status = self.install(installer, target, expected=None)
        assert locked.read_text() == "old-locked"
        self.record("E7", silent_locked_copy_exit=status, old_locked_file_preserved=True,
                    later_file_installed=(target / "last.txt").exists())

        # E8: model insufficient space using Inno's explicit requirement; do
        # not fill a CI volume. Reinstall skips the directory page too.
        target = self.root / "E8-app"
        app_id = str(uuid.uuid4())
        installer = self.compile("E8-first", files, app_id=app_id)
        self.install(installer, target)
        installer2 = self.compile("E8-space", files, setup="ExtraDiskSpaceRequired=1000000000000000",
                                  app_id=app_id)
        status = self.install(installer2, target, expected=None)
        self.record("E8", silent_skipped_directory_page_exit=status,
                    required_bytes=1000000000000000,
                    limit="Synthetic space requirement, not an actual disk-full test")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if (sys.platform != "win32" or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"):
        parser.error("native qualification requires a disposable GitHub-hosted Windows runner")
    root = args.output.resolve()
    runner_temp = Path(os.environ["RUNNER_TEMP"]).resolve()
    if not root.is_relative_to(runner_temp) or root == runner_temp or root.exists():
        parser.error("output must be a new child directory of RUNNER_TEMP")
    if not args.compiler.is_file():
        parser.error("Inno Setup compiler missing")
    root.mkdir()
    qualification = Qualification(root, args.compiler.resolve())
    try:
        qualification.experiments()
    finally:
        qualification.save()
        qualification.cleanup()


if __name__ == "__main__":
    main()

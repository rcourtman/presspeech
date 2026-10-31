"""Exercise the production installer with isolated native payloads in hosted CI."""
import argparse
import ctypes
from ctypes import wintypes
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

import installer_bundle as bundle


WINDOWS = Path(__file__).resolve().parent


class UpgradeQualification:
    def __init__(self, root, compiler):
        self.root, self.compiler = root, compiler
        self.results, self.command_number = {}, 0
        self.case_number = 0

    def record(self, name, **details):
        self.results[name] = details
        (self.root / "results.json").write_text(json.dumps(self.results, indent=2) + "\n")
        print(f"QUALIFIED {name}: {json.dumps(details)}", flush=True)

    def run(self, args, expected=0, timeout=300):
        self.command_number += 1
        log = self.root / f"command-{self.command_number:03d}.txt"
        with log.open("w", encoding="utf-8") as output:
            process = subprocess.Popen([str(a) for a in args], stdout=output, stderr=subprocess.STDOUT)
            try:
                status = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.kill(process)
                raise RuntimeError(f"Command timed out: {log}")
        if expected is not None and status != expected:
            raise RuntimeError(f"Exit {status}, expected {expected}: {log}\n"
                               + log.read_text(errors="replace")[-8000:])
        return status

    @staticmethod
    def kill(process):
        if process.poll() is None:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           capture_output=True, timeout=30)
        process.wait(timeout=30)

    def build_payload(self, label, marker, legacy=False):
        source = self.root / f"probe-{label}.py"
        source.write_text('''import ctypes, importlib.metadata, json, os, sys, time
from pathlib import Path
if len(sys.argv) > 1 and sys.argv[1] == "--hold":
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel.CreateMutexW(None, False, sys.argv[3])
    if not handle: raise RuntimeError("mutex unavailable")
    Path(sys.argv[2]).write_text("ready")
    time.sleep(180)
elif len(sys.argv) > 1 and sys.argv[1] == "--package-selftest":
    Path(os.environ["PRESSPEECH_PACKAGE_SELFTEST_RESULT"]).write_text("ok")
else:
    Path(sys.argv[1]).write_text(json.dumps({"bundle": Path(sys._MEIPASS).name,
                                           "marker": importlib.metadata.version("qualification-marker")}))
''', encoding="utf-8")
        metadata = self.root / f"marker-{label}" / f"qualification_marker-{marker}.dist-info"
        metadata.mkdir(parents=True)
        (metadata / "METADATA").write_text(f"Name: qualification-marker\nVersion: {marker}\n")
        name = "_internal" if legacy else bundle.PREFIX + uuid.uuid4().hex
        self.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
                  "--name", "Presspeech", "--contents-directory", name,
                  "--distpath", self.root / f"dist-{label}",
                  "--workpath", self.root / f"build-{label}", "--specpath", source.parent,
                  "--add-data", f"{metadata}:qualification_marker-{marker}.dist-info", source])
        package = self.root / f"dist-{label}" / "Presspeech"
        # Two large files make copy interruption and real disk-full failures
        # meaningful without packaging models or exercising any microphone.
        for filename in ("00-padding.bin", "zz-sentinel.bin"):
            with (package / name / filename).open("wb") as output:
                for _ in range(16):
                    output.write(b"\0" * 1024 * 1024)
        include = self.root / f"bundle-{label}.iss"
        if not legacy:
            bundle.prepare(package, include)
        return package, name, include

    def compile(self, case, package, include, legacy=False):
        output = case["root"] / ("old-installer" if legacy else "new-installer")
        output.mkdir()
        if legacy:
            script = output / "legacy.iss"
            script.write_text(fr'''[Setup]
AppId={case['id']}
AppName=Presspeech qualification legacy
AppVersion=0.1.11
DefaultDirName={{localappdata}}\PresspeechQualificationUnused
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
DisableDirPage=yes
CloseApplications=no
AppMutex={case['mutex']}
OutputDir={output}
OutputBaseFilename=legacy
Compression=none
[Files]
Source: "{package}\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "{case['startup']}"; Flags: dontcreatekey uninsdeletevalue
''')
            args = [script]
            executable = output / "legacy.exe"
        else:
            args = ["/DAppVersion=0.1.12", f"/DSourceDir={package}",
                    f"/DBundleMetadataFile={include}", f"/DInstallerOutputDir={output}",
                    "/DQualification=1", f"/DQualificationAppId={case['id']}",
                    WINDOWS / "installer.iss"]
            executable = output / "Presspeech-Setup-0.1.12-x64.exe"
        self.run([self.compiler, "/Qp", *args])
        return executable

    @contextmanager
    def case(self, name, old=True, volume=None):
        self.case_number += 1
        root = self.root / f"case-{self.case_number:02d}-{name}"
        root.mkdir()
        app_id = str(uuid.uuid4())
        case = {"root": root, "id": app_id,
                "app": (Path(volume) / f"app-{self.case_number}") if volume else root / "app",
                "mutex": "Local\\PresspeechQualification-" + app_id,
                "startup": "PresspeechQualification-" + app_id}
        case["new"] = self.compile(case, self.package_b, self.include_b)
        if old:
            case["old"] = self.compile(case, self.package_a, self.include_a)
            self.install(case, case["old"])
            self.probe(case, "1.0", self.name_a)
        try:
            yield case
        finally:
            uninstaller = case["app"] / "unins000.exe"
            if uninstaller.exists():
                self.run([uninstaller, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                          f"/LOG={root / 'uninstall.log'}"])
            import winreg
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                    r"Software\Microsoft\Windows\CurrentVersion\Run",
                                    access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                try:
                    winreg.QueryValueEx(key, case["startup"])
                except FileNotFoundError:
                    pass
                else:
                    raise AssertionError("Uninstall left the owned startup entry")

    def install(self, case, installer=None, expected=0, extra=()):
        self.command_number += 1
        log = case["root"] / f"install-{self.command_number:03d}.log"
        return self.run([installer or case["new"], "/VERYSILENT", "/SUPPRESSMSGBOXES",
                         "/NORESTART", "/NOCLOSEAPPLICATIONS", "/NORESTARTAPPLICATIONS",
                         "/NOICONS", "/SP-", "/TASKS=", f"/DIR={case['app']}", f"/LOG={log}",
                         *extra], expected=expected)

    def probe(self, case, marker, name):
        result = case["root"] / f"probe-{uuid.uuid4().hex}.json"
        self.run([case["app"] / "Presspeech.exe", result], timeout=30)
        value = json.loads(result.read_text())
        assert value == {"bundle": name, "marker": marker}, value

    @contextmanager
    def checkpoint(self, case, stage):
        checkpoint = case["root"] / f"checkpoint-{uuid.uuid4().hex}"
        log = checkpoint.with_suffix(".log")
        process = subprocess.Popen([str(case["new"]), "/VERYSILENT", "/SUPPRESSMSGBOXES",
                                    "/NORESTART", "/NOCLOSEAPPLICATIONS", "/NOICONS", "/SP-",
                                    "/TASKS=", f"/DIR={case['app']}", f"/LOG={log}",
                                    f"/QASTAGE={stage}", f"/QACHECKPOINT={checkpoint}"])
        try:
            for _ in range(600):
                if checkpoint.exists() and checkpoint.read_text().strip():
                    break
                if process.poll() is not None:
                    raise RuntimeError(f"Installer exited before {stage}: {log}")
                time.sleep(.05)
            else:
                raise RuntimeError(f"No {stage} checkpoint: {log}")
            yield process, checkpoint
        finally:
            self.kill(process)

    @staticmethod
    def resume(process, checkpoint, expected):
        Path(str(checkpoint) + ".release").touch()
        status = process.wait(timeout=120)
        assert status == expected, (status, expected, str(checkpoint))

    @staticmethod
    @contextmanager
    def locked(path):
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

    def qualify(self):
        self.package_a, self.name_a, self.include_a = self.build_payload("a", "1.0")
        self.package_b, self.name_b, self.include_b = self.build_payload("b", "2.0")
        legacy, _, _ = self.build_payload("legacy", "1.0", legacy=True)

        with self.case("fresh", old=False) as case:
            self.install(case)
            self.probe(case, "2.0", self.name_b)
        self.record("fresh-install", result="passed")

        with self.case("legacy", old=False) as case:
            old = self.compile(case, legacy, None, legacy=True)
            self.install(case, old)
            self.probe(case, "1.0", "_internal")
            self.seed_startup(case)
            self.install(case)
            self.probe(case, "2.0", self.name_b)
            self.check_startup(case)
        self.record("legacy-migration", startup="preserved through upgrade, removed by uninstall")

        with self.case("same-version") as case:
            self.seed_startup(case)
            self.install(case)
            self.probe(case, "2.0", self.name_b)
            assert not (case["app"] / self.name_a).exists()
            before = self.payload_snapshot(case)
            self.install(case)
            assert self.payload_snapshot(case) == before, "Identical installer rewrote immutable payload"
            self.check_startup(case)
            self.probe(case, "2.0", self.name_b)
        self.record("same-version-and-identical-reinstall", payload_not_rewritten=True)

        with self.case("damaged-active", old=False) as case:
            self.install(case)
            path = case["app"] / self.name_b / "00-padding.bin"
            with path.open("r+b") as output:
                output.write(b"damaged")
            before = bundle.digest(case["app"] / "Presspeech.exe")
            self.install(case, expected=7)
            assert bundle.digest(case["app"] / "Presspeech.exe") == before
            assert path.read_bytes()[:7] == b"damaged"
        self.record("damaged-active-generation", refused_without_overwrite=True)

        for fault in ("missing", "corrupt", "extra", "manifest", "pending"):
            with self.case("integrity-" + fault) as case:
                with self.checkpoint(case, "staged") as (process, checkpoint):
                    target = case["app"] / self.name_b
                    if fault == "missing":
                        (target / "zz-sentinel.bin").unlink()
                    elif fault == "extra":
                        (target / "poisoned.dist-info").mkdir()
                        (target / "poisoned.dist-info" / "METADATA").write_text("poison")
                    else:
                        filename = {"corrupt": "zz-sentinel.bin", "manifest": bundle.MANIFEST,
                                    "pending": bundle.PENDING}[fault]
                        with (target / filename).open("r+b") as output:
                            output.write(b"bad")
                    self.resume(process, checkpoint, 23)
                self.probe(case, "1.0", self.name_a)
                self.install(case)
                self.probe(case, "2.0", self.name_b)
            self.record("integrity-" + fault, old_preserved=True, rerun_recovered=True)

        for fault in ("locked", "mutex"):
            with self.case("activation-" + fault) as case:
                with self.checkpoint(case, "verified") as (process, checkpoint):
                    if fault == "locked":
                        with self.locked(case["app"] / "Presspeech.exe"):
                            self.resume(process, checkpoint, 23)
                    else:
                        ready = case["root"] / "ready"
                        holder = subprocess.Popen([str(case["app"] / "Presspeech.exe"), "--hold",
                                                   str(ready), case["mutex"]])
                        try:
                            for _ in range(300):
                                if ready.exists(): break
                                time.sleep(.05)
                            assert ready.exists()
                            self.resume(process, checkpoint, 23)
                        finally:
                            self.kill(holder)
                self.probe(case, "1.0", self.name_a)
                self.install(case)
                self.probe(case, "2.0", self.name_b)
            self.record("activation-" + fault, old_preserved=True, rerun_recovered=True)

        with self.case("copy-error") as case:
            with self.checkpoint(case, "payload") as (process, checkpoint):
                candidates = [p for p in (self.package_b / self.name_b).rglob('*')
                              if p.is_file() and p.name not in (bundle.OWNER, bundle.MANIFEST)
                              and not (case["app"] / self.name_b / p.relative_to(self.package_b / self.name_b)).exists()]
                assert candidates, 'No future payload file available for copy failure'
                source = max(candidates, key=lambda p: p.stat().st_size)
                target = case["app"] / self.name_b / source.relative_to(self.package_b / self.name_b)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("locked incomplete destination")
                with self.locked(target):
                    self.resume(process, checkpoint, 5)
            self.probe(case, "1.0", self.name_a)
            self.install(case)
            self.probe(case, "2.0", self.name_b)
        self.record("native-copy-failure", old_preserved=True, rerun_recovered=True)

        with self.case("cancel") as case:
            with self.checkpoint(case, "payload") as (process, checkpoint):
                user = ctypes.WinDLL("user32", use_last_error=True)
                user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
                assert user.PostMessageW(int(checkpoint.read_text()), 0x10, 0, 0)
                self.resume(process, checkpoint, 5)
            self.probe(case, "1.0", self.name_a)
            self.install(case)
            self.probe(case, "2.0", self.name_b)
        self.record("native-cancel", old_preserved=True, rerun_recovered=True)

        for stage in ("payload", "staged", "verified", "activated"):
            with self.case("kill-" + stage) as case:
                with self.checkpoint(case, stage) as (process, _):
                    self.kill(process)
                activated = stage == "activated"
                self.probe(case, "2.0" if activated else "1.0", self.name_b if activated else self.name_a)
                self.install(case)
                self.probe(case, "2.0", self.name_b)
            self.record("process-kill-" + stage, usable_install_preserved=True, rerun_recovered=True)

        self.qualify_reparse()
        self.qualify_disk()

    def payload_snapshot(self, case):
        return {str(p.relative_to(case["app"] / self.name_b)): (bundle.digest(p), p.stat().st_mtime_ns)
                for p in (case["app"] / self.name_b).rglob("*") if p.is_file()}

    @staticmethod
    def seed_startup(case):
        import winreg
        value = '"' + str(case["app"] / "Presspeech.exe") + '"'
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Run",
                                access=winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
            winreg.SetValueEx(key, case["startup"], 0, winreg.REG_SZ, value)

    @staticmethod
    def check_startup(case):
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run",
                            access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            assert winreg.QueryValueEx(key, case["startup"])[0] == '"' + str(case["app"] / "Presspeech.exe") + '"'

    def qualify_reparse(self):
        victim = self.root / "junction-victim"
        victim.mkdir()
        (victim / "keep.txt").write_text("outside installation")
        with self.case("junction-admission") as case:
            junction = case["app"] / self.name_b
            self.run(["cmd", "/c", "mklink", "/J", junction, victim])
            self.install(case, expected=7)
            self.probe(case, "1.0", self.name_a)
            junction.rmdir()
        with self.case("junction-cleanup") as case:
            junction = case["app"] / self.name_a / "foreign"
            self.run(["cmd", "/c", "mklink", "/J", junction, victim])
            unknown = case["app"] / (bundle.PREFIX + uuid.uuid4().hex)
            unknown.mkdir()
            (unknown / "keep.txt").write_text("unowned generation")
            self.install(case)
            assert (victim / "keep.txt").read_text() == "outside installation"
            assert (unknown / "keep.txt").read_text() == "unowned generation"
        assert (victim / "keep.txt").read_text() == "outside installation"
        assert (unknown / "keep.txt").read_text() == "unowned generation"
        self.record("reparse-and-cleanup-ownership", outside_files_preserved=True,
                    unowned_generation_preserved_through_uninstall=True)

    def diskpart(self, name, lines):
        script = self.root / f"{name}.diskpart.txt"
        script.write_text("\n".join(lines) + "\n")
        self.run(["diskpart", "/s", script], timeout=90)

    def qualify_disk(self):
        # Disposable software-only volume. Never fill a runner/system disk.
        volume = Path("Z:\\")
        if volume.exists():
            raise RuntimeError("Qualification drive letter Z is already in use")
        image = self.root / "qualification.vhd"
        try:
            self.diskpart("attach", [f'create vdisk file="{image}" maximum=512 type=expandable',
                         f'select vdisk file="{image}"', "attach vdisk", "create partition primary",
                         'format fs=ntfs quick label="PresspeechQualification"', "assign letter=Z"])
            assert volume.exists(), "Disposable VHD did not mount"
            with self.case("low-space", volume=volume) as case:
                filler = volume / "owned-filler.bin"
                with filler.open("wb") as output:
                    output.truncate(shutil.disk_usage(volume).free - 64 * 1024 * 1024)
                assert shutil.disk_usage(volume).free < 100 * 1024 * 1024
                self.install(case, expected=7)
                self.probe(case, "1.0", self.name_a)
                filler.unlink()
            self.record("real-low-space-admission", old_preserved=True)
            with self.case("disk-full-during-copy", volume=volume) as case:
                filler = volume / "owned-filler.bin"
                with self.checkpoint(case, "payload") as (process, checkpoint):
                    with filler.open("wb") as output:
                        output.truncate(max(0, shutil.disk_usage(volume).free - 4096))
                    assert shutil.disk_usage(volume).free < 1024 * 1024
                    self.resume(process, checkpoint, 5)
                filler.unlink()
                self.probe(case, "1.0", self.name_a)
                self.install(case)
                self.probe(case, "2.0", self.name_b)
            self.record("real-disk-full-copy", old_preserved=True, rerun_recovered=True)
        finally:
            self.diskpart("detach", [f'select vdisk file="{image}"', "detach vdisk"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (sys.platform != "win32" or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"):
        parser.error("Requires a disposable GitHub-hosted Windows runner")
    root = args.output.resolve()
    temp = Path(os.environ["RUNNER_TEMP"]).resolve()
    if root == temp or not root.is_relative_to(temp) or root.exists():
        parser.error("Output must be a new child directory of RUNNER_TEMP")
    root.mkdir()
    UpgradeQualification(root, args.compiler.resolve()).qualify()


if __name__ == "__main__":
    main()

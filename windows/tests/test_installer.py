import unittest
from pathlib import Path


INSTALLER = Path(__file__).resolve().parents[1] / "installer.iss"
SPEC = Path(__file__).resolve().parents[1] / "Presspeech.spec"


class InstallerLifecycleTests(unittest.TestCase):
    def test_windowed_package_disables_unhandled_traceback_dialog(self):
        spec = SPEC.read_text(encoding="utf-8")
        self.assertIn("console=False,", spec)
        self.assertIn("disable_windowed_traceback=True,", spec)
        self.assertNotIn("disable_windowed_traceback=False,", spec)

    @staticmethod
    def setup_directives():
        directives = {}
        in_setup = False
        for raw_line in INSTALLER.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line.startswith("[") and line.endswith("]"):
                in_setup = line.casefold() == "[setup]"
                continue
            if not in_setup or not line or line.startswith(";") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            directives[key.strip().casefold()] = value.strip().casefold()
        return directives

    def test_installer_rejects_unsupported_windows_systems(self):
        directives = self.setup_directives()

        # Presspeech is qualified for Windows 10/11 on native x64 only. Inno's
        # defaults would also admit Windows 7 SP1, while x64compatible includes
        # Arm64 systems running x64 code under emulation.
        self.assertEqual(directives.get("minversion"), "10.0")
        self.assertEqual(directives.get("architecturesallowed"), "x64os")
        self.assertEqual(
            directives.get("architecturesinstallin64bitmode"), "x64os")

    def test_uninstall_owns_the_app_managed_startup_value(self):
        entries = [
            line.strip().casefold()
            for line in INSTALLER.read_text(encoding="utf-8").splitlines()
            if line.strip().casefold().startswith("root: hkcu;")
        ]
        matching = [
            entry for entry in entries
            if 'subkey: "software\\microsoft\\windows\\currentversion\\run"'
            in entry and 'valuename: "presspeech"' in entry
        ]

        self.assertEqual(len(matching), 1)
        entry = matching[0]
        # The app applies the user's Setup/Settings choice. The installer must
        # only register cleanup and must not silently create a login launch.
        self.assertIn("valuetype: none", entry)
        self.assertIn("dontcreatekey", entry)
        self.assertIn("uninsdeletevalue", entry)


if __name__ == "__main__":
    unittest.main()

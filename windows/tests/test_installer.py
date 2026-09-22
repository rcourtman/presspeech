import unittest
from pathlib import Path


INSTALLER = Path(__file__).resolve().parents[1] / "installer.iss"


class InstallerLifecycleTests(unittest.TestCase):
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

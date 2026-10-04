"""앱 계층 - 설정 저장."""
import json
import os
import tempfile
import time
import unittest

from se650.app.settings import DEFAULTS, Settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "settings.json")

    def test_defaults_without_file(self):
        settings = Settings(self.path)
        self.assertEqual(settings["ip"], DEFAULTS["ip"])
        self.assertEqual(settings["language"], "")        # 시스템 언어
        self.assertFalse(os.path.exists(self.path))

    def test_set_persists(self):
        Settings(self.path).set("ip", "10.0.0.5")
        reloaded = Settings(self.path)
        self.assertEqual(reloaded["ip"], "10.0.0.5")
        self.assertEqual(reloaded["audio_source"], DEFAULTS["audio_source"])

    def test_same_value_not_rewritten(self):
        settings = Settings(self.path)
        settings.set("ip", "1.2.3.4")
        modified = os.path.getmtime(self.path)
        time.sleep(0.05)
        settings.set("ip", "1.2.3.4")
        self.assertEqual(os.path.getmtime(self.path), modified)

    def test_corrupt_file_uses_defaults(self):
        with open(self.path, "w") as f:
            f.write("{not json")
        settings = Settings(self.path)
        self.assertEqual(settings["ip"], DEFAULTS["ip"])
        settings.set("ip", "9.9.9.9")
        with open(self.path) as f:
            self.assertEqual(json.load(f)["ip"], "9.9.9.9")

    def test_wrong_types_ignored(self):
        with open(self.path, "w") as f:
            json.dump({"ip": 123, "duration": "abc", "hdmi_out": "PGM"}, f)
        settings = Settings(self.path)
        self.assertEqual((settings["ip"], settings["duration"], settings["hdmi_out"]),
                         (DEFAULTS["ip"], DEFAULTS["duration"], "PGM"))

    def test_unwritable_location_falls_back_to_home(self):
        settings = Settings(os.path.join(self.path, "missing", "dir", "s.json"))
        self.assertTrue(settings.save())
        self.assertTrue(settings.path.startswith(os.path.expanduser("~")))
        os.remove(settings.path)


if __name__ == "__main__":
    unittest.main()

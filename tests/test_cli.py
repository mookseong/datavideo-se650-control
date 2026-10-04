"""앱 계층 - CLI."""
import unittest

from se650 import i18n
from se650.app.cli import run_cli
from se650.app.main import split_language_option

from .helpers import DeviceFixture, wait_for


class CliTests(unittest.TestCase):
    def setUp(self):
        self.fx = DeviceFixture()
        self.output = []

    def tearDown(self):
        self.fx.stop()
        i18n.set_locale("en")

    def run_cli(self, *args):
        return run_cli(["127.0.0.1", *args], switcher_factory=self.fx.factory(), out=self.output.append)

    def test_usage_on_unknown_command(self):
        self.assertEqual(run_cli(["1.2.3.4", "nope"], out=self.output.append), 1)
        self.assertEqual(self.output[0], i18n.tr("app.cli.usage"))

    def test_usage_on_missing_argument(self):
        self.assertEqual(run_cli(["1.2.3.4", "pgm"], out=self.output.append), 1)

    def test_status(self):
        self.assertEqual(self.run_cli("status"), 0)
        self.assertIn("PGM: In 1 | PVW: In 2", self.output[-1])

    def test_pgm(self):
        self.run_cli("pgm", "3")
        self.assertTrue(wait_for(lambda: self.fx.device.state[(2, 86)] == 3))

    def test_keyer(self):
        self.run_cli("key1", "PGM")
        self.assertEqual((self.fx.device.state[(2, 19)], self.fx.device.state[(2, 80)]), (1, 1))
        self.assertIn("key1(PGM=1 PVW=0)", self.output[-1])

    def test_output_follows_language(self):
        i18n.set_locale("ko")
        self.run_cli("status")
        self.assertIn("입력이름", self.output[-1])
        i18n.set_locale("en")
        self.run_cli("status")
        self.assertIn("Input names", self.output[-1])


class LanguageOptionTests(unittest.TestCase):
    def test_split(self):
        self.assertEqual(split_language_option(["--lang", "ko", "1.2.3.4", "cut"]), ("ko", ["1.2.3.4", "cut"]))
        self.assertEqual(split_language_option(["1.2.3.4", "cut", "-l", "en"]), ("en", ["1.2.3.4", "cut"]))
        self.assertEqual(split_language_option(["1.2.3.4", "cut"]), (None, ["1.2.3.4", "cut"]))


if __name__ == "__main__":
    unittest.main()

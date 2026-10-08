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
        self.fx.assert_compliant(self)
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


class CliSafetyTests(unittest.TestCase):
    """검토 12번: 상태 없이 토글하지 않음, 위험/비문서 명령은 명시적 플래그."""

    def setUp(self):
        self.fx = DeviceFixture()
        self.output = []

    def tearDown(self):
        self.fx.assert_compliant(self)
        self.fx.stop()

    def run_cli(self, *args):
        return run_cli(["127.0.0.1", *args], switcher_factory=self.fx.factory(), out=self.output.append)

    def sent_sets(self):
        return [p for p in self.fx.device.commands_received if p[:4] == b"\x01\x00\x00\x00"]

    def test_state_dependent_command_refused_without_state(self):
        self.fx.device.push_on_connect = False
        self.fx.device.ignore_gets = 99
        self.assertEqual(self.run_cli("key1", "pgm"), 3)
        self.assertEqual(self.sent_sets(), [])

    def test_transition_and_memory_refused_without_state(self):
        """상태 없이는 장비가 Ready 인지 확인할 수 없다 (검토2 2번)."""
        self.fx.device.push_on_connect = False
        self.fx.device.ignore_gets = 99
        for command in (["cut"], ["dskcut"], ["load", "1"], ["save", "2"]):
            self.assertEqual(self.run_cli(*command), 3, command)
        self.assertEqual(self.sent_sets(), [])

    def test_menu_needs_explicit_flag(self):
        self.assertEqual(self.run_cli("menu", "DOWN"), 4)
        self.assertEqual(self.fx.device.menu_keys, [])
        self.assertEqual(self.run_cli("menu", "DOWN", "--allow-undocumented"), 0)
        self.assertEqual(self.fx.device.menu_keys, [3])
        self.fx.device.violations.clear()                    # 허용했을 때만 예상된 기록

    def test_save_boot_slot_needs_flag(self):
        self.assertEqual(self.run_cli("save", "0"), 4)
        self.assertEqual(self.sent_sets(), [])
        self.assertEqual(self.run_cli("save", "0", "--confirm-boot-slot"), 0)
        self.assertEqual(len(self.sent_sets()), 1)

    def test_rejected_value_reports_instead_of_crashing(self):
        self.assertEqual(self.run_cli("pgm", "99"), 4)
        self.assertEqual(self.run_cli("save", "1000"), 4)
        self.assertEqual(self.sent_sets(), [])
        self.assertTrue(any("1000" in line for line in self.output))


class LanguageOptionTests(unittest.TestCase):
    def test_split(self):
        self.assertEqual(split_language_option(["--lang", "ko", "1.2.3.4", "cut"]), ("ko", ["1.2.3.4", "cut"]))
        self.assertEqual(split_language_option(["1.2.3.4", "cut", "-l", "en"]), ("en", ["1.2.3.4", "cut"]))
        self.assertEqual(split_language_option(["1.2.3.4", "cut"]), (None, ["1.2.3.4", "cut"]))


if __name__ == "__main__":
    unittest.main()

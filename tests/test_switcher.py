"""제어 계층 - Switcher: 조작 -> 명령 변환, 수신 -> 상태 반영, 초기 상태 동기화."""
import struct
import time
import unittest

from se650.control import Switcher
from se650.i18n import tr
from se650.protocol.constants import Controls

from .helpers import DeviceFixture, RecordingConnection, wait_for


def values_packet(*pairs, command=1):
    body = b"".join(struct.pack("<HHi", c.number, c.section, v) for c, v in pairs)
    return struct.pack("<I", command) + body


class SwitcherUnitTests(unittest.TestCase):
    """RecordingConnection 으로 소켓 없이 검사."""

    def setUp(self):
        self.conn = RecordingConnection()
        self.changes = []
        self.switcher = Switcher(self.conn, on_change=lambda: self.changes.append(1))
        self.switcher.STATE_RETRY_COUNT = 0          # 재시도 스레드는 통합 테스트에서
        self.switcher.connect()
        self.conn.sent.clear()

    def last(self):
        return self.conn.sent[-1]

    # --- 송신
    def test_program_preview(self):
        self.switcher.set_program("In 4")
        self.assertEqual(self.last(), bytes.fromhex("01000000 56000200 04000000"))
        self.switcher.set_preview("Freeze")
        self.assertEqual(self.last(), bytes.fromhex("01000000 57000200 14000000"))

    def test_auto_runs_then_stops_while_running(self):
        self.switcher.auto()
        self.assertEqual(self.last()[-4:], b"\x01\x00\x00\x00")
        self.conn.receive_realtime(values_packet((Controls.ME_STATE, 2)))
        self.switcher.auto()
        self.assertEqual(self.last()[-4:], b"\x00\x00\x00\x00")

    def test_fade_to_black_enables_first(self):
        self.switcher.FTB_ENABLE_DELAY = 0
        self.switcher.fade_to_black()
        self.assertEqual(self.conn.sent[0], bytes.fromhex("01000000 85000200 01000000"))
        self.assertEqual(self.conn.sent[1], bytes.fromhex("01000000 0a000700 01000000"))

    def test_fade_to_black_when_enabled(self):
        self.conn.receive_realtime(values_packet((Controls.FTB_ENABLE, 1)))
        self.switcher.fade_to_black()
        self.assertEqual(len(self.conn.sent), 1)

    def test_durations(self):
        self.switcher.set_duration(45)
        self.assertEqual(self.last(), bytes.fromhex("01000000 03000700 2d000000"))
        self.switcher.set_duration(10, "dsk")
        self.assertEqual(self.last()[4:8], bytes.fromhex("08000700"))

    def test_outputs_audio(self):
        self.switcher.set_hdmi_output("PGM+DSK")
        self.assertEqual(self.last(), bytes.fromhex("01000000 0f000500 02000000"))
        self.switcher.set_audio_source("In 1")
        self.assertEqual(self.last(), bytes.fromhex("01000000 00000600 01000000"))
        self.switcher.set_audio_mode("Analog")
        self.assertEqual(self.last(), bytes.fromhex("01000000 02000600 02000000"))

    # --- 패널 버튼
    def test_still_alternates_still_1_and_2(self):
        self.switcher.press_still("pgm")
        self.assertEqual(self.last(), bytes.fromhex("01000000 56000200 13000000"))       # Still 1 (19)
        self.conn.receive_realtime(values_packet((Controls.PGM_SRC, 19)))
        self.switcher.press_still("pgm")
        self.assertEqual(self.last(), bytes.fromhex("01000000 56000200 14000000"))       # Still 2 (20)
        self.conn.receive_realtime(values_packet((Controls.PST_SRC, 20)))
        self.switcher.press_still("pvw")
        self.assertEqual(self.last(), bytes.fromhex("01000000 57000200 13000000"))

    def test_bkg_alias_is_matte(self):
        self.switcher.set_program("BKG")
        self.assertEqual(self.last()[-4:], struct.pack("<i", 17))

    def test_trans_bg_and_reverse_toggle(self):
        self.switcher.toggle_transition_background()
        self.assertEqual(self.last(), bytes.fromhex("01000000 4f000200 01000000"))
        self.conn.receive_realtime(values_packet((Controls.TRANS_BGND, 1), (Controls.TRANS_REVERSE, 1)))
        self.switcher.toggle_transition_background()
        self.assertEqual(self.last()[-4:], b"\x00\x00\x00\x00")
        self.switcher.toggle_wipe_reverse()
        self.assertEqual(self.last(), bytes.fromhex("01000000 54000200 00000000"))

    def test_wipe_preset(self):
        self.switcher.select_wipe_pattern(2)
        self.assertEqual(self.last(), bytes.fromhex("01000000 00000200 02000000"))

    def test_logo_on_off_sets_pgm_and_pvw(self):
        self.switcher.toggle_logo()
        self.assertEqual(self.last(), bytes.fromhex("01000000 5b000200 01000000 7f000200 00000000"))
        logo = self.switcher.state.keyers["dsk1"]
        self.assertEqual((logo.on, logo.preview), (True, True))
        self.switcher.toggle_logo()
        self.assertEqual(self.last()[4:12], bytes.fromhex("5b000200 00000000"))

    def test_tbar_sends_wipe_level(self):
        self.assertEqual(self.switcher.move_tbar(50), 50)
        self.assertEqual(self.last(), bytes.fromhex("01000000 01000200 00004842"))

    def test_send_when_disconnected_raises(self):
        self.conn.close()
        with self.assertRaises(ConnectionError):
            self.switcher.cut()

    # --- 키어
    def test_keyer_program_press_sends_on_and_flag(self):
        self.switcher.press_keyer("key1", "pgm")
        self.assertEqual(self.last(), bytes.fromhex("01000000 13000200 01000000 50000200 01000000"))
        keyer = self.switcher.state.keyers["key1"]
        self.assertEqual((keyer.on, keyer.preview), (True, False))

    def test_keyer_preview_press_sends_flag_only(self):
        self.switcher.press_keyer("dsk1", "pvw")
        self.assertEqual(self.last(), bytes.fromhex("01000000 7f000200 01000000"))
        self.assertTrue(self.switcher.state.keyers["dsk1"].preview)

    def test_keyer_preview_off_while_on_air(self):
        self.conn.receive_realtime(values_packet((Controls.KEY1_ON, 1), (Controls.TRANS_KEY1, 0)))
        self.switcher.press_keyer("key1", "pvw")
        self.assertEqual(self.last()[4:], bytes.fromhex("50000200 01000000"))
        self.assertFalse(self.switcher.state.keyers["key1"].preview)
        self.assertTrue(self.switcher.state.keyers["key1"].on)

    def test_keyer_press_notifies_immediately(self):
        self.switcher.press_keyer("pip", "pgm")
        self.assertTrue(self.changes)

    def test_keyer_invalid_bus(self):
        with self.assertRaises(ValueError) as ctx:
            self.switcher.press_keyer("key1", "both")
        self.assertEqual(ctx.exception.key, "control.error.invalid_bus")

    # --- 수신
    def test_realtime_values_update_state_and_ready(self):
        self.assertFalse(self.switcher.wait_ready(0))
        self.conn.receive_realtime(values_packet((Controls.PGM_SRC, 3), command=0x1234))
        self.assertEqual(self.switcher.state.pgm, 3)
        self.assertTrue(self.switcher.wait_ready(0))

    def test_non_bus_values_do_not_mark_ready(self):
        self.conn.receive_realtime(values_packet((Controls.ME_DURATION, 20)))
        self.assertFalse(self.switcher.wait_ready(0))

    def test_command_reply_values(self):
        self.conn.receive_command(values_packet((Controls.PST_SRC, 2)))
        self.assertEqual(self.switcher.state.pvw, 2)

    def test_input_names_matched_in_request_order(self):
        self.switcher.request_input_names()
        for name in ("A", "B", "C", "D", "extra"):
            raw = name.encode("utf-16le")
            self.conn.receive_command(struct.pack("<II", 3, len(raw) // 2) + raw)
        self.assertEqual(self.switcher.input_names, {1: "A", 2: "B", 3: "C", 4: "D"})

    def test_refresh_requests_state_and_names(self):
        self.switcher.refresh()
        commands = [struct.unpack_from("<I", p)[0] for p in self.conn.sent]
        self.assertEqual(commands, [0, 9, 9, 9, 9])

    def test_drop_notifies(self):
        self.changes.clear()
        self.conn.drop()
        self.assertTrue(self.changes)
        self.assertFalse(self.switcher.connected)


class SwitcherIntegrationTests(unittest.TestCase):
    """가짜 장비와 실제 TCP 로 연결."""

    def setUp(self):
        self.fx = DeviceFixture(push_on_connect=False)
        self.switcher = self.fx.switcher()

    def tearDown(self):
        self.switcher.close()
        self.fx.stop()

    def connect(self):
        self.switcher.connect()
        self.assertTrue(self.switcher.wait_ready(3.0), "접속 후 상태 미수신")

    def test_state_from_first_realtime_packet(self):
        self.fx.device.push_on_connect = True
        self.fx.device.ignore_gets = 99
        self.connect()
        self.assertEqual((self.switcher.state.pgm, self.switcher.state.pvw), (1, 2))

    def test_no_get_when_first_packet_arrives(self):
        """첫 패킷으로 상태를 받으면 GET 을 보내지 않는다 (늦은 GET 응답이 최신 값을 덮어쓰는 문제 방지)."""
        self.fx.device.push_on_connect = True
        self.connect()
        time.sleep(self.switcher.STATE_RETRY_INTERVAL * 2)
        gets = [p for p in self.fx.device.commands_received if p[:4] == b"\x00\x00\x00\x00" and len(p) > 4]
        self.assertEqual(gets, [])

    def test_state_from_get_when_no_first_packet(self):
        self.connect()
        self.assertEqual(self.switcher.state.me.duration, 30)

    def test_get_retried_when_ignored(self):
        self.fx.device.ignore_gets = 1
        with self.assertLogs("se650", "WARNING") as logs:
            self.connect()
        expected = tr("control.log.state_retry", attempt=1, total=self.switcher.STATE_RETRY_COUNT)
        self.assertTrue(any(expected in line for line in logs.output), logs.output)

    def test_retry_stops_after_ready(self):
        self.connect()
        time.sleep(self.switcher.STATE_RETRY_INTERVAL * 3)
        gets = [p for p in self.fx.device.commands_received if p[:4] == b"\x00\x00\x00\x00" and len(p) > 4]
        self.assertEqual(len(gets), 1)

    def test_gives_up_but_stays_connected(self):
        self.fx.device.ignore_gets = 99
        self.switcher.connect()
        self.assertFalse(self.switcher.wait_ready(1.5))
        self.assertTrue(self.switcher.connected)

    def test_input_names(self):
        self.connect()
        self.assertTrue(wait_for(lambda: len(self.switcher.input_names) == 4))
        self.assertEqual(self.switcher.input_names[4], "노트북")

    def test_cut_swaps_buses(self):
        self.connect()
        self.switcher.cut()
        self.assertTrue(wait_for(lambda: (self.switcher.state.pgm, self.switcher.state.pvw) == (2, 1)))

    def test_keyer_program_only_survives_device_echo(self):
        self.connect()
        self.switcher.press_keyer("key1", "pgm")
        self.assertTrue(wait_for(lambda: self.fx.device.state[(2, 80)] == 1))
        time.sleep(0.15)
        keyer = self.switcher.state.keyers["key1"]
        self.assertEqual((keyer.on, keyer.preview), (True, False))

    def test_keyer_preview_then_cut_goes_on_air(self):
        self.connect()
        self.switcher.press_keyer("key1", "pvw")
        self.switcher.cut()
        self.assertTrue(wait_for(lambda: self.switcher.state.keyers["key1"].on))
        keyer = self.switcher.state.keyers["key1"]
        self.assertEqual((keyer.in_next_transition, keyer.preview), (False, True))

    def test_keyer_program_only_then_cut_goes_off(self):
        self.connect()
        self.switcher.press_keyer("dsk1", "pgm")
        self.assertTrue(wait_for(lambda: self.fx.device.state[(2, 91)] == 1))
        self.switcher.cut()
        self.assertTrue(wait_for(lambda: not self.switcher.state.keyers["dsk1"].on))
        self.assertFalse(self.switcher.state.keyers["dsk1"].preview)

    def test_refresh_pulls_silent_change(self):
        self.connect()
        self.fx.device.state[(2, 86)] = 3
        self.switcher.refresh()
        self.assertTrue(wait_for(lambda: self.switcher.state.pgm == 3))

    def test_device_drop(self):
        self.connect()
        self.fx.stop()
        self.assertTrue(wait_for(lambda: not self.switcher.connected, 3.0))
        with self.assertRaises(ConnectionError):
            self.switcher.set_program(1)


if __name__ == "__main__":
    unittest.main()

"""제어 계층 - Switcher: 조작 -> 명령 변환, 수신 -> 상태 반영, 초기 상태 동기화."""
import struct
import time
import unittest

from se650.control import Switcher
from se650.control.switcher import NotReadyError, UndocumentedCommandError
from se650.i18n import LocalizedError, tr
from se650.protocol.constants import Controls

from .helpers import DeviceFixture, RecordingConnection, wait_for


def values_packet(*pairs, command=1):
    body = b"".join(struct.pack("<HHi", c.number, c.section, v) for c, v in pairs)
    return struct.pack("<I", command) + body


class QuickSwitcher(Switcher):
    """명령 변환만 검사하는 단위 테스트용: Ready 대기 시간 0 (대기 규칙은 SwitcherReadyRuleTests 에서)."""
    ENGINE_MIN_INTERVAL = 0
    MEMORY_MIN_INTERVAL = 0
    STATE_RETRY_COUNT = 0


def name_reply(name):
    raw = name.encode("utf-16le")
    return struct.pack("<II", 3, len(raw) // 2) + raw


class SwitcherUnitTests(unittest.TestCase):
    """RecordingConnection 으로 소켓 없이 검사."""

    def setUp(self):
        self.conn = RecordingConnection()
        self.changes = []
        self.switcher = QuickSwitcher(self.conn, on_change=lambda: self.changes.append(1))
        self.switcher.connect()
        self.answer_input_names()
        self.assertTrue(wait_for(lambda: not self.switcher._names_thread.is_alive()))
        self.conn.sent.clear()

    def tearDown(self):
        self.switcher.close()

    def answer_input_names(self, names=("A", "B", "C", "D")):
        """입력 이름 요청은 하나씩 나간다: 요청이 보이면 답하고 다음 요청을 기다린다."""
        for index, name in enumerate(names, start=1):
            self.assertTrue(wait_for(lambda: self.name_requests() == index), "이름 요청 %d 안 옴" % index)
            self.conn.receive_command(name_reply(name))

    def name_requests(self):
        return sum(1 for p in self.conn.sent if p[:4] == b"\x09\x00\x00\x00")

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
        self.conn.receive_realtime(values_packet((Controls.ME_STATE, 2), (Controls.ME_COMMAND, 8)))
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
        self.conn.receive_realtime(values_packet((Controls.AUDIO_SOURCE, 1)))       # 장비가 반영
        self.switcher.set_audio_mode("Analog")
        self.assertEqual(self.last(), bytes.fromhex("01000000 02000600 02000000"))

    # --- 패널 버튼
    def test_state_starts_at_zero(self):
        """문서 3.2.3: 접속 전 상태 사본은 0. (None 이면 값이 0 인 항목을 영영 못 받는다)"""
        fresh = Switcher(RecordingConnection()).state
        self.assertEqual((fresh.pgm, fresh.pvw, fresh.transition_type, fresh.me.duration), (0, 0, 0, 0))

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

    def test_tbar_rides_on_realtime_reply(self):
        """T-바는 명령 패킷을 보내지 않고, 장비가 기다리는 실시간 답장에 실린다 (3.2.4.2)."""
        self.assertEqual(self.switcher.move_tbar(50), 50)
        self.assertEqual(self.conn.sent, [])
        self.assertEqual(self.conn.next_realtime_reply(), bytes.fromhex("00000000 01000200 00004842"))
        self.assertIsNone(self.conn.next_realtime_reply())               # 다음 답장은 null

    def test_tbar_coalesces_but_never_drops_completion(self):
        for position in (10, 40, 70):
            self.switcher.move_tbar(position)
        self.switcher.move_tbar(100)                                     # 완료 (반드시 전달)
        self.switcher.move_tbar(100 - 15)                                # 반대 방향으로 15%
        replies = [self.conn.next_realtime_reply() for _ in range(3)]
        self.assertEqual(replies[0][-4:], struct.pack("<f", 100.0))      # 70 은 100 으로 대체, 100 은 유지
        self.assertEqual(replies[1][-4:], struct.pack("<f", 15.0))
        self.assertIsNone(replies[2])

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

    def test_any_realtime_packet_marks_ready(self):
        """첫 실시간 패킷은 0 기준 변경분이라 PGM 이 0(Black) 이면 PGM 이 아예 없을 수 있다 (3.2.3)."""
        self.conn.receive_realtime(values_packet((Controls.ME_DURATION, 20)))
        self.assertTrue(self.switcher.wait_ready(0))
        self.assertEqual(self.switcher.state.pgm, 0)

    def test_command_reply_without_bus_does_not_mark_ready(self):
        self.conn.receive_command(values_packet((Controls.ME_DURATION, 20)))
        self.assertFalse(self.switcher.wait_ready(0))

    def test_command_reply_values(self):
        self.conn.receive_command(values_packet((Controls.PST_SRC, 2)))
        self.assertEqual(self.switcher.state.pvw, 2)

    def test_input_names_one_request_at_a_time(self):
        self.assertEqual(self.switcher.input_names, {1: "A", 2: "B", 3: "C", 4: "D"})
        self.conn.sent.clear()
        self.switcher.request_input_names()
        self.assertTrue(wait_for(lambda: self.name_requests() == 1))
        time.sleep(0.1)
        self.assertEqual(self.name_requests(), 1, "응답 전에 다음 요청을 보내면 안 됨")
        self.conn.receive_command(name_reply("X"))
        self.assertTrue(wait_for(lambda: self.name_requests() == 2))

    def test_unanswered_name_request_times_out_and_continues(self):
        self.switcher.NAME_REPLY_TIMEOUT = 0.1
        self.conn.sent.clear()
        self.switcher.request_input_names()
        self.assertTrue(wait_for(lambda: self.name_requests() == 4, 2.0))
        self.assertTrue(wait_for(lambda: not self.switcher._names_thread.is_alive(), 2.0))   # 4번째도 시간 초과
        self.conn.receive_command(name_reply("late"))                     # 포기한 요청의 늦은 응답은 무시
        self.assertNotIn("late", self.switcher.input_names.values())

    def test_refresh_requests_state_and_names(self):
        self.switcher.refresh()
        self.assertTrue(wait_for(lambda: len(self.conn.sent) >= 2))
        self.assertEqual([struct.unpack_from("<I", p)[0] for p in self.conn.sent[:2]], [0, 9])

    def test_refresh_mashing_sends_once_per_second(self):
        for _ in range(5):
            self.switcher.refresh()
        gets = [p for p in self.conn.sent if p[:4] == b"\x00\x00\x00\x00"]
        self.assertEqual(len(gets), 1)

    def test_menu_refused_unless_allowed(self):
        with self.assertRaises(UndocumentedCommandError):
            self.switcher.press_menu("DOWN")
        self.assertEqual(self.conn.sent, [])
        self.switcher.allow_undocumented = True
        self.switcher.press_menu("DOWN")
        self.assertEqual(self.last(), bytes.fromhex("15000000 03000000"))

    def test_analog_mode_rejects_follow_video(self):
        self.conn.receive_realtime(values_packet((Controls.AUDIO_MODE, 2)))
        with self.assertRaises(LocalizedError) as ctx:
            self.switcher.set_audio_source("Follow")
        self.assertEqual(ctx.exception.key, "control.error.analog_needs_input")
        self.switcher.set_audio_source("In 2")
        self.assertEqual(self.last()[-4:], struct.pack("<i", 2))

    def test_analog_mode_rejected_while_following_video(self):
        with self.assertRaises(LocalizedError):
            self.switcher.set_audio_mode("Analog")            # 초기 오디오 소스 0 = 영상 연동
        self.assertEqual(self.conn.sent, [])

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
        self.fx.assert_compliant(self)
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
        self.switcher.press_keyer("key1", "pgm")
        self.assertTrue(wait_for(lambda: self.fx.device.state[(2, 19)] == 1))
        self.switcher.cut()
        self.assertTrue(wait_for(lambda: not self.switcher.state.keyers["key1"].on))
        self.assertFalse(self.switcher.state.keyers["key1"].preview)

    def test_dsk_is_driven_by_dsk_engine_not_me_cut(self):
        """문서 4.1.3.5: DSK1_TRANS_ENABLE 은 'DSK 트랜지션'에 포함 -> M/E CUT 이 아니라 DSK CUT 으로 바뀐다."""
        self.connect()
        self.switcher.press_keyer("dsk1", "pvw")
        self.assertTrue(wait_for(lambda: self.fx.device.state[(2, 127)] == 1))
        self.switcher.cut()
        time.sleep(0.2)
        self.assertFalse(self.switcher.state.keyers["dsk1"].on)
        self.switcher.dsk_cut()
        self.assertTrue(wait_for(lambda: self.switcher.state.keyers["dsk1"].on))

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


class SwitcherReadyRuleTests(unittest.TestCase):
    """문서 4.1.8.1.9 / 4.1.9.2: 장비가 Ready 일 때만 트랜지션/메모리 명령을 보낸다."""

    def setUp(self):
        self.conn = RecordingConnection()
        self.switcher = Switcher(self.conn)
        self.switcher.STATE_RETRY_COUNT = 0
        self.switcher.NAME_REPLY_TIMEOUT = 0.01
        self.switcher.connect()
        wait_for(lambda: self.switcher._names_thread is not None and not self.switcher._names_thread.is_alive())
        self.conn.sent.clear()

    def tearDown(self):
        self.switcher.close()

    def commands(self, control):
        prefix = struct.pack("<HH", control.number, control.section)
        return [p for p in self.conn.sent if prefix in p]

    def test_cut_mashing_sends_one_command(self):
        self.switcher.cut()
        for _ in range(5):
            with self.assertRaises(NotReadyError):
                self.switcher.cut()
        self.assertEqual(len(self.commands(Controls.ME_COMMAND)), 1)

    def test_next_command_after_device_reports_ready(self):
        self.switcher.cut()
        self.conn.receive_realtime(values_packet((Controls.ME_COMMAND, 5)))      # 아직 처리 전
        time.sleep(self.switcher.ENGINE_MIN_INTERVAL + 0.02)
        with self.assertRaises(NotReadyError):
            self.switcher.cut()
        self.conn.receive_realtime(values_packet((Controls.ME_COMMAND, 8)))      # Ready
        self.switcher.cut()
        self.assertEqual(len(self.commands(Controls.ME_COMMAND)), 2)

    def test_engines_are_independent(self):
        self.switcher.cut()
        self.switcher.dsk_cut()                                                  # 다른 엔진은 막히지 않음
        self.assertEqual(len(self.commands(Controls.DSK_COMMAND)), 1)

    def test_memory_busy_blocks_next_command(self):
        self.switcher.load_memory(1)
        self.conn.receive_realtime(values_packet((Controls.MEMORY_COMMAND, 1), (Controls.MEMORY_STATE, 1)))
        time.sleep(self.switcher.MEMORY_MIN_INTERVAL + 0.02)
        with self.assertRaises(NotReadyError) as ctx:
            self.switcher.load_memory(2)
        self.assertEqual(ctx.exception.key, "control.error.memory_busy")
        self.conn.receive_realtime(values_packet((Controls.MEMORY_COMMAND, 0), (Controls.MEMORY_STATE, 0)))
        self.switcher.load_memory(2)
        self.assertEqual(len(self.commands(Controls.MEMORY_COMMAND)), 2)

    def test_memory_result_reported(self):
        self.switcher.load_memory(7)
        with self.assertLogs("se650.control", "WARNING") as logs:
            self.conn.receive_realtime(values_packet((Controls.MEMORY_COMMAND, 1), (Controls.MEMORY_STATE, 1)))
            self.conn.receive_realtime(values_packet((Controls.MEMORY_RESULT, 2), (Controls.MEMORY_STATE, 0),
                                                     (Controls.MEMORY_COMMAND, 0)))
        self.assertIn(tr("control.log.memory_not_found", slot=7, operation=tr("control.log.memory_load")),
                      logs.output[0])


if __name__ == "__main__":
    unittest.main()

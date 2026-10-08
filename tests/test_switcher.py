"""제어 계층 - Switcher: 조작 -> 명령 변환, 수신 -> 상태 반영, 초기 상태 동기화."""
import struct
import time
import unittest

from se650.control import Switcher
from se650.control.switcher import BootSlotError, NotReadyError, UndocumentedCommandError
from se650.i18n import LocalizedError, tr
from se650.control.state import STATE_CONTROLS
from se650.protocol.constants import Controls

from .helpers import DeviceFixture, RecordingConnection, wait_for


def values_packet(*pairs, command=1):
    body = b"".join(struct.pack("<HHi", c.number, c.section, v) for c, v in pairs)
    return struct.pack("<I", command) + body


class QuickSwitcher(Switcher):
    """명령 변환만 검사하는 단위 테스트용: 처리 확인을 빨리 (확인 규칙 자체는 SwitcherReadyRuleTests 에서)."""
    ENGINE_CHECKS = (0.0, 0.05, 10)
    MEMORY_CHECKS = (0.0, 0.05, 10)
    STATE_RETRY_COUNT = 0
    MIN_SEND_GAP = 0
    DUPLICATE_WINDOW = 0


def answer_next_get(conn, *pairs, timeout=2.0):
    """클라이언트가 보낸 다음 GET 에 장비처럼 응답한다 (명령 뒤 GET = 처리 확인)."""
    seen = sum(1 for p in conn.sent if p[:4] == b"\x00\x00\x00\x00")
    assert wait_for(lambda: sum(1 for p in conn.sent if p[:4] == b"\x00\x00\x00\x00") > seen, timeout), "GET 안 옴"
    conn.receive_command(values_packet(*pairs))


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
        self.conn.receive_realtime(values_packet((Controls.ME_DURATION, 30)))   # 첫 상태 패킷
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
        self.assertEqual(self.conn.sent[-1][-4:], b"\x01\x00\x00\x00")
        answer_next_get(self.conn, (Controls.ME_COMMAND, 8), (Controls.ME_STATE, 2))   # 처리됨, 진행 중
        self.assertTrue(wait_for(lambda: not self.switcher.is_busy("me")))
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
    def fresh_switcher(self):
        """아직 상태를 받지 않은 새 Switcher (setUp 의 것은 첫 상태 패킷을 이미 받았다)."""
        conn = RecordingConnection()
        switcher = QuickSwitcher(conn)
        switcher.NAME_REPLY_TIMEOUT = 0.01
        switcher.connect()
        self.addCleanup(switcher.close)
        return conn, switcher

    def test_realtime_values_update_state_and_ready(self):
        conn, switcher = self.fresh_switcher()
        self.assertFalse(switcher.wait_ready(0))
        conn.receive_realtime(values_packet((Controls.PGM_SRC, 3), command=0x1234))
        self.assertEqual(switcher.state.pgm, 3)
        self.assertTrue(switcher.wait_ready(0))

    def test_any_realtime_packet_marks_ready(self):
        """첫 실시간 패킷은 0 기준 변경분이라 PGM 이 0(Black) 이면 PGM 이 아예 없을 수 있다 (3.2.3)."""
        conn, switcher = self.fresh_switcher()
        conn.receive_realtime(values_packet((Controls.ME_DURATION, 20)))
        self.assertTrue(switcher.wait_ready(0))
        self.assertEqual(switcher.state.pgm, 0)

    def test_command_reply_without_bus_does_not_mark_ready(self):
        conn, switcher = self.fresh_switcher()
        conn.receive_command(values_packet((Controls.ME_DURATION, 20)))
        self.assertFalse(switcher.wait_ready(0))

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

    def test_late_name_reply_discarded_on_next_pass(self):
        """포기한 요청의 응답이 다음 새로고침 중에 도착해도 다른 입력 이름으로 저장되지 않는다."""
        self.switcher.NAME_REPLY_TIMEOUT = 0.1
        self.switcher.input_names.clear()
        self.switcher.request_input_names()
        self.assertTrue(wait_for(lambda: not self.switcher._names_thread.is_alive(), 2.0))
        self.switcher.request_input_names()                                     # 다시 입력 1 요청
        self.conn.receive_command(name_reply("OLD-late"))                       # 이전 요청의 늦은 응답
        self.conn.receive_command(name_reply("NEW-1"))
        self.assertTrue(wait_for(lambda: self.switcher.input_names.get(1) == "NEW-1"))
        self.assertNotIn("OLD-late", self.switcher.input_names.values())

    def test_unanswered_name_request_stops_remaining_requests(self):
        """응답이 없으면 남은 요청을 멈춘다: 늦게 온 응답이 다음 입력의 이름으로 저장되지 않게 (검토 8번)."""
        self.switcher.NAME_REPLY_TIMEOUT = 0.1
        self.switcher.input_names.clear()
        self.conn.sent.clear()
        self.switcher.request_input_names()
        self.assertTrue(wait_for(lambda: not self.switcher._names_thread.is_alive(), 2.0))
        self.assertEqual(self.name_requests(), 1)
        self.conn.receive_command(name_reply("late"))                     # 포기한 요청의 늦은 응답은 무시
        self.assertEqual(self.switcher.input_names, {})

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

    def test_invisible_engine_processing_confirmed_end_to_end(self):
        """시뮬레이터는 문서대로 '바뀐 값만' 보내므로 8->5->8 이 안 보인다. 클라이언트가 GET 으로 확인 후 다음 CUT."""
        self.connect()
        self.switcher.cut()
        self.assertTrue(wait_for(lambda: not self.switcher.is_busy("me"), 2))
        self.switcher.cut()
        self.assertTrue(wait_for(lambda: (self.switcher.state.pgm, self.switcher.state.pvw) == (1, 2)))
        gets = [p for p in self.fx.device.commands_received if p[:4] == b"\x00\x00\x00\x00"]
        self.assertLessEqual(len(gets), 4)                       # 명령당 확인 GET 1~2개

    def test_slow_engine_still_never_overlaps(self):
        self.fx.device.engine_fields = 25                        # 0.5초 걸리는 장비
        self.connect()
        self.switcher.cut()
        refused = 0
        deadline = time.time() + 1.5
        while time.time() < deadline:
            try:
                self.switcher.cut()
                break
            except NotReadyError:
                refused += 1
                time.sleep(0.05)
        self.assertGreater(refused, 3)

    def test_memory_failure_reported(self):
        self.fx.device.memory_fails = True
        self.connect()
        with self.assertLogs("se650.control", "WARNING") as logs:
            self.switcher.load_memory(1)
            self.assertTrue(wait_for(lambda: not self.switcher.is_busy("memory"), 3))
        self.assertTrue(any(tr("control.log.memory_failed", slot=1, operation=tr("control.log.memory_load"),
                                result=1) in line for line in logs.output), logs.output)

    def test_tbar_ignored_while_auto_running_and_cleared_by_cut(self):
        self.fx.device.auto_fields = 50
        self.connect()
        self.switcher.move_tbar(30)                              # 대기열에 T-바 값
        self.switcher.cut()                                      # CUT 직전 대기열 비움
        self.assertTrue(wait_for(lambda: not self.switcher.is_busy("me"), 2))
        self.assertEqual(self.fx.device.wipe_levels, [])
        self.switcher.auto()
        self.assertTrue(wait_for(lambda: self.switcher.state.me.running, 2))
        self.assertIsNone(self.switcher.move_tbar(60))           # 진행 중에는 무시

    def test_device_that_echoes_sets_stays_compliant(self):
        """문서는 SET 응답 여부를 정하지 않았다. 응답하는 장비여도 확인이 꼬이거나 명령이 겹치지 않아야 한다."""
        self.fx.device.echo_sets = True
        self.connect()
        for _ in range(3):
            self.switcher.cut()
            self.assertTrue(wait_for(lambda: not self.switcher.is_busy("me"), 3))
        self.switcher.load_memory(2)
        self.assertTrue(wait_for(lambda: not self.switcher.is_busy("memory"), 3))

    def test_slow_get_replies_still_confirm_without_overlap(self):
        self.fx.device.get_reply_delay = 0.3
        self.connect()
        for _ in range(2):
            self.switcher.cut()
            self.assertTrue(wait_for(lambda: not self.switcher.is_busy("me"), 4))

    def test_short_realtime_pause_keeps_connection(self):
        self.connect()
        self.fx.device.pause_realtime(1.5)
        time.sleep(2.0)
        self.assertTrue(self.switcher.connected)

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
    """문서 4.1.8.1.9 / 4.1.9.2: 보낸 명령의 처리가 확인될 때까지 같은 엔진/메모리 명령을 보내지 않는다.
    시간이 지났다고 그냥 보내지도 않는다 (검토 3번)."""

    def setUp(self):
        self.conn = RecordingConnection()
        self.switcher = Switcher(self.conn)
        self.switcher.STATE_RETRY_COUNT = 0
        self.switcher.NAME_REPLY_TIMEOUT = 0.01
        self.switcher.connect()
        self.conn.receive_realtime(values_packet((Controls.ME_DURATION, 30)))   # 첫 상태 패킷
        wait_for(lambda: self.switcher._names_thread is not None and not self.switcher._names_thread.is_alive())
        self.conn.sent.clear()

    def tearDown(self):
        self.switcher.close()

    def commands(self, control):
        prefix = struct.pack("<HH", control.number, control.section)
        return [p for p in self.conn.sent if prefix in p and p[:4] == b"\x01\x00\x00\x00"]

    def gets(self):
        return [p for p in self.conn.sent if p[:4] == b"\x00\x00\x00\x00"]

    def test_cut_mashing_sends_one_command(self):
        self.switcher.cut()
        for _ in range(5):
            with self.assertRaises(NotReadyError):
                self.switcher.cut()
        self.assertEqual(len(self.commands(Controls.ME_COMMAND)), 1)

    def test_working_then_ready_on_realtime_confirms(self):
        self.switcher.cut()
        self.conn.receive_realtime(values_packet((Controls.ME_COMMAND, 5)))      # 처리 중이 보임
        with self.assertRaises(NotReadyError):
            self.switcher.cut()
        self.conn.receive_realtime(values_packet((Controls.ME_COMMAND, 8)))      # Ready
        self.switcher.cut()
        self.assertEqual(len(self.commands(Controls.ME_COMMAND)), 2)

    def test_invisible_processing_confirmed_by_get(self):
        """한 필드 안에 처리되면 실시간으로 안 보인다 -> 클라이언트가 GET 으로 확인하고, 그 응답으로 풀린다."""
        self.switcher.cut()
        answer_next_get(self.conn, (Controls.ME_COMMAND, 8), (Controls.ME_STATE, 0))
        self.assertTrue(wait_for(lambda: not self.switcher.is_busy("me")))
        self.switcher.cut()
        self.assertEqual(len(self.commands(Controls.ME_COMMAND)), 2)

    def test_stale_ready_before_command_is_not_trusted(self):
        """명령 전에 보낸 GET 의 응답(명령 이전 상태)은 처리 확인으로 쓰지 않는다."""
        self.switcher.request_state()                                           # 명령 전 GET
        self.switcher.cut()
        self.conn.receive_command(values_packet((Controls.ME_COMMAND, 8)))      # 그 GET 의 응답
        self.assertTrue(self.switcher.is_busy("me"))

    def test_stale_get_reply_after_realtime_working_does_not_confirm(self):
        """도착 순서 역전: 실시간으로 '처리 중'이 먼저 오고, 명령 전 GET 의 응답(Ready)이 나중에 와도 완료가 아니다.
        (연타 측정에서 실제로 메모리 명령이 겹친 경로)"""
        self.switcher.request_state()                                           # 명령 전 GET
        self.switcher.load_memory(1)
        self.conn.receive_realtime(values_packet((Controls.MEMORY_COMMAND, 1), (Controls.MEMORY_STATE, 1)))
        self.conn.receive_command(values_packet((Controls.MEMORY_COMMAND, 0), (Controls.MEMORY_STATE, 0)))
        self.assertTrue(self.switcher.is_busy("memory"))
        with self.assertRaises(NotReadyError):
            self.switcher.load_memory(2)

    def test_late_pre_command_get_reply_never_confirms(self):
        """늦게 온 명령 전 GET 응답은 컨트롤 목록으로 자기 GET 과 짝지어져 '명령 이전'으로 판정된다
        (시간이 지나도 다른 GET 의 짝이 되지 않는다)."""
        self.switcher.request_state()                                           # 전체 상태 GET
        self.switcher.cut()
        assert wait_for(lambda: len(self.gets()) == 2)                         # 처리 확인 GET 도 나감
        time.sleep(0.3)
        state_reply = [(c, 8 if c == Controls.ME_COMMAND else 0) for c in STATE_CONTROLS]
        self.conn.receive_command(values_packet(*state_reply))                  # 늦게 온 전체 상태 응답
        self.assertTrue(self.switcher.is_busy("me"))

    def test_unmatched_reply_is_not_used_for_confirmation(self):
        self.switcher.cut()
        self.conn.receive_realtime(values_packet((Controls.ME_COMMAND, 5)))      # 처리 중
        with self.switcher._gets_lock:
            self.switcher._outstanding_gets.clear()                             # 짝지을 GET 이 없음
        self.conn.receive_command(values_packet((Controls.ME_COMMAND, 8)))      # 정체불명의 응답 (예: SET 메아리)
        self.assertTrue(self.switcher.is_busy("me"))

    def test_previous_commands_check_reply_does_not_confirm_new_command(self):
        self.switcher.cut()
        assert wait_for(lambda: len(self.gets()) == 1)                         # 첫 CUT 의 확인 GET (응답 보류)
        self.conn.receive_realtime(values_packet((Controls.ME_COMMAND, 5)))
        self.conn.receive_realtime(values_packet((Controls.ME_COMMAND, 8)))      # 실시간으로 첫 CUT 완료
        self.switcher.cut()                                                     # 두 번째 CUT
        self.conn.receive_command(values_packet((Controls.ME_COMMAND, 8), (Controls.ME_STATE, 0)))  # 첫 확인 GET 응답
        self.assertTrue(self.switcher.is_busy("me"))

    def test_stale_get_values_do_not_overwrite_fresher_realtime(self):
        self.switcher.request_state()
        self.conn.receive_realtime(values_packet((Controls.PGM_SRC, 3)))         # GET 이후 실시간 갱신
        self.conn.receive_command(values_packet((Controls.PGM_SRC, 1), (Controls.PST_SRC, 4)))
        self.assertEqual(self.switcher.state.pgm, 3)                            # 더 최신 값 유지
        self.assertEqual(self.switcher.state.pvw, 4)                            # 실시간 갱신이 없던 값은 반영

    def test_stale_get_cannot_unblock_memory_through_merged_state(self):
        """오래된 GET 값이 상태를 덮어쓴 뒤, RESULT 만 든 실시간 패킷으로 완료 처리되던 경로."""
        self.switcher.request_state()
        self.switcher.load_memory(1)
        self.conn.receive_realtime(values_packet((Controls.MEMORY_COMMAND, 1), (Controls.MEMORY_STATE, 1)))
        self.conn.receive_command(values_packet(*[(c, 0) for c in STATE_CONTROLS]))
        self.conn.receive_realtime(values_packet((Controls.MEMORY_RESULT, 0)))
        self.assertTrue(self.switcher.is_busy("memory"))
        self.assertEqual(self.switcher.state.memory.state, 1)

    def test_tracked_commands_refused_before_state(self):
        fresh = Switcher(RecordingConnection())
        fresh.STATE_RETRY_COUNT = 0
        fresh.NAME_REPLY_TIMEOUT = 0.01
        fresh.connect()
        try:
            for action in (fresh.cut, fresh.auto, fresh.dsk_cut, lambda: fresh.load_memory(1)):
                with self.assertRaises(NotReadyError) as ctx:
                    action()
                self.assertEqual(ctx.exception.key, "control.error.no_state")
        finally:
            fresh.close()

    def test_never_sends_while_unconfirmed_even_after_timeout(self):
        self.switcher.ENGINE_CHECKS = (0.0, 0.02, 3)
        self.switcher._trackers["me"] = type(self.switcher._trackers["me"])(*self.switcher.ENGINE_CHECKS)
        self.switcher.cut()
        with self.assertLogs("se650.control", "ERROR"):
            self.assertTrue(wait_for(lambda: self.switcher._trackers["me"].gave_up, 2.0))
            time.sleep(0.1)
        with self.assertRaises(NotReadyError):
            self.switcher.cut()
        self.assertEqual(len(self.commands(Controls.ME_COMMAND)), 1)
        self.assertLessEqual(len(self.gets()), 3)                                # 확인 GET 은 정해진 횟수만

    def test_busy_from_other_controller_blocks(self):
        self.conn.receive_realtime(values_packet((Controls.ME_COMMAND, 5)))      # 패널이 보낸 명령 처리 중
        with self.assertRaises(NotReadyError):
            self.switcher.cut()
        self.assertEqual(self.commands(Controls.ME_COMMAND), [])

    def test_engines_are_independent(self):
        self.switcher.cut()
        self.switcher.dsk_cut()                                                  # 다른 엔진은 막히지 않음
        self.assertEqual(len(self.commands(Controls.DSK_COMMAND)), 1)

    def test_memory_busy_blocks_next_command(self):
        self.switcher.load_memory(1)
        self.conn.receive_realtime(values_packet((Controls.MEMORY_COMMAND, 1), (Controls.MEMORY_STATE, 1)))
        with self.assertRaises(NotReadyError) as ctx:
            self.switcher.load_memory(2)
        self.assertEqual(ctx.exception.key, "control.error.memory_busy")
        self.conn.receive_realtime(values_packet((Controls.MEMORY_COMMAND, 0), (Controls.MEMORY_STATE, 0)))
        self.switcher.load_memory(2)
        self.assertEqual(len(self.commands(Controls.MEMORY_COMMAND)), 2)

    def test_memory_result_only_after_command_processed(self):
        """처리 시작 전에 온 이전 결과(OK)를 이번 결과로 보고하지 않는다 (검토 10번)."""
        self.switcher.load_memory(7)
        self.conn.receive_realtime(values_packet((Controls.MEMORY_RESULT, 0), (Controls.MEMORY_STATE, 0)))
        self.assertTrue(self.switcher.is_busy("memory"))
        with self.assertLogs("se650.control", "WARNING") as logs:
            self.conn.receive_realtime(values_packet((Controls.MEMORY_COMMAND, 1), (Controls.MEMORY_STATE, 1)))
            self.conn.receive_realtime(values_packet((Controls.MEMORY_RESULT, 2), (Controls.MEMORY_STATE, 0),
                                                     (Controls.MEMORY_COMMAND, 0)))
        self.assertIn(tr("control.log.memory_not_found", slot=7, operation=tr("control.log.memory_load")),
                      logs.output[0])

    def test_memory_slot_range(self):
        """1000 은 시스템 메모리(부팅 시 영상 규격 등), 음수/1000 이상은 보내지 않는다 (검토 2번)."""
        for slot in (1000, 1001, -1):
            with self.assertRaises(ValueError):
                self.switcher.store_memory(slot)
            with self.assertRaises(ValueError):
                self.switcher.load_memory(slot)
        self.assertEqual(self.commands(Controls.MEMORY_COMMAND), [])

    def test_boot_slot_needs_confirmation(self):
        with self.assertRaises(BootSlotError):
            self.switcher.store_memory(0)
        self.assertEqual(self.commands(Controls.MEMORY_COMMAND), [])
        self.switcher.store_memory(0, confirm_boot_slot=True)
        self.assertEqual(len(self.commands(Controls.MEMORY_COMMAND)), 1)


class ValueValidationTests(unittest.TestCase):
    """문서에 정해진 값/범위만 보낸다 (검토 5번). 거부하면 아무것도 보내지 않는다."""

    def setUp(self):
        self.conn = RecordingConnection()
        self.switcher = QuickSwitcher(self.conn)
        self.switcher.NAME_REPLY_TIMEOUT = 0.01
        self.switcher.connect()
        self.conn.receive_realtime(values_packet((Controls.ME_DURATION, 30)))   # 첫 상태 패킷
        wait_for(lambda: not self.switcher._names_thread.is_alive())
        self.conn.sent.clear()

    def tearDown(self):
        self.switcher.close()

    def test_rejected_values_send_nothing(self):
        cases = [
            lambda: self.switcher.set_program(99), lambda: self.switcher.set_program(5),
            lambda: self.switcher.set_preview(18), lambda: self.switcher.set_program("16"),
            lambda: self.switcher.select_wipe_pattern(0), lambda: self.switcher.select_wipe_pattern(33),
            lambda: self.switcher.set_transition_type(2), lambda: self.switcher.set_hdmi_output(9),
            lambda: self.switcher.set_audio_mode(3), lambda: self.switcher.set_keyer_source("key1", 18),
            lambda: self.switcher.set_duration(0), lambda: self.switcher.set_duration(-5),
            lambda: self.switcher.set_duration(1000000),
        ]
        for index, case in enumerate(cases):
            with self.assertRaises(ValueError, msg="case %d" % index):
                case()
        self.assertEqual(self.conn.sent, [])

    def test_documented_values_accepted(self):
        for case in (lambda: self.switcher.set_program(17), lambda: self.switcher.set_preview("20"),
                     lambda: self.switcher.select_wipe_pattern(32), lambda: self.switcher.set_duration(999),
                     lambda: self.switcher.set_hdmi_output(4)):
            case()
        self.assertEqual(len(self.conn.sent), 5)


class PacingTests(unittest.TestCase):
    """명령 채널 패킷 사이 최소 간격, 같은 값 반복 억제 (검토 6번)."""

    def setUp(self):
        self.conn = RecordingConnection()
        self.switcher = Switcher(self.conn)
        self.switcher.STATE_RETRY_COUNT = 0
        self.switcher.NAME_REPLY_TIMEOUT = 0.01
        self.switcher.connect()
        self.conn.receive_realtime(values_packet((Controls.ME_DURATION, 30)))   # 첫 상태 패킷
        wait_for(lambda: not self.switcher._names_thread.is_alive())
        self.conn.sent.clear()
        self.times = []
        original = self.conn.send_command

        def timed(payload):
            self.times.append(time.monotonic())
            original(payload)
        self.conn.send_command = timed

    def tearDown(self):
        self.switcher.close()

    def test_key_repeat_of_same_value_sends_once(self):
        for _ in range(30):                                   # 키를 누르고 있을 때의 자동 반복
            self.switcher.set_preview(3)
        self.assertEqual(len(self.conn.sent), 1)

    def test_minimum_gap_between_packets(self):
        for source in (1, 2, 3, 4, 1, 2):
            self.switcher.set_preview(source)
        gaps = [b - a for a, b in zip(self.times, self.times[1:])]
        self.assertTrue(all(gap >= self.switcher.MIN_SEND_GAP * 0.9 for gap in gaps), gaps)


if __name__ == "__main__":
    unittest.main()

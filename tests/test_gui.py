"""앱 계층 - GUI. tkinter 와 디스플레이가 필요 (헤드리스 리눅스: xvfb-run)."""
import gc
import json
import os
import tempfile
import time
import unittest

from .helpers import DeviceFixture, wait_for

try:
    import tkinter as tk
    tk.Tk().destroy()
    HAVE_DISPLAY = True
except Exception:       # noqa: BLE001
    HAVE_DISPLAY = False

from se650.i18n import set_locale, tr

if HAVE_DISPLAY:
    from se650.app.gui import MainWindow
    from se650.app.gui import theme
    from se650.app.settings import Settings


class FakeKeyEvent:
    def __init__(self, keysym, widget):
        self.keysym, self.widget = keysym, widget


@unittest.skipUnless(HAVE_DISPLAY, "tkinter/디스플레이 없음")
class GuiTests(unittest.TestCase):
    def setUp(self):
        self.fx = DeviceFixture(push_on_connect=False)
        self.settings = Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        self.settings.set("language", "en")
        self.root = tk.Tk()
        self.window = MainWindow(self.root, settings=self.settings, switcher_factory=self.fx.factory())
        self.window.connection_bar.ip_var.set("127.0.0.1")

    def tearDown(self):
        self.fx.assert_compliant(self)          # 모든 GUI 테스트: 문서 규칙 위반 0건
        try:
            self.window.stop()
            self.root.destroy()
        except tk.TclError:
            pass
        self.fx.stop()
        set_locale("en")
        del self.window, self.root
        gc.collect()        # tk 변수는 메인 스레드에서 수거해야 한다 (다른 스레드 GC 시 블로킹)

    # ---------------------------------------------------------------- helpers
    def pump_until(self, condition, timeout=3.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.root.update()
            if condition():
                return True
            time.sleep(0.02)
        return condition()

    def pump(self, seconds):
        self.pump_until(lambda: False, seconds)

    def lit(self, bus):
        """켜진 소스 버튼 키 목록 ('bkg', '1'..'4', 'still')"""
        color = theme.PGM if bus == "pgm" else theme.PVW
        return [k for k, b in self.window.device.sources.buttons[bus].items() if theme.is_lit(b, color)]

    def keyer_lit(self, keyer, bus):
        color = theme.PGM if bus == "pgm" else theme.PVW
        return theme.is_lit(self.window.device.keyers.buttons[(keyer, bus)], color)

    def group_lit(self, group, key, color):
        return theme.is_lit(getattr(self.window.device, group).buttons[key], color)

    def connect(self):
        self.window.connect()
        self.assertTrue(self.pump_until(lambda: self.lit("pgm") == ["1"] and self.lit("pvw") == ["2"]),
                        "연결 후 상태 미표시")

    def log_text(self):
        return self.window.log_box.get("1.0", "end")

    # ---------------------------------------------------------------- tests
    def test_state_shown_right_after_connect(self):
        self.connect()
        self.assertEqual(self.window.connection_bar.status_var.get(), tr("app.status.connected", host="127.0.0.1"))
        self.assertTrue(self.pump_until(lambda: self.window.device.sources.stickers[3].cget("text") == "PC"))
        self.assertEqual(self.window.device.sources.stickers[3].cget("bg"), theme.STICKER)

    def test_state_shown_from_first_realtime_packet(self):
        self.fx.device.push_on_connect = True
        self.fx.device.ignore_gets = 99
        self.connect()

    def test_ip_saved_on_connect(self):
        self.connect()
        with open(self.settings.path) as f:
            self.assertEqual(json.load(f)["ip"], "127.0.0.1")

    def test_failed_connect(self):
        self.fx.stop()
        self.window.connect()
        self.assertTrue(self.pump_until(lambda: self.window.connection_bar.status_var.get() == tr("app.status.failed"),
                                        5.0))
        self.assertIsNone(self.window.switcher)
        self.assertEqual(str(self.window.connection_bar.connect_button.cget("state")), "normal")

    def test_settings_restored(self):
        self.settings.set("ip", "10.1.1.1")
        self.settings.set("duration", 7)
        self.settings.set("hdmi_out", "PGM")
        root = tk.Tk()
        window = MainWindow(root, settings=Settings(self.settings.path))
        try:
            self.assertEqual(window.connection_bar.ip, "10.1.1.1")
            self.assertEqual(window.extras.duration_var.get(), 7)
            self.assertEqual(window.extras.hdmi.value, "PGM")
        finally:
            window.stop()
            root.destroy()
            del window, root
            gc.collect()

    def test_bus_button(self):
        self.connect()
        self.window.device.sources.buttons["pvw"]["4"].invoke()
        self.assertTrue(self.pump_until(lambda: self.lit("pvw") == ["4"]))
        self.assertEqual(self.fx.device.state[(2, 87)], 4)

    def test_keyer_program_lights_program_only(self):
        self.connect()
        self.window.device.keyers.buttons[("key1", "pgm")].invoke()
        self.assertTrue(self.pump_until(lambda: self.keyer_lit("key1", "pgm")))
        self.pump(0.3)
        self.assertFalse(self.keyer_lit("key1", "pvw"))

    def test_keyer_preview_then_cut(self):
        self.connect()
        self.window.device.keyers.buttons[("key1", "pvw")].invoke()
        self.assertTrue(self.pump_until(lambda: self.keyer_lit("key1", "pvw")))
        self.assertFalse(self.keyer_lit("key1", "pgm"))
        self.window.run(lambda sw: sw.cut())
        self.assertTrue(self.pump_until(lambda: self.keyer_lit("key1", "pgm") and self.keyer_lit("key1", "pvw")))

    def test_click_before_connect(self):
        self.window.device.sources.buttons["pgm"]["2"].invoke()
        self.pump(0.2)
        self.assertIn(tr("app.message.connect_first"), self.log_text())

    def test_disconnect_and_reconnect(self):
        self.connect()
        self.window.toggle_connection()
        self.pump(0.2)
        self.assertEqual(self.lit("pgm"), [])
        self.assertEqual(self.window.connection_bar.connect_button.cget("text"), tr("app.connection.connect"))
        self.connect()

    # ---------------------------------------------------------------- 연결 누수 (검토 1번)
    def press_enter_in_ip(self, times):
        entry = [w for w in self.window.connection_bar.winfo_children() if w.winfo_class() == "TEntry"][0]
        self.root.update()
        entry.focus_force()
        self.root.update()
        for _ in range(times):
            entry.event_generate("<Return>", when="now")
            self.root.update()
        self.assertNotEqual(self.window._status[0], "disconnected", "Enter 가 전달되지 않음 (테스트 오류)")

    def test_enter_mashing_while_connecting_opens_one_connection(self):
        self.press_enter_in_ip(4)
        self.assertTrue(self.pump_until(lambda: self.window._status[0] == "connected", 4))
        self.pump(0.8)
        self.assertEqual(self.fx.device.open_sessions, 1)
        self.assertEqual(self.fx.device.realtime_connections, 1)

    def test_enter_mashing_during_reconnect_delay_opens_one_connection(self):
        self.connect()
        self.window.toggle_connection()
        self.press_enter_in_ip(4)
        self.assertTrue(self.pump_until(lambda: self.window._status[0] == "connected", 5))
        self.pump(0.8)
        self.assertEqual(self.fx.device.open_sessions, 1)
        self.assertEqual(self.fx.device.realtime_connections, 2)        # 처음 + 재연결 1번

    def test_enter_while_connected_does_not_disconnect(self):
        self.connect()
        self.press_enter_in_ip(3)
        self.pump(0.3)
        self.assertEqual(self.window._status[0], "connected")
        self.assertEqual(self.fx.device.realtime_connections, 1)

    def test_disconnect_closes_device_session(self):
        self.connect()
        self.window.toggle_connection()
        self.assertTrue(wait_for(lambda: self.fx.device.open_sessions == 0, 2))

    # ---------------------------------------------------------------- 키보드 (검토 6번)
    def pvw_commands(self):
        return [p for p in self.fx.device.commands_received if p[4:8] == bytes.fromhex("57000200")]

    def test_held_key_sends_once(self):
        """키를 누르고 있으면 OS 가 자동 반복을 보낸다 -> 한 번만 보낸다 (Windows 식: 놓기 없이 반복)."""
        self.connect()
        before = len(self.pvw_commands())
        widget = self.window.device.cut_auto.auto_button
        for _ in range(15):
            self.window._on_key(FakeKeyEvent("3", widget))
        self.pump(0.3)
        self.assertEqual(len(self.pvw_commands()) - before, 1)

    def test_x11_style_autorepeat_ignored_but_real_presses_count(self):
        self.connect()
        before = len(self.pvw_commands())
        widget = self.window.device.cut_auto.auto_button
        self.window._on_key(FakeKeyEvent("4", widget))
        for _ in range(5):                                    # X11 자동 반복: 놓기+누르기 쌍이 즉시
            self.window._on_key_release(FakeKeyEvent("4", widget))
            self.window._on_key(FakeKeyEvent("4", widget))
        self.window._on_key_release(FakeKeyEvent("4", widget))
        self.pump(0.1)                                        # 정말로 놓음
        self.window._on_key(FakeKeyEvent("3", widget))        # 새로 누름
        self.pump(0.3)
        self.assertEqual(len(self.pvw_commands()) - before, 2)

    def test_buttons_never_take_keyboard_focus(self):
        """버튼이 포커스를 가지면 Space 가 그 버튼(예: 메모리 저장)과 CUT 을 함께 누른다."""
        def walk(widget):
            yield widget
            for child in widget.winfo_children():
                yield from walk(child)
        buttons = [w for w in walk(self.root) if w.winfo_class() in ("Button", "TButton", "Checkbutton", "TCheckbutton")]
        self.assertGreater(len(buttons), 30)
        self.assertEqual([str(b) for b in buttons if str(b.cget("takefocus")) not in ("0", "False")], [])

    # ---------------------------------------------------------------- 메모리 (검토 2번)
    def memory_commands(self):
        return [p for p in self.fx.device.commands_received
                if p[:4] == b"\x01\x00\x00\x00" and bytes.fromhex("01000800") in p]     # SET 만 (GET 제외)

    def test_store_to_boot_slot_asks_first(self):
        self.connect()
        answers = []
        self.window.extras._confirm = lambda title, message: answers.append(title) or False
        self.window.extras.memory_var.set(0)
        self.window.extras._memory("store_memory")
        self.pump(0.3)
        self.assertEqual(len(answers), 1)
        self.assertEqual(self.memory_commands(), [])                     # 아니오 -> 보내지 않음
        self.window.extras._confirm = lambda title, message: True
        self.window.extras._memory("store_memory")
        self.assertTrue(self.pump_until(lambda: len(self.memory_commands()) == 1))

    def test_memory_outside_range_not_sent(self):
        self.connect()
        for value in (1000, -1):
            self.window.extras.memory_var.set(value)
            self.window.extras._memory("store_memory")
            self.window.extras._memory("load_memory")
        self.pump(0.3)
        self.assertEqual(self.memory_commands(), [])
        self.assertIn("0", self.log_text())

    def test_cancel_while_waiting_to_reconnect(self):
        """연결 중에는 버튼이 '취소'. 누르면 대기 중인 연결을 취소한다 (검토2 8번)."""
        self.connect()
        self.window.toggle_connection()
        self.window.connect()                                    # 재연결 대기(1.5초) 중
        self.pump(0.1)
        self.assertEqual(self.window.connection_bar.connect_button.cget("text"), tr("app.connection.cancel"))
        self.window.toggle_connection()                          # 취소
        self.pump(2.0)
        self.assertEqual(self.window._status[0], "disconnected")
        self.assertEqual(self.fx.device.realtime_connections, 1)  # 새 연결 없음
        self.connect()                                           # 다시 연결 가능

    def test_factory_error_does_not_lock_connect_button(self):
        good = self.window._switcher_factory

        def bad(host, on_change=None):
            raise ValueError("bad port")
        self.window._switcher_factory = bad
        self.window.connect()
        self.pump(0.2)
        self.assertEqual(self.window._status[0], "failed")
        self.window._switcher_factory = good
        self.connect()

    def test_focus_loss_forgets_held_keys(self):
        """키를 누른 채 다른 창으로 가면 놓기가 오지 않는다 -> 그 단축키가 영구히 막히면 안 된다 (검토2 9번)."""
        self.connect()
        widget = self.window.device.cut_auto.auto_button
        before = len(self.pvw_commands())
        self.window._on_key(FakeKeyEvent("3", widget))
        self.window._forget_held_keys()                          # FocusOut
        self.window._on_key(FakeKeyEvent("4", widget))
        self.window._on_key(FakeKeyEvent("3", widget))
        self.pump(0.3)
        self.assertEqual(len(self.pvw_commands()) - before, 3)

    def test_reconnect_waits_after_disconnect(self):
        """해제 직후 재연결은 스위처가 이전 채널을 정리하도록 잠시 기다렸다가 연결한다."""
        from se650.app.gui import window as window_module
        self.connect()
        self.window.toggle_connection()
        self.window.connect()
        self.pump(0.3)
        self.assertEqual(self.window._status[0], "connecting")
        self.assertIsNone(self.window.switcher)                              # 아직 연결 시도 안 함
        self.assertTrue(self.pump_until(lambda: self.window._status[0] == "connected",
                                        window_module.RECONNECT_DELAY + 3))

    def test_device_drop(self):
        self.connect()
        self.fx.stop()
        self.assertTrue(self.pump_until(lambda: self.window.connection_bar.status_var.get() == tr("app.status.lost"),
                                        4.0))

    def test_refresh_button(self):
        self.connect()
        self.fx.device.state[(2, 86)] = 3
        self.window.refresh_from_device()
        self.assertTrue(self.pump_until(lambda: self.lit("pgm") == ["3"]))

    def test_keyboard_shortcuts(self):
        self.connect()
        self.window._on_key(FakeKeyEvent("3", self.window.device.cut_auto.auto_button))
        self.assertTrue(self.pump_until(lambda: self.lit("pvw") == ["3"]))
        self.window._on_key(FakeKeyEvent("space", self.window.device.cut_auto.auto_button))
        self.assertTrue(self.pump_until(lambda: self.lit("pgm") == ["3"]))

    def test_invalid_duration(self):
        self.connect()
        self.window.extras.duration_var.set("abc")
        self.window.extras.apply_duration()
        self.pump(0.2)
        self.assertIn(tr("app.message.frames_not_number"), self.log_text())

    def test_library_logs_reach_log_box(self):
        self.connect()
        self.assertTrue(self.pump_until(lambda: tr("transport.log.connected", host="127.0.0.1") in self.log_text()))

    # ---------------------------------------------------------------- 언어
    def test_change_language_keeps_connection_and_state(self):
        self.connect()
        self.window.device.keyers.buttons[("key1", "pgm")].invoke()
        self.assertTrue(self.pump_until(lambda: self.keyer_lit("key1", "pgm")))
        old_log = self.log_text().strip()

        self.window.connection_bar.language.select("ko")
        self.pump(0.2)
        self.assertEqual(self.window.connection_bar.connect_button.cget("text"), "연결 해제")
        self.assertEqual(self.window.connection_bar.status_var.get(), "● 연결됨 (127.0.0.1)")
        self.assertEqual(self.window.extras.cget("text"), "추가 기능 (실제 패널에 없는 기능)")
        self.assertEqual(self.window.device.transition.cget("text"), "TRANSITION")      # 패널 표기는 영어 유지
        self.assertEqual(self.root.title(), "Datavideo SE-650 DVIP 제어")
        self.assertEqual(self.lit("pgm"), ["1"])                        # 버스 상태 유지
        self.assertTrue(self.keyer_lit("key1", "pgm"))                 # 키어 상태 유지
        self.assertIn(old_log.splitlines()[0], self.log_text())      # 로그 유지
        self.assertIn("언어: 한국어", self.log_text())
        self.assertEqual(self.settings["language"], "ko")
        self.window.device.sources.buttons["pvw"]["4"].invoke()                   # 다시 만든 버튼도 동작
        self.assertTrue(self.pump_until(lambda: self.lit("pvw") == ["4"]))

    # ---------------------------------------------------------------- 실제 패널 버튼
    def test_bkg_selects_matte(self):
        self.connect()
        self.window.device.sources.buttons["pgm"]["bkg"].invoke()
        self.assertTrue(self.pump_until(lambda: self.lit("pgm") == ["bkg"]))
        self.assertEqual(self.fx.device.state[(2, 86)], 17)

    def test_still_alternates_and_shows_still_2(self):
        self.connect()
        still = self.window.device.sources.buttons["pvw"]["still"]
        still.invoke()
        self.assertTrue(self.pump_until(lambda: self.fx.device.state[(2, 87)] == 19 and self.lit("pvw") == ["still"]))
        still.invoke()
        self.assertTrue(self.pump_until(lambda: self.fx.device.state[(2, 87)] == 20))
        self.assertTrue(self.pump_until(lambda: still.cget("text") == tr("app.panel.still_2")))
        self.assertEqual(self.lit("pvw"), ["still"])

    def test_transition_group_lamps(self):
        self.connect()          # 시뮬레이터 초기값: TRANS BG 켜짐, MIX
        self.assertTrue(self.group_lit("transition", "trans_bg", theme.TRANSITION_LIT))
        self.assertTrue(self.group_lit("transition", "mix", theme.TRANSITION_LIT))
        self.window.device.transition.buttons["wipe"].invoke()
        self.assertTrue(self.pump_until(lambda: self.group_lit("transition", "wipe", theme.TRANSITION_LIT)))
        self.assertFalse(self.group_lit("transition", "mix", theme.TRANSITION_LIT))
        self.window.device.transition.buttons["trans_bg"].invoke()
        self.assertTrue(self.pump_until(lambda: not self.group_lit("transition", "trans_bg", theme.TRANSITION_LIT)))
        self.assertEqual(self.fx.device.state[(2, 79)], 0)

    def test_wipe_presets_and_rev(self):
        self.connect()
        self.assertTrue(self.group_lit("wipe", "pattern_1", theme.WIPE_LIT))
        self.window.device.wipe.buttons["pattern_2"].invoke()
        self.assertTrue(self.pump_until(lambda: self.group_lit("wipe", "pattern_2", theme.WIPE_LIT)))
        self.assertFalse(self.group_lit("wipe", "pattern_1", theme.WIPE_LIT))
        self.window.device.wipe.buttons["rev"].invoke()
        self.assertTrue(self.pump_until(lambda: self.group_lit("wipe", "rev", theme.WIPE_LIT)))

    def test_logo_on_off(self):
        self.connect()
        self.window.device.logo.button.invoke()
        self.assertTrue(self.pump_until(lambda: theme.is_lit(self.window.device.logo.button, theme.LOGO_LIT)))
        self.assertEqual((self.fx.device.state[(2, 91)], self.fx.device.state[(2, 127)]), (1, 0))

    def test_user_memory_recalls_slot(self):
        self.connect()
        self.window.device.memory.buttons["user_2"].invoke()
        load = bytes.fromhex("01000000 00000800 02000000 01000800 01000000")
        self.assertTrue(self.pump_until(lambda: load in self.fx.device.commands_received))

    def test_reset_explains_instead_of_sending(self):
        self.connect()
        before = len(self.fx.device.commands_received)
        self.window.device.memory.buttons["reset"].invoke()
        self.pump(0.2)
        self.assertIn(tr("app.message.reset_unsupported"), self.log_text())
        self.assertEqual(len(self.fx.device.commands_received), before)

    def test_menu_pad_off_by_default(self):
        """메뉴 키는 문서에 없는 명령 -> 기본으로 보내지 않고 안내만."""
        self.connect()
        self.window.device.menu.buttons["down"].invoke()
        self.pump(0.2)
        self.assertEqual(self.fx.device.menu_keys, [])
        self.assertIn(tr("control.error.undocumented_command"), self.log_text())
        self.assertEqual(self.window.device.menu.buttons["down"].cget("fg"), theme.MUTED)

    def test_menu_pad_when_explicitly_allowed(self):
        self.connect()
        self.window.extras.undocumented_var.set(True)
        self.window.extras._toggle_undocumented()
        self.assertTrue(self.settings["undocumented_menu"])
        self.window.device.menu.buttons["down"].invoke()
        self.window.device.menu.buttons["enter"].invoke()
        self.assertTrue(self.pump_until(lambda: self.fx.device.menu_keys == [3, 6]))
        self.assertTrue(all("undocumented" in v for v in self.fx.device.violations))   # 허용한 경우에만 예상된 기록
        self.fx.device.violations.clear()

    def test_tbar_full_travel_completes_transition(self):
        self.connect()
        before = len(self.fx.device.commands_received)
        tbar = self.window.device.tbar
        for position in range(0, 101, 4):
            tbar._on_move(str(position))
        tbar._on_release(None)
        self.assertTrue(self.pump_until(lambda: self.lit("pgm") == ["2"] and self.lit("pvw") == ["1"]))
        self.assertEqual(self.fx.device.wipe_levels[-1], 100.0)
        self.assertEqual(self.window.switcher.tbar.origin, 100)         # 다음 트랜지션은 위 -> 아래
        self.assertEqual(len(self.fx.device.commands_received), before)  # 명령 패킷 0개 (실시간 답장에 실림)

    def test_tbar_without_connection_logs_once_per_drag(self):
        tbar = self.window.device.tbar
        for position in (10, 30, 50):
            tbar._on_move(str(position))
        tbar._on_release(None)
        self.pump(0.2)
        self.assertEqual(self.log_text().count(tr("app.message.connect_first")), 1)

    def test_button_mashing_stays_within_protocol(self):
        """모든 버튼을 연타해도 문서 규칙 위반 0, 명령 채널 패킷 수도 제한된다."""
        self.connect()
        device = self.window.device
        before = len(self.fx.device.commands_received)
        presses = 0
        for _ in range(3):
            for widget in (device.cut_auto.cut_button, device.cut_auto.auto_button,
                           device.memory.buttons["user_1"], device.memory.buttons["user_2"],
                           device.transition.buttons["trans_bg"], device.wipe.buttons["rev"],
                           device.keyers.buttons[("key1", "pgm")], device.logo.button,
                           device.sources.buttons["pvw"]["3"], device.sources.buttons["pgm"]["still"],
                           self.window.extras.ftb_button, device.menu.buttons["down"]):
                widget.invoke()
                presses += 1
            for position in list(range(0, 101, 5)) + list(range(100, -1, -5)):
                device.tbar._on_move(str(position))
            device.tbar._on_release(None)
            self.window.refresh_from_device()
        self.pump(1.5)
        sent = len(self.fx.device.commands_received) - before
        self.assertLessEqual(sent, presses + 6)            # 버튼당 최대 1패킷 + 새로고침/이름 (T-바는 0)
        self.assertEqual(self.fx.device.menu_keys, [])

    def test_choice_labels_translated_but_values_language_independent(self):
        self.window.connection_bar.language.select("ko")
        self.pump(0.1)
        self.assertEqual(self.window.extras.audio_mode.get(), "디지털")      # 표시
        self.window.extras.audio_mode.select("Analog")
        self.assertEqual(self.settings["audio_mode"], "Analog")              # 저장값은 ID
        self.assertEqual(self.window.extras.audio_mode.get(), "아날로그")

    def test_language_restored_from_settings(self):
        self.settings.set("language", "ko")
        root = tk.Tk()
        window = MainWindow(root, settings=Settings(self.settings.path))
        try:
            self.assertEqual(window.connection_bar.connect_button.cget("text"), "연결")
            self.assertEqual(window.connection_bar.language.value, "ko")
        finally:
            window.stop()
            root.destroy()
            del window, root
            gc.collect()

    def test_disconnected_status_redrawn_in_new_language(self):
        self.window.connection_bar.language.select("ko")
        self.pump(0.1)
        self.assertEqual(self.window.connection_bar.status_var.get(), "● 연결 안 됨")


if __name__ == "__main__":
    unittest.main()

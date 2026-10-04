"""제어 계층 - 상태 모델: 순수 로직 (소켓 없음)."""
import itertools
import unittest

from se650.control.state import STATE_CONTROLS, KeyerState, SwitcherState
from se650.control.tbar import TBarTracker
from se650.protocol.codec import ControlValue
from se650.protocol.constants import Controls


class KeyerStateTests(unittest.TestCase):
    def test_preview_truth_table(self):
        """패널 PVW 줄 = ON XOR 다음 트랜지션 포함"""
        table = {(False, False): False, (False, True): True, (True, False): True, (True, True): False}
        for (on, trans), preview in table.items():
            self.assertEqual(KeyerState(on, trans).preview, preview, (on, trans))

    def test_program_press_toggles_on_and_keeps_preview(self):
        for on, trans in itertools.product((False, True), repeat=2):
            before = KeyerState(on, trans)
            after = before.after_program_press()
            self.assertEqual(after.on, not on)
            self.assertEqual(after.preview, before.preview, (on, trans))

    def test_preview_press_toggles_preview_only(self):
        for on, trans in itertools.product((False, True), repeat=2):
            before = KeyerState(on, trans)
            after = before.after_preview_press()
            self.assertEqual(after.on, on)
            self.assertEqual(after.preview, not before.preview)

    def test_reported_case_pgm_only(self):
        """사용자 보고: PGM 만 켰을 때 장비 값 ON=1, 플래그=1 -> PVW 꺼짐"""
        self.assertEqual(KeyerState().after_program_press(), KeyerState(on=True, in_next_transition=True))
        self.assertFalse(KeyerState(True, True).preview)


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.state = SwitcherState()

    def test_bus_values(self):
        self.assertTrue(self.state.apply(ControlValue(Controls.PGM_SRC, 3)))
        self.assertTrue(self.state.apply(ControlValue(Controls.PST_SRC, 1)))
        self.assertEqual((self.state.pgm, self.state.pvw), (3, 1))

    def test_unchanged_returns_false(self):
        self.state.apply(ControlValue(Controls.PGM_SRC, 3))
        self.assertFalse(self.state.apply(ControlValue(Controls.PGM_SRC, 3)))

    def test_keyer_flags(self):
        self.state.apply(ControlValue(Controls.KEY1_ON, 1))
        self.state.apply(ControlValue(Controls.TRANS_KEY1, 1))
        keyer = self.state.keyers["key1"]
        self.assertEqual((keyer.on, keyer.in_next_transition, keyer.preview), (True, True, False))

    def test_keyer_order_independent(self):
        a, b = SwitcherState(), SwitcherState()
        on, flag = ControlValue(Controls.DSK1_ON, 1), ControlValue(Controls.DSK1_TRANS, 1)
        for value in (on, flag):
            a.apply(value)
        for value in (flag, on):
            b.apply(value)
        self.assertEqual(a.keyers["dsk1"], b.keyers["dsk1"])

    def test_transition_engines(self):
        self.state.apply(ControlValue(Controls.ME_STATE, 2))
        self.state.apply(ControlValue(Controls.ME_DURATION, 25))
        self.state.apply(ControlValue(Controls.FTB_STATE, 3))
        self.assertTrue(self.state.me.running)
        self.assertEqual(self.state.me.duration, 25)
        self.assertFalse(self.state.ftb.running)

    def test_panel_transition_settings(self):
        self.state.apply(ControlValue(Controls.TRANS_BGND, 1))
        self.state.apply(ControlValue(Controls.WIPE_PATTERN, 2))
        self.state.apply(ControlValue(Controls.TRANS_REVERSE, 1))
        self.assertEqual((self.state.transition_background, self.state.wipe_pattern, self.state.wipe_reverse),
                         (True, 2, True))

    def test_subsection_ignored(self):
        self.assertFalse(self.state.apply(ControlValue(Controls.PGM_SRC, 4, subsection=1)))
        self.assertIsNone(self.state.pgm)

    def test_unknown_control_ignored(self):
        self.assertFalse(self.state.apply(ControlValue(Controls.KEY1_SRC, 3)))

    def test_state_controls_cover_bus_keyers_and_engines(self):
        for control in (Controls.PGM_SRC, Controls.PST_SRC, Controls.KEY1_ON, Controls.TRANS_PIP,
                        Controls.DSK1_TRANS, Controls.ME_DURATION, Controls.FTB_STATE):
            self.assertIn(control, STATE_CONTROLS)


class TBarTests(unittest.TestCase):
    def test_bottom_to_top_then_top_to_bottom(self):
        tbar = TBarTracker()
        self.assertEqual(tbar.level_for(30), 30)
        self.assertEqual(tbar.level_for(100), 100)       # 완료 -> 출발점이 위로
        self.assertEqual(tbar.level_for(100), 0)         # 그 자리에서는 다음 트랜지션 0%
        self.assertEqual(tbar.level_for(25), 75)         # 위에서 아래로 75% 내려옴
        self.assertEqual(tbar.level_for(0), 100)
        self.assertEqual(tbar.origin, 0)

    def test_partial_move_back_and_forth(self):
        tbar = TBarTracker()
        tbar.level_for(60)
        self.assertEqual(tbar.level_for(20), 20)         # 중간에서 되돌려도 출발점 유지
        self.assertEqual(tbar.origin, 0)

    def test_position_clamped(self):
        tbar = TBarTracker()
        self.assertEqual(tbar.level_for(-5), 0)
        self.assertEqual(tbar.level_for(140), 100)


if __name__ == "__main__":
    unittest.main()

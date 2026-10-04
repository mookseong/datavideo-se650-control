"""명령 계층: payload 바이트가 공식 문서/레퍼런스 구현과 일치하는지. (소켓 없음)"""
import struct
import unittest

from se650.protocol import codec
from se650.protocol.constants import (KEY_SOURCES, SOURCE_ALIASES, SOURCES, TRANSITION_TYPES, ControlId,
                                      Controls, MemoryCommand, TransitionCommand, lookup)


def entry(control: ControlId, value: int) -> bytes:
    return struct.pack("<HHi", control.number, control.section, value)


class EncodeTests(unittest.TestCase):
    def test_set_program_matches_document_example(self):
        # 문서 2.3.1: [SET][SWITCHER<<16 | PGM_SRC][INPUT_01]
        self.assertEqual(codec.encode_set([(Controls.PGM_SRC, 1)]), bytes.fromhex("01000000 56000200 01000000"))

    def test_set_multiple_values_in_one_packet(self):
        payload = codec.encode_set([(Controls.KEY1_ON, 1), (Controls.TRANS_KEY1, 1)])
        self.assertEqual(payload, bytes.fromhex("01000000 13000200 01000000 50000200 01000000"))

    def test_get(self):
        self.assertEqual(codec.encode_get([Controls.PGM_SRC, Controls.PST_SRC]),
                         bytes.fromhex("00000000 56000200 57000200"))

    def test_transition_commands(self):
        self.assertEqual(codec.encode_set([(Controls.ME_COMMAND, TransitionCommand.RUN)]),
                         bytes.fromhex("01000000 00000700 01000000"))
        self.assertEqual(codec.encode_set([(Controls.ME_COMMAND, TransitionCommand.GOTO_END)]),
                         bytes.fromhex("01000000 00000700 05000000"))
        self.assertEqual(codec.encode_set([(Controls.FTB_COMMAND, 1)])[4:8], bytes.fromhex("0a000700"))

    def test_memory_load(self):
        payload = codec.encode_set([(Controls.MEMORY_SELECT, 3), (Controls.MEMORY_COMMAND, MemoryCommand.LOAD)])
        self.assertEqual(payload, bytes.fromhex("01000000 00000800 03000000 01000800 01000000"))

    def test_float_control_encoded_as_ieee754(self):
        # T-바 진행률 50.0 -> 0x42480000
        self.assertEqual(codec.encode_set([(Controls.WIPE_LEVEL, 50)]), bytes.fromhex("01000000 01000200 00004842"))

    def test_input_name_and_menu(self):
        self.assertEqual(codec.encode_get_input_name(2), bytes.fromhex("09000000 02000000"))
        self.assertEqual(codec.encode_menu(6), bytes.fromhex("15000000 06000000"))


class ControlNumberTests(unittest.TestCase):
    """공식 문서 번호 확인 (4.1.3 / 4.1.6 / 4.1.7 / 4.1.8)"""

    def test_switcher_controls(self):
        expected = {"WIPE_PATTERN": 0, "WIPE_LEVEL": 1, "KEY1_ON": 19, "PIP_ON": 49, "TRANS_BGND": 79,
                    "TRANS_KEY1": 80, "TRANS_PIP": 81, "TRANS_REVERSE": 84, "PGM_SRC": 86, "PST_SRC": 87,
                    "TRANS_TYPE": 88, "DSK1_ON": 91, "DSK1_TRANS": 127, "FTB_ENABLE": 133}
        for name, number in expected.items():
            self.assertEqual(getattr(Controls, name), ControlId(2, number), name)

    def test_value_tables(self):
        self.assertEqual((SOURCES["Matte"], SOURCES["Still 1"], SOURCES["Still 2"]), (17, 19, 20))
        # 사용자 매뉴얼: 패널 BKG = Matte. 예전 이름(Still/Freeze)은 CLI 호환 별칭
        self.assertEqual(SOURCE_ALIASES, {"BKG": 17, "Still": 19, "Freeze": 20})
        self.assertEqual((KEY_SOURCES["Still 1"], KEY_SOURCES["Still 2"]), (19, 20))
        self.assertEqual(TRANSITION_TYPES, {"Mix": 0, "Wipe": 1})
        self.assertEqual(Controls.HDMI_OUT, ControlId(5, 15))


class DecodeTests(unittest.TestCase):
    def test_realtime_ignores_command_value(self):
        payload = struct.pack("<I", 0xDEADBEEF) + entry(Controls.PGM_SRC, 3)
        self.assertEqual(codec.decode_realtime(payload), [codec.ControlValue(Controls.PGM_SRC, 3)])

    def test_switcher_subsection_split(self):
        payload = struct.pack("<I", 1) + struct.pack("<BBHi", 86, 1, 2, 4)
        self.assertEqual(codec.decode_realtime(payload), [codec.ControlValue(Controls.PGM_SRC, 4, subsection=1)])

    def test_flag_uses_low_byte(self):
        param = struct.pack("<HH", 80, 2)
        self.assertEqual(codec.decode_realtime(b"\x01\x00\x00\x00" + param + bytes([0, 1, 0, 0]))[0].value, 0)
        self.assertEqual(codec.decode_realtime(b"\x01\x00\x00\x00" + param + bytes([1, 9, 9, 9]))[0].value, 1)

    def test_float_control_decoded(self):
        payload = struct.pack("<I", 1) + struct.pack("<HHf", 1, 2, 37.5)
        self.assertEqual(codec.decode_realtime(payload)[0].value, 37.5)

    def test_int_keeps_full_value(self):
        payload = struct.pack("<I", 1) + entry(Controls.ME_DURATION, 300)
        self.assertEqual(codec.decode_realtime(payload)[0].value, 300)

    def test_trailing_partial_entry_ignored(self):
        payload = struct.pack("<I", 1) + entry(Controls.PGM_SRC, 2) + b"\x01\x02"
        self.assertEqual(len(codec.decode_realtime(payload)), 1)

    def test_command_reply_control_values(self):
        for command in (0, 1, 0x00134E30):
            payload = struct.pack("<I", command) + entry(Controls.PST_SRC, 2)
            self.assertEqual(codec.decode_command_reply(payload), [codec.ControlValue(Controls.PST_SRC, 2)])

    def test_command_reply_input_name(self):
        name = "카메라1".encode("utf-16le")
        payload = struct.pack("<II", 3, len(name) // 2) + name
        self.assertEqual(codec.decode_command_reply(payload), codec.InputName("카메라1"))

    def test_input_name_reply_not_confused_with_values(self):
        """PGM 값이 3 이어도 (03 00 00 00 패턴) 입력 이름으로 오인하지 않음."""
        payload = struct.pack("<I", 1) + entry(Controls.PGM_SRC, 3)
        self.assertIsInstance(codec.decode_command_reply(payload), list)

    def test_unknown_command_reply(self):
        self.assertIsNone(codec.decode_command_reply(struct.pack("<II", 0x15, 1)))
        self.assertIsNone(codec.decode_command_reply(b"\x01"))


class LookupTests(unittest.TestCase):
    def test_variants(self):
        self.assertEqual(lookup(SOURCES, "In 2"), 2)
        self.assertEqual(lookup(SOURCES, "in2"), 2)
        self.assertEqual(lookup(SOURCES, "still 2"), 20)
        self.assertEqual(lookup({**SOURCES, **SOURCE_ALIASES}, "bkg"), 17)
        self.assertEqual(lookup(SOURCES, "3"), 3)
        self.assertEqual(lookup(SOURCES, 4), 4)

    def test_unknown(self):
        with self.assertRaises(ValueError) as ctx:        # UnknownChoiceError 는 ValueError
            lookup(TRANSITION_TYPES, "Clip")
        self.assertEqual(ctx.exception.key, "protocol.error.unknown_choice")
        self.assertEqual(ctx.exception.params["choices"], "Mix, Wipe")


if __name__ == "__main__":
    unittest.main()

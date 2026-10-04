"""명령 payload 인코딩/디코딩. 길이 prefix 는 전송 계층 담당이므로 여기서는 다루지 않는다.

payload = [명령 4byte LE][파라미터 ...]
SET/GET 파라미터 1 = [control 2byte][section 2byte],  SET 파라미터 2 = 값 4byte
"""
import struct
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple, Union

from .constants import (FLAG_CONTROLS, REALTIME_VARIANT_ID, RESULT_INPUT_NAME, Command, ControlId,
                        Section)

_COMMAND = struct.Struct("<I")
_PARAM_ID = struct.Struct("<HH")         # control, section
_PARAM_VALUE = struct.Struct("<HHi")     # control, section, value
_CONTROL_VALUE_COMMANDS = {Command.GET_CONTROL, Command.SET_CONTROL, REALTIME_VARIANT_ID}


@dataclass(frozen=True)
class ControlValue:
    control: ControlId
    value: int
    subsection: int = 0      # SECTION_SWITCHER 의 상위 바이트. 0 = 메인 M/E


@dataclass(frozen=True)
class InputName:
    name: str


DecodedReply = Union[List[ControlValue], InputName, None]


# ---------------------------------------------------------------- encode
def encode_set(items: Iterable[Tuple[ControlId, int]]) -> bytes:
    body = b"".join(_PARAM_VALUE.pack(c.number, c.section, int(v)) for c, v in items)
    return _COMMAND.pack(Command.SET_CONTROL) + body


def encode_get(controls: Iterable[ControlId]) -> bytes:
    body = b"".join(_PARAM_ID.pack(c.number, c.section) for c in controls)
    return _COMMAND.pack(Command.GET_CONTROL) + body


def encode_get_input_name(input_number: int) -> bytes:
    return struct.pack("<II", Command.GET_INPUT_NAME, input_number)


def encode_menu(key_code: int) -> bytes:
    return struct.pack("<II", Command.MENU, key_code)


# ---------------------------------------------------------------- decode
def command_of(payload: bytes) -> Optional[int]:
    return _COMMAND.unpack_from(payload)[0] if len(payload) >= _COMMAND.size else None


def decode_realtime(payload: bytes) -> List[ControlValue]:
    """실시간 채널 패킷: 명령 값은 무시하고 파라미터/값만 읽는다 (3.2.2)."""
    return _decode_parameters(payload)


def decode_command_reply(payload: bytes) -> DecodedReply:
    """명령 채널 패킷: GET 응답(SET_CONTROL 형식) 또는 입력 이름 응답."""
    command = command_of(payload)
    if command in _CONTROL_VALUE_COMMANDS:
        return _decode_parameters(payload)
    if command == RESULT_INPUT_NAME:
        return _decode_input_name(payload)
    return None


def _decode_parameters(payload: bytes) -> List[ControlValue]:
    values = []
    for offset in range(_COMMAND.size, len(payload) - _PARAM_VALUE.size + 1, _PARAM_VALUE.size):
        number, section, value = _PARAM_VALUE.unpack_from(payload, offset)
        subsection = 0
        if section == Section.SWITCHER:              # 하위 바이트 = 컨트롤, 상위 바이트 = 서브섹션
            number, subsection = number & 0xFF, number >> 8
        control = ControlId(section, number)
        if control in FLAG_CONTROLS:
            value = payload[offset + 4]              # flag 는 하위 1바이트만 유효
        values.append(ControlValue(control, value, subsection))
    return values


def _decode_input_name(payload: bytes) -> Optional[InputName]:
    if len(payload) < 8:
        return None
    char_count = struct.unpack_from("<H", payload, 4)[0]     # 레퍼런스 구현과 같이 하위 16비트만
    raw = payload[8:8 + char_count * 2]
    return InputName(raw.decode("utf-16le", "ignore"))

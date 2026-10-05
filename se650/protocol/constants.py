"""SE-650 명령/섹션/컨트롤 번호와 값 표.

출처: "Datavideo SE-650 Ethernet Control Protocol E1 (2019-07-09)" (괄호 안은 문서 절 번호)
"""
from dataclasses import dataclass
from enum import IntEnum

from ..i18n import LocalizedError


class Command(IntEnum):                       # (2.2)
    GET_CONTROL = 0
    SET_CONTROL = 1
    GET_INPUT_NAME = 9
    MENU = 0x15     # SE-650 문서 2.2 목록(0~20)에 없음. 레퍼런스 구현이 SE-700 표를 빌려 씀 -> 기본 비활성


# 장비가 보내는 응답 명령 ID
RESULT_INPUT_NAME = 3                         # DV_COMMAND_RESULT_INPUT_NAME (2.3.10)
REALTIME_VARIANT_ID = 0x00134E30              # 실시간 포트에서 관찰되는 ID (문서상 "무시됨")


class Section(IntEnum):                       # (4.1)
    SWITCHER = 2
    OUTPUT = 5
    AUDIO = 6
    TRANSITION = 7
    MEMORY = 8


@dataclass(frozen=True)
class ControlId:
    """파라미터 1 = (section << 16) | number  (2.3.1.1)"""
    section: int
    number: int


class Controls:
    # --- SECTION_SWITCHER (4.1.3) ---
    WIPE_PATTERN = ControlId(Section.SWITCHER, 0)         # int 1~32 (4.1.3.1.1)
    WIPE_LEVEL = ControlId(Section.SWITCHER, 1)           # float 0~100 = T-바. 100 이면 장비가 0 으로 되돌리며 완료
    KEY1_ON = ControlId(Section.SWITCHER, 19)             # flag, "Key 1 onto the PGM"
    KEY1_SRC = ControlId(Section.SWITCHER, 20)
    PIP_ON = ControlId(Section.SWITCHER, 49)              # KEY2 = PIP (4.1.3.3)
    PIP_SRC = ControlId(Section.SWITCHER, 50)
    TRANS_BGND = ControlId(Section.SWITCHER, 79)          # flag, 패널 TRANS BG
    TRANS_KEY1 = ControlId(Section.SWITCHER, 80)          # flag, "Keyer 1 included in next transition"
    TRANS_PIP = ControlId(Section.SWITCHER, 81)           # flag
    TRANS_REVERSE = ControlId(Section.SWITCHER, 84)       # flag, 패널 REV (와이프 방향 반전)
    PGM_SRC = ControlId(Section.SWITCHER, 86)
    PST_SRC = ControlId(Section.SWITCHER, 87)
    TRANS_TYPE = ControlId(Section.SWITCHER, 88)
    DSK1_ON = ControlId(Section.SWITCHER, 91)             # flag
    DSK1_SRC = ControlId(Section.SWITCHER, 92)
    DSK1_TRANS = ControlId(Section.SWITCHER, 127)         # flag, "DSK 1 in the DSK transition"
    FTB_ENABLE = ControlId(Section.SWITCHER, 133)         # flag

    # --- SECTION_OUTPUT_CTRL (4.1.6) ---
    HDMI_OUT = ControlId(Section.OUTPUT, 15)              # OUTPUT_DVI_OUT_SELECT

    # --- SECTION_AUDIO_CTRL (4.1.7) ---
    AUDIO_SOURCE = ControlId(Section.AUDIO, 0)
    AUDIO_MODE = ControlId(Section.AUDIO, 2)

    # --- SECTION_TRANSITION_CTRL (4.1.8) ---
    ME_COMMAND = ControlId(Section.TRANSITION, 0)
    ME_STATE = ControlId(Section.TRANSITION, 2)
    ME_DURATION = ControlId(Section.TRANSITION, 3)
    DSK_COMMAND = ControlId(Section.TRANSITION, 5)
    DSK_STATE = ControlId(Section.TRANSITION, 7)
    DSK_DURATION = ControlId(Section.TRANSITION, 8)
    FTB_COMMAND = ControlId(Section.TRANSITION, 10)
    FTB_STATE = ControlId(Section.TRANSITION, 12)
    FTB_DURATION = ControlId(Section.TRANSITION, 13)

    # --- SECTION_MEMORY_CTRL (4.1.9) ---
    MEMORY_SELECT = ControlId(Section.MEMORY, 0)
    MEMORY_COMMAND = ControlId(Section.MEMORY, 1)        # Ready(0) 일 때만 새 명령 (4.1.9.2)
    MEMORY_STATE = ControlId(Section.MEMORY, 2)
    MEMORY_RESULT = ControlId(Section.MEMORY, 3)


@dataclass(frozen=True)
class KeyerControls:
    on: ControlId                 # 키가 PGM 에 올라가 있음
    in_next_transition: ControlId # 다음 트랜지션(AUTO/CUT)에서 이 키를 토글
    source: ControlId


KEYERS = {
    "key1": KeyerControls(Controls.KEY1_ON, Controls.TRANS_KEY1, Controls.KEY1_SRC),
    "pip": KeyerControls(Controls.PIP_ON, Controls.TRANS_PIP, Controls.PIP_SRC),
    "dsk1": KeyerControls(Controls.DSK1_ON, Controls.DSK1_TRANS, Controls.DSK1_SRC),
}

# 문서상 type 이 flag 인 컨트롤: 값은 하위 1바이트만 유효
FLAG_CONTROLS = frozenset({
    Controls.KEY1_ON, Controls.TRANS_KEY1, Controls.TRANS_PIP, Controls.TRANS_BGND, Controls.TRANS_REVERSE,
    Controls.DSK1_ON, Controls.DSK1_TRANS, Controls.FTB_ENABLE,
})

# 문서상 type 이 float 인 컨트롤: 값 4byte 를 IEEE 754 float 로 주고받는다
FLOAT_CONTROLS = frozenset({Controls.WIPE_LEVEL})


class TransitionType(IntEnum):                # (4.1.3.4.10) + 사용자 매뉴얼 "MIX/WIPE 동시 = Clip"
    MIX = 0
    WIPE = 1
    CLIP = 2


class TransitionCommand(IntEnum):             # (4.1.8.1)
    STOP = 0
    RUN = 1
    GOTO_END = 5
    READY = 8       # 엔진이 명령을 처리하면 이 값으로 바꾼다. 이때만 다음 명령을 받는다 (4.1.8.1.9)


class TransitionStatus(IntEnum):              # (4.1.8.3)
    STOPPED = 0
    AT_START = 1
    RUNNING = 2
    AT_END = 3
    PAUSED = 4


class MemoryCommand(IntEnum):                 # (4.1.9.2)
    READY = 0       # 명령이 아니라 "새 명령을 받을 수 있음" 표시. 보내기 전에 확인해야 한다
    LOAD = 1
    STORE = 2


class MemoryState(IntEnum):                   # (4.1.9.3)
    READY = 0
    BUSY = 1
    ERROR = 2


class MemoryResult(IntEnum):                  # (4.1.9.4)
    OK = 0
    FAIL = 1
    NOT_FOUND = 2
    ILLEGAL_COMMAND = 3
    ILLEGAL_VALUE = 4


# --- 소스 번호 ---
# 사용자 매뉴얼: 패널 BKG = Matte, STILL 버튼은 누를 때마다 Still 1 <-> Still 2.
# 프로토콜 문서는 20 을 "Freeze" 로 적었지만 키어 소스 표(4.1.3.2.2)와 매뉴얼은 Still 2 로 쓴다.
SOURCE_BLACK = 0
SOURCE_MATTE = 17
SOURCE_STILL_1 = 19
SOURCE_STILL_2 = 20
STILL_SOURCES = (SOURCE_STILL_1, SOURCE_STILL_2)

# --- 이름 -> 값 표 (GUI 표시 순서 유지) ---
SOURCES = {"Black": SOURCE_BLACK, "In 1": 1, "In 2": 2, "In 3": 3, "In 4": 4,     # (4.1.3.4.8)
           "Matte": SOURCE_MATTE, "Still 1": SOURCE_STILL_1, "Still 2": SOURCE_STILL_2}
SOURCE_ALIASES = {"BKG": SOURCE_MATTE, "Still": SOURCE_STILL_1, "Freeze": SOURCE_STILL_2}   # CLI 호환
KEY_SOURCES = {"Black": 0, "In 1": 1, "In 2": 2, "In 3": 3, "In 4": 4,  # (4.1.3.2.2)
               "Matte": 17, "Still 1": 19, "Still 2": 20}
TRANSITION_TYPES = {"Mix": 0, "Wipe": 1}                                # (4.1.3.4.10)
HDMI_OUTPUTS = {"PGM": 0, "PVW": 1, "PGM+DSK": 2, "PVW+DSK": 3, "MultiView": 4}   # (4.1.6.9)
AUDIO_SOURCES = {"Follow": 0, "In 1": 1, "In 2": 2, "In 3": 3, "In 4": 4}       # (4.1.7.1)
AUDIO_MODES = {"Off": 0, "Digital": 1, "Analog": 2}                              # (4.1.7.3)
AUDIO_FOLLOW_VIDEO = 0
AUDIO_MODE_ANALOG = 2       # 아날로그 모드에서는 입력 1~4 만 가능 (4.1.7.1 Note)
MENU_KEYS = {"MENU": 1, "UP": 2, "DOWN": 3, "LEFT": 4, "RIGHT": 5, "ENTER": 6}

SOURCE_NAMES = {value: name for name, value in SOURCES.items()}
WIPE_PRESETS = (1, 2)              # 패널의 와이프 프리셋 버튼 2개 = 패턴 1, 2 (매뉴얼 "Transition Effects")


class UnknownChoiceError(LocalizedError, ValueError):
    pass


def lookup(table: dict, value) -> int:
    """'In 2', 'in2', '2', 2 같은 입력을 표의 값으로 바꾼다."""
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    wanted = text.lower().replace(" ", "")
    for name, number in table.items():
        if name.lower().replace(" ", "") == wanted:
            return number
    raise UnknownChoiceError("protocol.error.unknown_choice", value=repr(value), choices=", ".join(table))

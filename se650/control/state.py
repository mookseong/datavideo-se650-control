"""스위처 상태 모델. 네트워크와 무관한 순수 도메인 로직.

문서 3.2.3: 장비는 첫 실시간 패킷에 "0 에서 달라진 값"을 보내므로, 접속 전 상태 사본은 모두 0 이어야 한다.
그래서 모든 필드의 초기값은 0(False) 이다. ("모름" 을 뜻하는 None 을 쓰면 값이 0 인 항목을 영영 못 받는다)
"""
from dataclasses import dataclass, field, replace
from typing import Dict

from ..protocol.codec import ControlValue
from ..protocol.constants import (KEYERS, Controls, MemoryCommand, MemoryState, TransitionCommand,
                                  TransitionStatus)


@dataclass
class KeyerState:
    """
    on                 : 키가 지금 PGM 에 올라가 있음
    in_next_transition : 다음 트랜지션에서 이 키를 토글함
    preview            : 패널 PVW 줄 키 버튼 = "트랜지션 후 키가 켜져 있을지" = on XOR in_next_transition
    """
    on: bool = False
    in_next_transition: bool = False

    @property
    def preview(self) -> bool:
        return self.on != self.in_next_transition

    def after_program_press(self) -> "KeyerState":
        """PGM 버튼: 키를 켜고/끄되 PVW 표시는 유지."""
        new_on = not self.on
        return KeyerState(on=new_on, in_next_transition=new_on != self.preview)

    def after_preview_press(self) -> "KeyerState":
        """PVW 버튼: 다음 트랜지션 포함 여부만 토글."""
        return replace(self, in_next_transition=not self.in_next_transition)


@dataclass
class TransitionEngine:
    command: int = 0             # 장비가 명령을 처리하면 READY(8) (4.1.8.1.9)
    status: int = TransitionStatus.STOPPED
    duration: int = 0

    @property
    def running(self) -> bool:
        return self.status == TransitionStatus.RUNNING

    @property
    def ready_for_command(self) -> bool:
        """0 = 아직 명령을 받은 적 없음(정지), 8 = 이전 명령 처리 완료"""
        return self.command in (TransitionCommand.STOP, TransitionCommand.READY)


@dataclass
class MemoryStatus:
    command: int = MemoryCommand.READY
    state: int = MemoryState.READY
    result: int = 0

    @property
    def ready_for_command(self) -> bool:
        return self.command == MemoryCommand.READY and self.state != MemoryState.BUSY


def _new_keyers() -> Dict[str, KeyerState]:
    return {name: KeyerState() for name in KEYERS}


@dataclass
class SwitcherState:
    pgm: int = 0
    pvw: int = 0
    transition_type: int = 0
    transition_background: bool = False      # 패널 TRANS BG
    wipe_pattern: int = 0
    wipe_reverse: bool = False               # 패널 REV
    ftb_enabled: bool = False
    hdmi_out: int = 0
    audio_source: int = 0
    audio_mode: int = 0
    keyers: Dict[str, KeyerState] = field(default_factory=_new_keyers)
    me: TransitionEngine = field(default_factory=TransitionEngine)
    dsk: TransitionEngine = field(default_factory=TransitionEngine)
    ftb: TransitionEngine = field(default_factory=TransitionEngine)
    memory: MemoryStatus = field(default_factory=MemoryStatus)

    def apply(self, update: ControlValue) -> bool:
        """장비에서 받은 값 하나를 반영. 상태가 바뀌었으면 True."""
        binding = _BINDINGS.get(update.control)
        if binding is None or update.subsection != 0:
            return False
        path, convert = binding
        return _assign(self, path, convert(update.value))


def _keyer_bindings():
    bindings = {}
    for name, controls in KEYERS.items():
        bindings[controls.on] = (("keyers", name, "on"), bool)
        bindings[controls.in_next_transition] = (("keyers", name, "in_next_transition"), bool)
    return bindings


_BINDINGS = {
    Controls.PGM_SRC: (("pgm",), int),
    Controls.PST_SRC: (("pvw",), int),
    Controls.TRANS_TYPE: (("transition_type",), int),
    Controls.TRANS_BGND: (("transition_background",), bool),
    Controls.WIPE_PATTERN: (("wipe_pattern",), int),
    Controls.TRANS_REVERSE: (("wipe_reverse",), bool),
    Controls.FTB_ENABLE: (("ftb_enabled",), bool),
    Controls.HDMI_OUT: (("hdmi_out",), int),
    Controls.AUDIO_SOURCE: (("audio_source",), int),
    Controls.AUDIO_MODE: (("audio_mode",), int),
    Controls.ME_COMMAND: (("me", "command"), int),
    Controls.DSK_COMMAND: (("dsk", "command"), int),
    Controls.FTB_COMMAND: (("ftb", "command"), int),
    Controls.MEMORY_COMMAND: (("memory", "command"), int),
    Controls.MEMORY_STATE: (("memory", "state"), int),
    Controls.MEMORY_RESULT: (("memory", "result"), int),
    Controls.ME_STATE: (("me", "status"), int),
    Controls.ME_DURATION: (("me", "duration"), int),
    Controls.DSK_STATE: (("dsk", "status"), int),
    Controls.DSK_DURATION: (("dsk", "duration"), int),
    Controls.FTB_STATE: (("ftb", "status"), int),
    Controls.FTB_DURATION: (("ftb", "duration"), int),
    **_keyer_bindings(),
}

# 접속 시 / 새로고침 시 조회할 컨트롤 = 상태 모델이 아는 모든 컨트롤
STATE_CONTROLS = tuple(_BINDINGS)
BUS_CONTROLS = frozenset({Controls.PGM_SRC, Controls.PST_SRC})


def _assign(root, path, value) -> bool:
    target = root
    for part in path[:-1]:
        target = target[part] if isinstance(target, dict) else getattr(target, part)
    attribute = path[-1]
    if getattr(target, attribute) == value:
        return False
    setattr(target, attribute, value)
    return True

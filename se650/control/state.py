"""스위처 상태 모델. 네트워크와 무관한 순수 도메인 로직."""
from dataclasses import dataclass, field, replace
from typing import Dict, Optional

from ..protocol.codec import ControlValue
from ..protocol.constants import KEYERS, Controls, TransitionStatus


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
    status: int = TransitionStatus.STOPPED
    duration: Optional[int] = None

    @property
    def running(self) -> bool:
        return self.status == TransitionStatus.RUNNING


def _new_keyers() -> Dict[str, KeyerState]:
    return {name: KeyerState() for name in KEYERS}


@dataclass
class SwitcherState:
    pgm: Optional[int] = None
    pvw: Optional[int] = None
    transition_type: Optional[int] = None
    transition_background: bool = False      # 패널 TRANS BG
    wipe_pattern: Optional[int] = None
    wipe_reverse: bool = False               # 패널 REV
    ftb_enabled: bool = False
    keyers: Dict[str, KeyerState] = field(default_factory=_new_keyers)
    me: TransitionEngine = field(default_factory=TransitionEngine)
    dsk: TransitionEngine = field(default_factory=TransitionEngine)
    ftb: TransitionEngine = field(default_factory=TransitionEngine)

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

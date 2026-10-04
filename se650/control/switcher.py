"""스위처 제어: 상태 동기화 + 사용자 조작을 명령으로 변환.

전송은 Connection 인터페이스에, 바이트 변환은 protocol 에 맡긴다.
"""
import logging
import threading
import time
from collections import deque
from typing import Callable, Iterable, List, Protocol, Tuple

from ..i18n import LocalizedError, tr
from ..protocol import codec
from ..protocol.constants import (AUDIO_MODES, AUDIO_SOURCES, HDMI_OUTPUTS, KEY_SOURCES, KEYERS,
                                  MENU_KEYS, SOURCES, TRANSITION_TYPES, ControlId, Controls,
                                  MemoryCommand, TransitionCommand, lookup)
from .state import BUS_CONTROLS, STATE_CONTROLS, KeyerState, SwitcherState, TransitionEngine

log = logging.getLogger(__name__)

INPUT_COUNT = 4
_ENGINES = {
    "me": (Controls.ME_COMMAND, Controls.ME_DURATION),
    "dsk": (Controls.DSK_COMMAND, Controls.DSK_DURATION),
    "ftb": (Controls.FTB_COMMAND, Controls.FTB_DURATION),
}


class InvalidBusError(LocalizedError, ValueError):
    def __init__(self, bus):
        super().__init__("control.error.invalid_bus", bus=repr(bus))


class Connection(Protocol):
    """Switcher 가 전송 계층에 기대하는 최소 인터페이스."""
    host: str
    connected: bool

    def open(self, on_realtime_packet, on_command_packet, on_closed) -> None: ...
    def send_command(self, payload: bytes) -> None: ...
    def close(self) -> None: ...


class Switcher:
    STATE_RETRY_COUNT = 4
    STATE_RETRY_INTERVAL = 0.6
    FTB_ENABLE_DELAY = 0.05

    def __init__(self, connection: Connection, on_change: Callable[[], None] = None):
        self._connection = connection
        self._on_change = on_change or (lambda: None)
        self.state = SwitcherState()
        self.input_names = {}
        self._pending_names = deque()     # 이름 응답에는 입력 번호가 없어서 요청 순서로 매칭
        self._ready = threading.Event()   # PGM/PVW 값을 받으면 set
        self._stopped = threading.Event()

    # ------------------------------------------------------------ 연결
    @property
    def host(self) -> str:
        return self._connection.host

    @property
    def connected(self) -> bool:
        return self._connection.connected

    def connect(self):
        self._stopped.clear()
        self._connection.open(self._on_realtime_packet, self._on_command_packet, self._on_closed)
        self.request_input_names()
        threading.Thread(target=self._sync_initial_state, name="initial-state", daemon=True).start()

    def close(self):
        self._stopped.set()
        self._connection.close()

    def wait_ready(self, timeout: float = 2.0) -> bool:
        return self._ready.wait(timeout)

    def refresh(self):
        self._ready.clear()
        self.request_state()
        self.request_input_names()

    def request_state(self):
        self._send(codec.encode_get(STATE_CONTROLS))

    def request_input_names(self):
        self._pending_names = deque(range(1, INPUT_COUNT + 1))
        for number in range(1, INPUT_COUNT + 1):
            self._send(codec.encode_get_input_name(number))

    # ------------------------------------------------------------ 버스
    def set_program(self, source):
        self._set([(Controls.PGM_SRC, lookup(SOURCES, source))])

    def set_preview(self, source):
        self._set([(Controls.PST_SRC, lookup(SOURCES, source))])

    # ------------------------------------------------------------ 트랜지션
    def auto(self):
        self._run_or_stop("me", self.state.me)

    def cut(self):
        self._set([(Controls.ME_COMMAND, TransitionCommand.GOTO_END)])

    def dsk_auto(self):
        self._run_or_stop("dsk", self.state.dsk)

    def dsk_cut(self):
        self._set([(Controls.DSK_COMMAND, TransitionCommand.GOTO_END)])

    def fade_to_black(self):
        if not self.state.ftb_enabled:
            self._set([(Controls.FTB_ENABLE, 1)])
            time.sleep(self.FTB_ENABLE_DELAY)
        self._run_or_stop("ftb", self.state.ftb)

    def set_transition_type(self, name: str):
        self._set([(Controls.TRANS_TYPE, lookup(TRANSITION_TYPES, name))])

    def set_duration(self, frames: int, engine: str = "me"):
        _, duration_control = _ENGINES[engine]
        self._set([(duration_control, int(frames))])

    # ------------------------------------------------------------ 키어
    def press_keyer(self, keyer: str, bus: str):
        """패널의 키 버튼과 같은 동작. bus = 'pgm' | 'pvw'"""
        controls = KEYERS[keyer]
        current = self.state.keyers[keyer]
        if bus == "pgm":
            new = current.after_program_press()
            self._set([(controls.on, new.on), (controls.in_next_transition, new.in_next_transition)])
        elif bus == "pvw":
            new = current.after_preview_press()
            self._set([(controls.in_next_transition, new.in_next_transition)])
        else:
            raise InvalidBusError(bus)
        self._apply_local_keyer(keyer, new, bus)

    def set_keyer_source(self, keyer: str, source):
        self._set([(KEYERS[keyer].source, lookup(KEY_SOURCES, source))])

    # ------------------------------------------------------------ 출력 / 오디오 / 메모리 / 메뉴
    def set_hdmi_output(self, name: str):
        self._set([(Controls.HDMI_OUT, lookup(HDMI_OUTPUTS, name))])

    def set_audio_source(self, name: str):
        self._set([(Controls.AUDIO_SOURCE, lookup(AUDIO_SOURCES, name))])

    def set_audio_mode(self, name: str):
        self._set([(Controls.AUDIO_MODE, lookup(AUDIO_MODES, name))])

    def load_memory(self, number: int):
        self._set([(Controls.MEMORY_SELECT, int(number)), (Controls.MEMORY_COMMAND, MemoryCommand.LOAD)])

    def store_memory(self, number: int):
        self._set([(Controls.MEMORY_SELECT, int(number)), (Controls.MEMORY_COMMAND, MemoryCommand.STORE)])

    def press_menu(self, key: str):
        self._send(codec.encode_menu(MENU_KEYS[key.upper()]))

    # ------------------------------------------------------------ 내부: 송신
    def _send(self, payload: bytes):
        self._connection.send_command(payload)

    def _set(self, items: Iterable[Tuple[ControlId, int]]):
        self._send(codec.encode_set(items))

    def _run_or_stop(self, engine_name: str, engine: TransitionEngine):
        command_control, _ = _ENGINES[engine_name]
        command = TransitionCommand.STOP if engine.running else TransitionCommand.RUN
        self._set([(command_control, command)])

    def _apply_local_keyer(self, keyer: str, new: KeyerState, bus: str):
        """장비 회신을 기다리지 않고 바로 반영 (연속 클릭이 올바르게 토글되도록)."""
        self.state.keyers[keyer] = new
        log.info(tr("control.log.keyer_sent", bus=bus.upper(), **_keyer_fields(keyer, new)))
        self._on_change()

    # ------------------------------------------------------------ 내부: 수신
    def _on_realtime_packet(self, payload: bytes):
        self._apply(codec.decode_realtime(payload))

    def _on_command_packet(self, payload: bytes):
        reply = codec.decode_command_reply(payload)
        if isinstance(reply, codec.InputName):
            self._store_input_name(reply.name)
        elif reply:
            self._apply(reply)

    def _on_closed(self):
        self._on_change()

    def _apply(self, updates: List[codec.ControlValue]):
        keyers_before = {name: (k.on, k.in_next_transition) for name, k in self.state.keyers.items()}
        changed = False
        for update in updates:
            changed |= self.state.apply(update)
        for name, keyer in self.state.keyers.items():
            if keyers_before[name] != (keyer.on, keyer.in_next_transition):
                log.info(tr("control.log.keyer_received", **_keyer_fields(name, keyer)))
        if any(u.control in BUS_CONTROLS and u.subsection == 0 for u in updates):
            self._ready.set()
        if changed:
            self._on_change()

    def _store_input_name(self, name: str):
        if not self._pending_names:
            return
        number = self._pending_names.popleft()
        self.input_names[number] = name
        log.info(tr("control.log.input_name", number=number, name=name))
        self._on_change()

    def _sync_initial_state(self):
        """문서 3.2.3: 실시간 첫 패킷에 전체 상태가 온다. 기다려도 안 오면 명령 채널 GET 으로 보완.

        처음부터 GET 을 보내지 않는 이유: GET 응답(명령 채널)이 그 사이 조작의 실시간 회신보다
        늦게 도착하면 오래된 값으로 덮어쓴다.
        """
        for attempt in range(1, self.STATE_RETRY_COUNT + 1):
            if self._ready.wait(self.STATE_RETRY_INTERVAL):
                return
            if self._stopped.is_set() or not self.connected:
                return
            log.warning(tr("control.log.state_retry", attempt=attempt, total=self.STATE_RETRY_COUNT))
            try:
                self.request_state()
            except OSError:
                return


def _keyer_fields(name: str, keyer: KeyerState) -> dict:
    return dict(keyer=name.upper(), on=int(keyer.on), trans=int(keyer.in_next_transition),
                preview=int(keyer.preview))

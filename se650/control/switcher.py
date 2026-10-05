"""스위처 제어: 상태 동기화 + 사용자 조작을 명령으로 변환.

전송은 Connection 인터페이스에, 바이트 변환은 protocol 에 맡긴다.
장비에 부담을 주지 않도록 공식 문서의 규칙을 여기서 지킨다:
  - 트랜지션/메모리 명령은 장비가 Ready 일 때만 (4.1.8.1.9, 4.1.9.2)  -> ReadyGate
  - T-바처럼 연속 값은 실시간 답장에 실어 보냄 (1.1, 3.2.4.2)         -> RealtimeOutbox
  - 응답이 있는 요청(입력 이름)은 하나씩, 응답을 받은 뒤 다음 것       -> _request_names_sequentially
  - 문서에 없는 명령(메뉴 0x15)은 명시적으로 허용했을 때만
"""
import logging
import threading
import time
from typing import Callable, Iterable, List, Protocol, Tuple

from ..i18n import LocalizedError, tr
from ..protocol import codec
from ..protocol.constants import (AUDIO_FOLLOW_VIDEO, AUDIO_MODE_ANALOG, AUDIO_MODES, AUDIO_SOURCES, HDMI_OUTPUTS,
                                  KEY_SOURCES, KEYERS, MENU_KEYS, SOURCE_ALIASES, SOURCE_STILL_1, SOURCE_STILL_2,
                                  SOURCES, TRANSITION_TYPES, ControlId, Controls, MemoryCommand, MemoryResult,
                                  MemoryState, TransitionCommand, lookup)
from .gates import ReadyGate
from .outbox import RealtimeOutbox
from .state import BUS_CONTROLS, STATE_CONTROLS, KeyerState, SwitcherState
from .tbar import TBarTracker

log = logging.getLogger(__name__)

INPUT_COUNT = 4
_SOURCE_NAMES_AND_ALIASES = {**SOURCES, **SOURCE_ALIASES}
_BUS_CONTROLS = {"pgm": Controls.PGM_SRC, "pvw": Controls.PST_SRC}
_ENGINES = {
    "me": (Controls.ME_COMMAND, Controls.ME_DURATION),
    "dsk": (Controls.DSK_COMMAND, Controls.DSK_DURATION),
    "ftb": (Controls.FTB_COMMAND, Controls.FTB_DURATION),
}
_MEMORY_RESULT_KEYS = {
    MemoryResult.OK: "control.log.memory_ok",
    MemoryResult.NOT_FOUND: "control.log.memory_not_found",
}


class InvalidBusError(LocalizedError, ValueError):
    def __init__(self, bus):
        super().__init__("control.error.invalid_bus", bus=repr(bus))


class NotReadyError(LocalizedError):
    """장비가 이전 명령을 처리 중이라 보내지 않았다 (문서 규칙)."""


class UndocumentedCommandError(LocalizedError):
    def __init__(self):
        super().__init__("control.error.undocumented_command")


class Connection(Protocol):
    """Switcher 가 전송 계층에 기대하는 최소 인터페이스."""
    host: str
    connected: bool

    def open(self, on_realtime_packet, on_command_packet, on_closed, realtime_reply=None) -> None: ...
    def send_command(self, payload: bytes) -> None: ...
    def close(self) -> None: ...


class Switcher:
    STATE_RETRY_COUNT = 4
    STATE_RETRY_INTERVAL = 0.6
    FTB_ENABLE_DELAY = 0.05
    ENGINE_MIN_INTERVAL = 0.1        # 명령 후 최소 대기 (50Hz 기준 5필드)
    ENGINE_TIMEOUT = 1.0
    MEMORY_MIN_INTERVAL = 0.2
    MEMORY_TIMEOUT = 10.0
    NAME_REPLY_TIMEOUT = 1.0
    REFRESH_MIN_INTERVAL = 1.0

    def __init__(self, connection: Connection, on_change: Callable[[], None] = None,
                 allow_undocumented: bool = False):
        self._connection = connection
        self._on_change = on_change or (lambda: None)
        self.allow_undocumented = allow_undocumented
        self.state = SwitcherState()
        self.input_names = {}
        self.tbar = TBarTracker()
        self._outbox = RealtimeOutbox()
        self._engine_gates = {name: ReadyGate(self.ENGINE_MIN_INTERVAL, self.ENGINE_TIMEOUT) for name in _ENGINES}
        self._memory_gate = ReadyGate(self.MEMORY_MIN_INTERVAL, self.MEMORY_TIMEOUT)
        self._memory_request = None          # (slot, "load"|"store") 결과를 기다리는 중
        self._name_in_flight = None
        self._name_replied = threading.Event()
        self._names_thread = None
        self._last_refresh = float("-inf")
        self._ready = threading.Event()      # 첫 실시간 패킷(전체 상태) 또는 PGM/PVW 값을 받으면 set
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
        self._connection.open(self._on_realtime_packet, self._on_command_packet, self._on_closed,
                              realtime_reply=self._next_realtime_reply)
        self.request_input_names()
        threading.Thread(target=self._sync_initial_state, name="initial-state", daemon=True).start()

    def close(self):
        self._stopped.set()
        self._name_replied.set()
        self._connection.close()

    def wait_ready(self, timeout: float = 2.0) -> bool:
        return self._ready.wait(timeout)

    def refresh(self):
        """새로고침 버튼. 연타해도 1초에 한 번만 보낸다."""
        now = time.monotonic()
        if now - self._last_refresh < self.REFRESH_MIN_INTERVAL:
            return
        self._last_refresh = now
        self.request_state()
        self.request_input_names()

    def request_state(self):
        self._send(codec.encode_get(STATE_CONTROLS))

    def request_input_names(self):
        """입력 이름은 응답에 번호가 없으므로, 하나 보내고 응답을 받은 뒤 다음 것을 보낸다."""
        if self._names_thread and self._names_thread.is_alive():
            return
        self._names_thread = threading.Thread(target=self._request_names_sequentially, name="input-names",
                                              daemon=True)
        self._names_thread.start()

    # ------------------------------------------------------------ 버스
    def set_program(self, source):
        self._set([(Controls.PGM_SRC, lookup(_SOURCE_NAMES_AND_ALIASES, source))])

    def set_preview(self, source):
        self._set([(Controls.PST_SRC, lookup(_SOURCE_NAMES_AND_ALIASES, source))])

    def press_still(self, bus: str):
        """패널 STILL 버튼: 누를 때마다 Still 1 <-> Still 2 (사용자 매뉴얼)."""
        current = getattr(self.state, _bus_attribute(bus))
        target = SOURCE_STILL_2 if current == SOURCE_STILL_1 else SOURCE_STILL_1
        self._set([(_BUS_CONTROLS[bus], target)])

    # ------------------------------------------------------------ 트랜지션
    def auto(self):
        self._run_or_stop("me")

    def cut(self):
        self._engine_command("me", TransitionCommand.GOTO_END)

    def dsk_auto(self):
        self._run_or_stop("dsk")

    def dsk_cut(self):
        self._engine_command("dsk", TransitionCommand.GOTO_END)

    def fade_to_black(self):
        self._require_engine_ready("ftb")
        if not self.state.ftb_enabled:
            self._set([(Controls.FTB_ENABLE, 1)])
            time.sleep(self.FTB_ENABLE_DELAY)
        self._run_or_stop("ftb")

    def set_transition_type(self, name: str):
        self._set([(Controls.TRANS_TYPE, lookup(TRANSITION_TYPES, name))])

    def toggle_transition_background(self):
        self._set([(Controls.TRANS_BGND, not self.state.transition_background)])

    def select_wipe_pattern(self, pattern: int):
        self._set([(Controls.WIPE_PATTERN, int(pattern))])

    def toggle_wipe_reverse(self):
        self._set([(Controls.TRANS_REVERSE, not self.state.wipe_reverse)])

    def set_duration(self, frames: int, engine: str = "me"):
        _, duration_control = _ENGINES[engine]
        self._set([(duration_control, int(frames))])

    def move_tbar(self, position: float) -> float:
        """T-바 위치(0=아래 ~ 100=위). 진행률은 다음 실시간 답장에 실린다 (추가 패킷 없음).
        100(완료)은 덮어쓰지 않고 반드시 전달한다."""
        level = self.tbar.level_for(position)
        self._outbox.put(Controls.WIPE_LEVEL, level, must_deliver=level >= 100)
        return level

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

    def toggle_logo(self):
        """패널 LOGO ON/OFF: 로고(DSK1)를 PGM 과 PVW 에 함께 켜고 끈다 (사용자 매뉴얼)."""
        controls = KEYERS["dsk1"]
        new = KeyerState(on=not self.state.keyers["dsk1"].on, in_next_transition=False)
        self._set([(controls.on, new.on), (controls.in_next_transition, False)])
        self._apply_local_keyer("dsk1", new, "logo")

    def set_keyer_source(self, keyer: str, source):
        self._set([(KEYERS[keyer].source, lookup(KEY_SOURCES, source))])

    # ------------------------------------------------------------ 출력 / 오디오 / 메모리 / 메뉴
    def set_hdmi_output(self, name: str):
        self._set([(Controls.HDMI_OUT, lookup(HDMI_OUTPUTS, name))])

    def set_audio_source(self, name: str):
        source = lookup(AUDIO_SOURCES, name)
        if source == AUDIO_FOLLOW_VIDEO and self.state.audio_mode == AUDIO_MODE_ANALOG:
            raise LocalizedError("control.error.analog_needs_input")       # 4.1.7.1 Note
        self._set([(Controls.AUDIO_SOURCE, source)])

    def set_audio_mode(self, name: str):
        mode = lookup(AUDIO_MODES, name)
        if mode == AUDIO_MODE_ANALOG and self.state.audio_source == AUDIO_FOLLOW_VIDEO:
            raise LocalizedError("control.error.analog_needs_input")
        self._set([(Controls.AUDIO_MODE, mode)])

    def load_memory(self, number: int):
        self._memory_command(int(number), MemoryCommand.LOAD)

    def store_memory(self, number: int):
        self._memory_command(int(number), MemoryCommand.STORE)

    def press_menu(self, key: str):
        """문서에 없는 명령 (2.2 목록 밖). allow_undocumented 일 때만 보낸다."""
        if not self.allow_undocumented:
            raise UndocumentedCommandError()
        self._send(codec.encode_menu(MENU_KEYS[key.upper()]))

    # ------------------------------------------------------------ 내부: 송신
    def _send(self, payload: bytes):
        self._connection.send_command(payload)

    def _set(self, items: Iterable[Tuple[ControlId, int]]):
        self._send(codec.encode_set(items))

    def _require_engine_ready(self, engine: str):
        if not self._engine_gates[engine].can_send(getattr(self.state, engine).ready_for_command):
            raise NotReadyError("control.error.engine_busy")

    def _engine_command(self, engine: str, command: int):
        self._require_engine_ready(engine)
        command_control, _ = _ENGINES[engine]
        self._set([(command_control, command)])
        self._engine_gates[engine].mark_sent()

    def _run_or_stop(self, engine: str):
        running = getattr(self.state, engine).running
        self._engine_command(engine, TransitionCommand.STOP if running else TransitionCommand.RUN)

    def _memory_command(self, slot: int, command: int):
        if not self._memory_gate.can_send(self.state.memory.ready_for_command):
            raise NotReadyError("control.error.memory_busy")
        self._set([(Controls.MEMORY_SELECT, slot), (Controls.MEMORY_COMMAND, command)])
        self._memory_gate.mark_sent()
        self._memory_request = (slot, command)

    def _apply_local_keyer(self, keyer: str, new: KeyerState, bus: str):
        """장비 회신을 기다리지 않고 바로 반영 (연속 클릭이 올바르게 토글되도록)."""
        self.state.keyers[keyer] = new
        log.info(tr("control.log.keyer_sent", bus=bus.upper(), **_keyer_fields(keyer, new)))
        self._on_change()

    def _next_realtime_reply(self):
        """실시간 채널 스레드가 장비 패킷마다 호출. 실을 값이 없으면 None (= null 답장)."""
        items = self._outbox.take()
        return codec.encode_realtime_reply(items) if items else None

    # ------------------------------------------------------------ 내부: 수신
    def _on_realtime_packet(self, payload: bytes):
        self._apply(codec.decode_realtime(payload))
        self._ready.set()                    # 실시간 패킷 = 0 기준 변경분 -> 상태 사본이 장비와 일치 (3.2.3)

    def _on_command_packet(self, payload: bytes):
        reply = codec.decode_command_reply(payload)
        if isinstance(reply, codec.InputName):
            self._store_input_name(reply.name)
        elif reply:
            self._apply(reply)

    def _on_closed(self):
        self._name_replied.set()
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
        self._report_memory_result()
        if changed:
            self._on_change()

    def _report_memory_result(self):
        memory = self.state.memory
        if self._memory_request is None or not memory.ready_for_command:
            return
        slot, command = self._memory_request
        self._memory_request = None
        key = _MEMORY_RESULT_KEYS.get(memory.result, "control.log.memory_failed")
        operation = tr("control.log.memory_load" if command == MemoryCommand.LOAD else "control.log.memory_store")
        message = tr(key, slot=slot, operation=operation, result=memory.result)
        failed = memory.result != MemoryResult.OK or memory.state == MemoryState.ERROR
        (log.warning if failed else log.info)(message)

    def _store_input_name(self, name: str):
        number = self._name_in_flight
        if number is None:
            return                           # 시간 초과로 포기한 요청의 늦은 응답
        self._name_in_flight = None
        self.input_names[number] = name
        log.info(tr("control.log.input_name", number=number, name=name))
        self._name_replied.set()
        self._on_change()

    def _request_names_sequentially(self):
        for number in range(1, INPUT_COUNT + 1):
            if self._stopped.is_set() or not self.connected:
                return
            self._name_replied.clear()
            self._name_in_flight = number
            try:
                self._send(codec.encode_get_input_name(number))
            except OSError:
                return
            if not self._name_replied.wait(self.NAME_REPLY_TIMEOUT):
                self._name_in_flight = None

    def _sync_initial_state(self):
        """문서 3.2.3: 실시간 첫 패킷에 전체 상태가 온다. 기다려도 안 오면 명령 채널 GET 으로 보완.

        처음부터 GET 을 보내지 않는 이유: 불필요한 요청을 줄이고, GET 응답(명령 채널)이
        그 사이 조작의 실시간 회신보다 늦게 도착해 오래된 값으로 덮어쓰는 일을 막기 위해.
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


def _bus_attribute(bus: str) -> str:
    if bus not in _BUS_CONTROLS:
        raise InvalidBusError(bus)
    return bus


def _keyer_fields(name: str, keyer: KeyerState) -> dict:
    return dict(keyer=name.upper(), on=int(keyer.on), trans=int(keyer.in_next_transition),
                preview=int(keyer.preview))

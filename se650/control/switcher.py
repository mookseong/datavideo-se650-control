"""스위처 제어: 상태 동기화 + 사용자 조작을 명령으로 변환.

전송은 Connection 인터페이스에, 바이트 변환은 protocol 에 맡긴다.
장비에 부담을 주지 않도록 공식 문서의 규칙을 여기서 지킨다:
  - 트랜지션/메모리 명령은 장비가 처리를 끝낸 게 확인될 때만 (4.1.8.1.9, 4.1.9.2)  -> ReadyTracker
  - 문서에 정해진 값/범위만 보낸다 (메모리 0~999, 소스 표, 와이프 1~32 ...)
  - T-바처럼 연속 값은 실시간 답장에 실어 보냄 (1.1, 3.2.4.2)                   -> RealtimeOutbox
  - 명령 채널 패킷 사이 최소 간격, 같은 값을 연달아 보내지 않음
  - 응답이 있는 요청(입력 이름)은 하나씩, 응답을 받은 뒤 다음 것
  - 문서에 없는 명령(메뉴 0x15)은 명시적으로 허용했을 때만
"""
import logging
import threading
import time
from collections import deque
from typing import Callable, Iterable, List, Protocol, Tuple

from ..i18n import LocalizedError, tr
from ..protocol import codec
from ..protocol.constants import (AUDIO_FOLLOW_VIDEO, AUDIO_MODE_ANALOG, AUDIO_MODES, AUDIO_SOURCES,
                                  BOOT_MEMORY_SLOT, DURATION_RANGE, HDMI_OUTPUTS, KEY_SOURCES, KEYERS, MENU_KEYS,
                                  SOURCE_ALIASES, SOURCE_STILL_1, SOURCE_STILL_2, SOURCES, TRANSITION_TYPES,
                                  USER_MEMORY_RANGE, WIPE_PATTERN_RANGE, ControlId, Controls, MemoryCommand,
                                  MemoryResult, MemoryState, TransitionCommand, check_range, lookup)
from .outbox import RealtimeOutbox
from .ready import ReadyTracker
from .state import BUS_CONTROLS, STATE_CONTROLS, KeyerState, SwitcherState
from .tbar import TBarTracker

log = logging.getLogger(__name__)

INPUT_COUNT = 4
_SOURCE_NAMES_AND_ALIASES = {**SOURCES, **SOURCE_ALIASES}
_BUS_CONTROLS = {"pgm": Controls.PGM_SRC, "pvw": Controls.PST_SRC}
_ENGINES = {
    "me": (Controls.ME_COMMAND, Controls.ME_STATE, Controls.ME_DURATION),
    "dsk": (Controls.DSK_COMMAND, Controls.DSK_STATE, Controls.DSK_DURATION),
    "ftb": (Controls.FTB_COMMAND, Controls.FTB_STATE, Controls.FTB_DURATION),
}
_MEMORY_CONTROLS = (Controls.MEMORY_COMMAND, Controls.MEMORY_STATE, Controls.MEMORY_RESULT)
_MEMORY_RESULT_KEYS = {
    MemoryResult.OK: "control.log.memory_ok",
    MemoryResult.NOT_FOUND: "control.log.memory_not_found",
}


class InvalidBusError(LocalizedError, ValueError):
    def __init__(self, bus):
        super().__init__("control.error.invalid_bus", bus=repr(bus))


class NotReadyError(LocalizedError):
    """장비가 이전 명령을 처리 중이거나 처리가 확인되지 않아 보내지 않았다 (문서 규칙)."""


class UndocumentedCommandError(LocalizedError):
    def __init__(self):
        super().__init__("control.error.undocumented_command")


class BootSlotError(LocalizedError):
    """메모리 0 은 부팅 때마다 불러온다 (4.1.9.1). 덮어쓰려면 confirm=True 가 필요하다."""

    def __init__(self):
        super().__init__("control.error.boot_slot")


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
    # 처리 확인: (첫 확인까지, 확인 간격, 최대 확인 횟수). 확인 = 그 값만 GET 으로 묻는 작은 패킷 하나
    ENGINE_CHECKS = (0.1, 0.2, 10)
    MEMORY_CHECKS = (0.3, 0.5, 40)
    WATCH_INTERVAL = 0.05
    NAME_REPLY_TIMEOUT = 1.0
    REFRESH_MIN_INTERVAL = 1.0
    MIN_SEND_GAP = 0.02              # 명령 채널 패킷 사이 최소 간격 (1필드)
    DUPLICATE_WINDOW = 0.25          # 같은 SET 을 이 시간 안에 다시 보내지 않음 (키 반복, 연타)
    MAX_OUTSTANDING_GETS = 64            # 응답이 오지 않은 GET 기록의 상한 (메모리만 보호, 시간으로 버리지 않음)

    def __init__(self, connection: Connection, on_change: Callable[[], None] = None,
                 allow_undocumented: bool = False):
        self._connection = connection
        self._on_change = on_change or (lambda: None)
        self.allow_undocumented = allow_undocumented
        self.state = SwitcherState()
        self.input_names = {}
        self.tbar = TBarTracker()
        self._outbox = RealtimeOutbox()
        self._trackers = {name: ReadyTracker(*self.ENGINE_CHECKS) for name in _ENGINES}
        self._trackers["memory"] = ReadyTracker(*self.MEMORY_CHECKS)
        self._gave_up_reported = set()
        self._memory_request = None          # (slot, command) 결과를 기다리는 중
        self._send_lock = threading.RLock()  # 전송 순서 = GET 순번 순서가 되도록 한 잠금으로 묶는다
        self._last_send = 0.0
        self._last_set = (b"", 0.0)
        self._get_seq = 0                    # 보낸 GET 수
        self._gets_lock = threading.Lock()   # 수신 스레드는 이것만 잡는다 (_send_lock 은 송신 중 오래 잡힐 수 있음)
        self._outstanding_gets = deque()     # (seq, 요청한 컨트롤 집합, 보낸 시각)
        self._realtime_updated_at = {}       # 컨트롤 -> 실시간으로 마지막에 갱신된 시각
        self._late_name_replies = 0          # 시간 초과로 포기한 이름 요청 수 (늦게 오는 응답을 버릴 개수)
        self._late_since = 0.0
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
        """Switcher 와 연결 객체는 한 번만 쓴다. close() 뒤 재연결은 새 객체로 (닫힌 객체는 열리지 않는다)."""
        self._connection.open(self._on_realtime_packet, self._on_command_packet, self._on_closed,
                              realtime_reply=self._next_realtime_reply)
        self.request_input_names()
        threading.Thread(target=self._sync_initial_state, name="initial-state", daemon=True).start()
        threading.Thread(target=self._watch_pending, name="ready-watch", daemon=True).start()

    def close(self):
        self._stopped.set()
        self._name_replied.set()
        self._connection.close()

    def wait_ready(self, timeout: float = 2.0) -> bool:
        return self._ready.wait(timeout)

    def refresh(self):
        """새로고침 버튼. 연타해도 1초에 한 번만 보낸다. 막힌 명령의 처리 여부도 다시 확인한다."""
        now = time.monotonic()
        if now - self._last_refresh < self.REFRESH_MIN_INTERVAL:
            return
        self._last_refresh = now
        for tracker in self._trackers.values():
            tracker.reset()
        self._gave_up_reported.clear()
        self.request_state()
        self.request_input_names()

    def request_state(self):
        self._get(STATE_CONTROLS)

    def request_input_names(self):
        """입력 이름은 응답에 번호가 없으므로, 하나 보내고 응답을 받은 뒤 다음 것을 보낸다."""
        if self._names_thread and self._names_thread.is_alive():
            return
        self._names_thread = threading.Thread(target=self._request_names_sequentially, name="input-names",
                                              daemon=True)
        self._names_thread.start()

    def is_busy(self, gate: str) -> bool:
        """'me' | 'dsk' | 'ftb' | 'memory' 의 이전 명령이 아직 처리 확인 전인가 (화면 표시용)."""
        return self._trackers[gate].pending

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
        self._require_ready("ftb")
        if not self.state.ftb_enabled:
            self._set([(Controls.FTB_ENABLE, 1)])
            time.sleep(self.FTB_ENABLE_DELAY)
        self._run_or_stop("ftb")

    def set_transition_type(self, name: str):
        self._set([(Controls.TRANS_TYPE, lookup(TRANSITION_TYPES, name))])

    def toggle_transition_background(self):
        self._set([(Controls.TRANS_BGND, not self.state.transition_background)])

    def select_wipe_pattern(self, pattern: int):
        self._set([(Controls.WIPE_PATTERN, check_range("wipe pattern", pattern, *WIPE_PATTERN_RANGE))])

    def toggle_wipe_reverse(self):
        self._set([(Controls.TRANS_REVERSE, not self.state.wipe_reverse)])

    def set_duration(self, frames: int, engine: str = "me"):
        duration_control = _ENGINES[engine][2]
        self._set([(duration_control, check_range("duration", frames, *DURATION_RANGE))])

    def move_tbar(self, position: float):
        """T-바 위치(0=아래 ~ 100=위). 진행률은 다음 실시간 답장에 실린다 (추가 패킷 없음).
        100(완료)은 덮어쓰지 않고 반드시 전달한다.
        AUTO/CUT 이 진행 중이거나 처리 확인 전이면 무시한다 (엔진과 T-바가 서로 다투지 않게). 보낸 진행률 또는 None."""
        if self.state.me.running or self._trackers["me"].pending:
            return None
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
        mode = lookup(AUDIO_MODES, name)          # 표에 Test(3) 가 없으므로 테스트 톤은 보낼 수 없다
        if mode == AUDIO_MODE_ANALOG and self.state.audio_source == AUDIO_FOLLOW_VIDEO:
            raise LocalizedError("control.error.analog_needs_input")
        self._set([(Controls.AUDIO_MODE, mode)])

    def load_memory(self, number: int):
        self._memory_command(check_range("user memory", number, *USER_MEMORY_RANGE), MemoryCommand.LOAD)

    def store_memory(self, number: int, confirm_boot_slot: bool = False):
        """메모리 0 은 부팅 때 불러오는 메모리라 confirm_boot_slot=True 일 때만 저장한다 (4.1.9.1)."""
        slot = check_range("user memory", number, *USER_MEMORY_RANGE)
        if slot == BOOT_MEMORY_SLOT and not confirm_boot_slot:
            raise BootSlotError()
        self._memory_command(slot, MemoryCommand.STORE)

    def press_menu(self, key: str):
        """문서에 없는 명령 (2.2 목록 밖). allow_undocumented 일 때만 보낸다."""
        if not self.allow_undocumented:
            raise UndocumentedCommandError()
        self._send(codec.encode_menu(MENU_KEYS[key.upper()]))

    # ------------------------------------------------------------ 내부: 송신
    def _send(self, payload: bytes):
        """모든 명령 채널 전송은 여기로. 패킷 사이에 최소 1필드 간격을 둔다."""
        with self._send_lock:
            wait = self.MIN_SEND_GAP - (time.monotonic() - self._last_send)
            if wait > 0:
                time.sleep(wait)
            self._connection.send_command(payload)
            self._last_send = time.monotonic()

    def _set(self, items: Iterable[Tuple[ControlId, int]]):
        payload = codec.encode_set(items)
        last_payload, last_time = self._last_set
        if payload == last_payload and time.monotonic() - last_time < self.DUPLICATE_WINDOW:
            return                           # 키 반복/연타로 같은 값을 다시 보내지 않는다
        self._send(payload)
        self._last_set = (payload, time.monotonic())

    def _get(self, controls):
        with self._send_lock:                # 순번 부여와 전송을 함께: 순번 순서 = 장비 도착 순서
            with self._gets_lock:
                self._get_seq += 1
                entry = (self._get_seq, frozenset(controls), time.monotonic())
                self._outstanding_gets.append(entry)
                while len(self._outstanding_gets) > self.MAX_OUTSTANDING_GETS:
                    self._outstanding_gets.popleft()
            try:
                self._send(codec.encode_get(controls))
            except Exception:
                with self._gets_lock:
                    if entry in self._outstanding_gets:
                        self._outstanding_gets.remove(entry)
                raise

    def _require_ready(self, gate: str):
        if not self._ready.is_set():
            # 상태를 받기 전에는 모든 값이 0 이라 'Ready' 처럼 보인다 -> 확인 없이 보내게 되므로 막는다
            raise NotReadyError("control.error.no_state")
        if self._trackers[gate].pending:
            raise NotReadyError("control.error.memory_busy" if gate == "memory" else "control.error.engine_busy")
        device_ready = (self.state.memory.ready_for_command if gate == "memory"
                        else getattr(self.state, gate).ready_for_command)
        if not device_ready:                 # 다른 컨트롤러(패널 등)가 보낸 명령을 장비가 처리 중
            raise NotReadyError("control.error.memory_busy" if gate == "memory" else "control.error.engine_busy")

    def _engine_command(self, engine: str, command: int):
        self._require_ready(engine)
        if engine == "me":
            self._outbox.clear(Controls.WIPE_LEVEL)     # 대기 중인 T-바 값이 CUT/AUTO 뒤에 도착하지 않게
        self._set_tracked(engine, [(_ENGINES[engine][0], command)])

    def _run_or_stop(self, engine: str):
        running = getattr(self.state, engine).running
        self._engine_command(engine, TransitionCommand.STOP if running else TransitionCommand.RUN)

    def _memory_command(self, slot: int, command: int):
        self._require_ready("memory")
        self._set_tracked("memory", [(Controls.MEMORY_SELECT, slot), (Controls.MEMORY_COMMAND, command)])
        self._memory_request = (slot, command)

    def _set_tracked(self, gate: str, items):
        """처리 확인이 필요한 명령. 같은 값이라도 중복 제거하지 않는다 (확인 장치가 막는다)."""
        with self._send_lock:                # 이 명령 뒤에 보낸 GET 만 '명령 이후 상태'로 믿는다
            self._send(codec.encode_set(items))
            self._trackers[gate].mark_sent(self._get_seq)
        self._gave_up_reported.discard(gate)

    def _apply_local_keyer(self, keyer: str, new: KeyerState, bus: str):
        """장비 회신을 기다리지 않고 바로 반영 (연속 클릭이 올바르게 토글되도록)."""
        self.state.keyers[keyer] = new
        log.info(tr("control.log.keyer_sent", bus=bus.upper(), **_keyer_fields(keyer, new)))
        self._on_change()

    def _next_realtime_reply(self):
        """실시간 채널 스레드가 장비 패킷마다 호출. 실을 값이 없으면 None (= null 답장)."""
        items = self._outbox.take()
        return codec.encode_realtime_reply(items) if items else None

    # ------------------------------------------------------------ 내부: 처리 확인
    def _watch_pending(self):
        """처리 확인 전인 명령이 있으면 정해진 시각에 그 값만 GET 으로 확인. 없으면 아무것도 보내지 않는다."""
        while not self._stopped.wait(self.WATCH_INTERVAL):
            if not self.connected:
                continue
            for gate, tracker in self._trackers.items():
                if tracker.check_due():
                    controls = _MEMORY_CONTROLS if gate == "memory" else _ENGINES[gate][:2]
                    try:
                        self._get(controls)
                    except OSError:
                        return
                elif tracker.gave_up and gate not in self._gave_up_reported:
                    self._gave_up_reported.add(gate)
                    log.error(tr("control.log.not_confirmed", gate=gate))
                    self._on_change()

    def _observe_trackers(self, updates: List[codec.ControlValue], source: str, seq=None):
        """처리 확인. 판단은 '이번 패킷에 실제로 들어 있는 값'으로만 한다 (합쳐진 상태는 오래된 값이 섞일 수 있다).

        source: "realtime" | "get" (seq = 짝지어진 GET 순번) | "unmatched" (짝이 없는 명령 채널 응답 -> 쓰지 않음)
        """
        if source == "unmatched":
            return
        values = {u.control: u.value for u in updates if u.subsection == 0}
        for gate, tracker in self._trackers.items():
            if not tracker.pending:
                continue
            trusted = source == "get" and seq > tracker.sent_get_seq
            if source == "get" and not trusted:
                # 명령 '이전'에 보낸 GET 의 응답 = 명령 이전 상태. 실시간 채널보다 늦게 도착할 수 있어서,
                # "처리 중을 본 뒤의 Ready" 로 착각하면 처리 중인 장비에 다음 명령이 겹친다. 아예 쓰지 않는다.
                continue
            if gate == "memory":
                command = values.get(Controls.MEMORY_COMMAND)
                state = values.get(Controls.MEMORY_STATE)
                if command is None and state is None:
                    continue
                working = (command is not None and command != MemoryCommand.READY) or state == MemoryState.BUSY
                current_state = state if state is not None else self.state.memory.state
                ready = command == MemoryCommand.READY and current_state != MemoryState.BUSY
            else:
                command = values.get(_ENGINES[gate][0])
                if command is None:
                    continue
                working = command not in (TransitionCommand.STOP, TransitionCommand.READY)
                ready = command == TransitionCommand.READY
            if tracker.observe(working, ready, trusted):
                if gate == "memory":
                    self._report_memory_result()
                self._on_change()

    # ------------------------------------------------------------ 내부: 수신
    def _on_realtime_packet(self, payload: bytes):
        updates = codec.decode_realtime(payload)
        now = time.monotonic()
        for update in updates:
            self._realtime_updated_at[update.control] = now
        self._apply(updates, "realtime")
        self._ready.set()                    # 실시간 패킷 = 0 기준 변경분 -> 상태 사본이 장비와 일치 (3.2.3)

    def _on_command_packet(self, payload: bytes):
        reply = codec.decode_command_reply(payload)
        if isinstance(reply, codec.InputName):
            self._store_input_name(reply.name)
        elif reply:
            match = self._match_get_reply({u.control for u in reply})
            if match is None:
                self._apply(reply, "unmatched")
                return
            seq, sent_at = match
            # GET 을 보낸 뒤 실시간으로 갱신된 값은 그쪽이 더 최신이다 -> 덮어쓰지 않는다
            fresh = [u for u in reply if self._realtime_updated_at.get(u.control, float("-inf")) <= sent_at]
            self._apply(fresh, "get", seq)

    def _match_get_reply(self, controls):
        """응답에 든 컨트롤 목록으로 GET 을 찾는다 (같은 목록이면 오래된 것부터 = 명령 채널 순서).
        찾으면 (순번, 보낸 시각), 짝이 없으면 None. 시간이 지났다고 버리지 않는다 (늦은 응답이 남의 짝이 되지 않게)."""
        with self._gets_lock:
            for index, (seq, requested, sent_at) in enumerate(self._outstanding_gets):
                if controls <= requested:
                    del self._outstanding_gets[index]
                    return seq, sent_at
        return None

    def _on_closed(self):
        self._name_replied.set()
        self._on_change()

    def _apply(self, updates: List[codec.ControlValue], source: str, seq=None):
        keyers_before = {name: (k.on, k.in_next_transition) for name, k in self.state.keyers.items()}
        changed = False
        for update in updates:
            changed |= self.state.apply(update)
        for name, keyer in self.state.keyers.items():
            if keyers_before[name] != (keyer.on, keyer.in_next_transition):
                log.info(tr("control.log.keyer_received", **_keyer_fields(name, keyer)))
        if any(u.control in BUS_CONTROLS and u.subsection == 0 for u in updates):
            self._ready.set()
        self._observe_trackers(updates, source, seq)
        if changed:
            self._on_change()

    def _report_memory_result(self):
        """처리 확인이 끝난 뒤에만 부른다 -> 이전 명령의 결과를 이번 결과로 착각하지 않는다."""
        if self._memory_request is None:
            return
        memory = self.state.memory
        slot, command = self._memory_request
        self._memory_request = None
        key = _MEMORY_RESULT_KEYS.get(memory.result, "control.log.memory_failed")
        if memory.state == MemoryState.ERROR:
            key = "control.log.memory_failed"
        operation = tr("control.log.memory_load" if command == MemoryCommand.LOAD else "control.log.memory_store")
        message = tr(key, slot=slot, operation=operation, result=memory.result)
        failed = key != "control.log.memory_ok"
        (log.warning if failed else log.info)(message)

    def _store_input_name(self, name: str):
        if self._late_name_replies:          # 명령 채널은 순서대로 온다 -> 포기한 요청의 응답이 먼저 도착
            if time.monotonic() - self._late_since > 3 * self.NAME_REPLY_TIMEOUT:
                self._late_name_replies = 0      # 그만큼 지나도 안 왔으면 안 오는 것 -> 이 응답은 정상 응답
            else:
                self._late_name_replies -= 1
                return
        number = self._name_in_flight
        if number is None:
            return
        self._name_in_flight = None
        self.input_names[number] = name
        log.info(tr("control.log.input_name", number=number, name=name))
        self._name_replied.set()
        self._on_change()

    def _request_names_sequentially(self):
        """하나씩 묻는다. 응답이 없으면 남은 요청을 멈춘다: 늦게 온 응답이 다음 입력 이름으로 잘못 저장되지 않게."""
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
                self._late_name_replies += 1     # 나중에 오면 버린다 (다음 요청의 응답으로 저장되지 않게)
                self._late_since = time.monotonic()
                if not self._stopped.is_set():
                    log.warning(tr("control.log.input_name_timeout", number=number))
                return

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

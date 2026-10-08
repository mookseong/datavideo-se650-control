"""가짜 SE-650: 공식 문서("SE-650 Ethernet Control Protocol E1") 규칙을 그대로 따르는 시뮬레이터.

- 테스트: 클라이언트가 문서 규칙을 어기면 `violations` 에 기록한다 -> 테스트는 위반 0건을 확인
- 장비 없이 GUI 확인:  python -m se650.simulator   ->  GUI 에서 127.0.0.1 로 연결

문서대로 구현한 동작 (괄호는 문서 절 번호)
- 연결: 포트 조회는 정확한 요청 패킷에만 응답, 이 장비의 포트 쌍이 쓰이는 중이면 "빈 채널 없음" (1.4, 2.1.1)
- 실시간 채널: 매 필드 패킷을 보내고, 답장을 받기 전에는 다음 패킷을 보내지 않는다 (3.2, 3.2.5)
  "마지막으로 보낸 값"과 달라진 값만 보낸다. 첫 패킷의 기준은 모두 0 (3.2.3)
  -> 명령이 한 필드 안에 처리돼 값이 원래대로 돌아오면(예: 8 -> 5 -> 8) 실시간으로는 아무것도 안 보인다
- 명령 채널: 명령-응답. GET 은 SET 형식으로 응답, SET 은 응답 없음 (2.3.1, 2.3.2)
- 트랜지션 엔진: 명령은 필드 간격으로 처리 후 명령 값을 Ready(8) 로 바꾼다 (4.1.8.1.9)
- 유저 메모리: 다음 필드에 Busy, 처리 후 Ready(0). 1000 은 시스템 메모리 (4.1.9.1 ~ 4.1.9.4)

검사하는 위반: 요청 없는 답장, 답장 누락(2초), 실시간 포트에 명령, 문서에 없는 명령, 명령 채널 null,
잘못된 길이/파라미터 수, 읽기 전용 값 쓰기, 문서 범위 밖 값, float 자리에 int, Ready 전 명령,
0~999 밖 메모리, 포트 쌍 중복 사용.

일부러 se650.protocol 을 쓰지 않고 struct 로 직접 구현했다 (클라이언트 코덱과 독립된 기준).
"""
import socket
import struct
import sys
import threading
import time

CMD_GET, CMD_SET, CMD_INPUT_NAME = 0, 1, 9
RESULT_INPUT_NAME = 3
DOCUMENTED_COMMANDS = set(range(0, 21))          # 2.2: 0 ~ 20
FIELD = 0.02                                     # 50Hz
DISCOVERY_REQUEST = struct.pack("<II", 8, 0x55AA0001)
UNANSWERED_LIMIT = 2.0                           # 이 시간 넘게 답장이 없으면 위반 (실시간 채널이 멈춤)
FLOOD_LIMIT = 60                                 # 명령 채널로 1초 안에 이보다 많이 오면 과다 전송으로 기록
SESSION_RELEASE_WAIT = 0.5                       # 새 연결이 올 때 이전 연결이 끝나길 기다리는 시간

PGM, PST = (2, 86), (2, 87)
WIPE_PATTERN, WIPE_LEVEL = (2, 0), (2, 1)
FLOAT_KEYS = {WIPE_LEVEL}
KEYERS = (((2, 19), (2, 80)), ((2, 49), (2, 81)))            # M/E 트랜지션에 포함되는 키어 (ON, 포함 플래그)
DSK1 = ((2, 91), (2, 127))                                   # DSK 트랜지션에 포함되는 DSK1 (4.1.3.5)
ENGINES = {"me": (7, 0), "dsk": (7, 5), "ftb": (7, 10)}      # 각 엔진의 COMMAND 컨트롤
ENGINE_STATES = {"me": (7, 2), "dsk": (7, 7), "ftb": (7, 12)}
TRANS_STOP, TRANS_RUN, TRANS_GOTO_END, TRANS_READY = 0, 1, 5, 8
STATE_STOPPED, STATE_RUNNING = 0, 2
MEMORY_SELECT, MEMORY_COMMAND, MEMORY_STATE, MEMORY_RESULT = (8, 0), (8, 1), (8, 2), (8, 3)
MEM_READY, MEM_LOAD, MEM_STORE = 0, 1, 2
MEM_STATE_READY, MEM_STATE_BUSY, MEM_STATE_ERROR = 0, 1, 2
MEM_RESULT_OK, MEM_RESULT_FAIL, MEM_RESULT_NOT_FOUND = 0, 1, 2

_SOURCES = {0, 1, 2, 3, 4, 17, 19, 20}                       # 4.1.3.4.8 / 4.1.3.2.2 (5~16, 18 미사용)
VALID_VALUES = {                                             # 문서에 값이 정해진 컨트롤
    PGM: _SOURCES, PST: _SOURCES, (2, 20): _SOURCES, (2, 50): _SOURCES, (2, 92): _SOURCES,
    (2, 88): {0, 1},                                         # TRANS_TYPE (4.1.3.4.10)
    (5, 15): set(range(0, 5)),                               # HDMI 출력 (4.1.6.9)
    (6, 0): set(range(0, 5)),                                # 오디오 소스 (4.1.7.1)
    (6, 2): set(range(0, 4)),                                # 오디오 모드 (4.1.7.3)
    (2, 49): {0, 1},                                         # KEY2 ON (int)
    **{key: {0, 1} for key in ((2, 19), (2, 79), (2, 80), (2, 81), (2, 84), (2, 91), (2, 127), (2, 133))},  # flag
    **{key: set(range(0, 9)) for key in ENGINES.values()},   # 트랜지션 명령 (4.1.8.1)
    MEMORY_COMMAND: set(range(0, 4)),                        # (4.1.9.2)
}
VALID_RANGES = {WIPE_PATTERN: (1, 32), MEMORY_SELECT: (0, 999),
                (7, 3): (1, 10000), (7, 8): (1, 10000), (7, 13): (1, 10000)}    # 길이(프레임) > 0
READ_ONLY = set(ENGINE_STATES.values()) | {MEMORY_STATE, MEMORY_RESULT}         # 장비가 쓰는 값 (4.1.8.3, 4.1.9.3/4)
READ_ONLY_SECTIONS = {0}                                                      # SECTION_STATUS (4.1.1)


def frame(payload: bytes) -> bytes:
    return struct.pack("<I", len(payload) + 4) + payload


def initial_state():
    """(section, control) -> value."""
    return {
        PGM: 1, PST: 2, (2, 88): 0, (2, 133): 0,
        WIPE_PATTERN: 1, (2, 79): 1, (2, 84): 0,                   # 와이프 패턴, TRANS BG, REV
        (2, 19): 0, (2, 80): 0, (2, 49): 0, (2, 81): 0, (2, 91): 0, (2, 127): 0,
        (7, 0): TRANS_READY, (7, 2): 0, (7, 3): 30,
        (7, 5): TRANS_READY, (7, 7): 0, (7, 8): 30,
        (7, 10): TRANS_READY, (7, 12): 0, (7, 13): 30,
        MEMORY_SELECT: 0, MEMORY_COMMAND: MEM_READY, MEMORY_STATE: MEM_STATE_READY, MEMORY_RESULT: MEM_RESULT_OK,
        (5, 15): 4, (6, 0): 0, (6, 2): 1,                          # HDMI 출력, 오디오 소스, 오디오 모드
    }


class _RealtimeSession:
    def __init__(self, conn, baseline):
        self.conn = conn
        self.last_sent = dict(baseline)          # 키 -> 마지막으로 보낸 값 (첫 패킷 기준은 0)
        self.forced = []
        self.awaiting_reply = False
        self.sent_at = 0.0
        self.stall_reported = False
        self.sent = 0
        self.replies = 0


class FakeSE650:
    def __init__(self, host="127.0.0.1", discovery_port=5009, realtime_port=5001, push_on_connect=True):
        self.host = host
        self.discovery_port = discovery_port
        self.realtime_port = realtime_port
        self.push_on_connect = push_on_connect     # False: 첫 패킷(전체 상태)을 보내지 않는 장비 흉내
        self.state = initial_state()
        self.input_names = {1: "CAM 1", 2: "CAM 2", 3: "PC", 4: "노트북"}
        self.stored_memories = {0, 1, 2, 3}

        # 테스트 조절
        self.discovery_reply = None                # 지정하면 포트 조회에 이 바이트를 그대로 응답
        self.first_packet_command = 0              # 실시간 첫 패킷의 명령 값 (문서상 무시되는 값)
        self.ignore_gets = 0                       # 처음 n 번의 GET 에 응답하지 않음
        self.name_reply_delay = 0.0                # 입력 이름 응답 지연 (초)
        self.answer_input_names = True             # False: 입력 이름 요청에 응답하지 않음
        self.command_null_mode = "silent"          # "spam": 명령 채널로 0.5초 동안 null 을 쏟아냄
        self.engine_fields = 1                     # 트랜지션 명령 처리에 걸리는 필드 수
        self.auto_fields = 3                       # AUTO 가 Running 상태로 있는 필드 수
        self.memory_fields = 5                     # 메모리 명령 처리에 걸리는 필드 수 (Busy 기간)
        self.memory_fails = False                  # True: 메모리 명령이 Error 로 끝남
        self.frozen = False                        # True: 장비가 멈춤 (실시간 패킷을 보내지 않음)
        self.send_corrupt_frame = False            # True: 실시간 채널로 길이가 깨진 패킷을 한 번 보냄
        # 문서가 정하지 않은 장비 동작 (클라이언트가 이런 장비에서도 안전한지 검사)
        self.echo_sets = False                     # True: SET 을 받으면 적용한 값을 SET 형식으로 되돌려 보냄
        self.get_reply_delay = 0.0                 # GET 응답 지연 (초, 순서는 유지)
        self._paused_until = 0.0

        # 관찰
        self.violations = []                       # 클라이언트의 문서 규칙 위반
        self.commands_received = []                # 명령 채널로 받은 payload
        self.command_nulls = 0                     # 명령 채널로 받은 null
        self.realtime_replies = []                 # 실시간 채널로 받은 답장 payload
        self.wipe_levels = []                      # 받은 T-바 진행률
        self.menu_keys = []                        # 문서에 없는 0x15 명령으로 받은 값
        self.max_names_in_flight = 0
        self.realtime_connections = 0              # 받아들인 실시간 연결 수 (누적)
        self._command_times = []                   # 최근 1초 동안 받은 명령 패킷 시각

        self._lock = threading.RLock()
        self._sessions = []
        self._command_conns = []
        self._pending_engine = {}                  # 엔진 -> [명령, 남은 필드]
        self._running = {}                         # 엔진 -> AUTO 남은 필드
        self._memory_busy_fields = 0
        self._memory_started = False
        self._names_in_flight = 0
        self._stop = threading.Event()
        self._servers = []
        self._clients = []

    # ------------------------------------------------------------ 수명
    def start(self):
        for port, handler in ((self.discovery_port, self._serve_discovery),
                              (self.realtime_port, self._serve_realtime),
                              (self.realtime_port + 1, self._serve_command)):
            server = socket.socket()
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.host, port))
            server.listen(5)
            server.settimeout(0.2)
            self._servers.append(server)
            threading.Thread(target=self._accept_loop, args=(server, handler), daemon=True).start()
        threading.Thread(target=self._field_clock, daemon=True).start()
        return self

    def stop(self):
        self._stop.set()
        for sock in self._servers + self._clients:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    @property
    def open_sessions(self) -> int:
        """지금 열려 있는 실시간 세션 수 (연결 누수 검사용)."""
        with self._lock:
            return len(self._sessions)

    def _accept_loop(self, server, handler):
        while not self._stop.is_set():
            try:
                conn, _ = server.accept()
            except (socket.timeout, OSError):
                continue
            conn.settimeout(None)
            self._clients.append(conn)
            threading.Thread(target=handler, args=(conn,), daemon=True).start()

    def _violation(self, text):
        with self._lock:
            self.violations.append(text)

    # ------------------------------------------------------------ 상태
    def set_value(self, key, value):
        with self._lock:
            self.state[key] = value

    def push(self, keys):
        """값이 그대로여도 다음 필드에 다시 보내게 한다 (테스트용)."""
        with self._lock:
            for session in self._sessions:
                session.forced.extend(k for k in keys if k not in session.forced)

    def _entries(self, keys):
        out = b""
        for section, control in keys:
            if (section, control) not in self.state:
                continue
            value = self.state[(section, control)]
            fmt = "<HHf" if (section, control) in FLOAT_KEYS else "<HHi"
            out += struct.pack(fmt, control, section, value)
        return out

    # ------------------------------------------------------------ 필드 클럭
    def _field_clock(self):
        while not self._stop.is_set():
            time.sleep(FIELD)
            with self._lock:
                self._process_engines()
                self._process_memory()
                for session in list(self._sessions):
                    self._send_field_packet(session)

    def pause_realtime(self, seconds):
        """실시간 패킷을 잠시 멈춘다 (예: 무거운 작업 중인 장비). 답장 기다림은 유지된다."""
        self._paused_until = time.monotonic() + seconds

    def _send_field_packet(self, session):
        if self.frozen or time.monotonic() < self._paused_until:
            return
        if self.send_corrupt_frame:
            self.send_corrupt_frame = False
            try:
                session.conn.sendall(struct.pack("<I", 3) + b"\x00" * 8)
            except OSError:
                pass
            return
        if session.awaiting_reply:                  # 3.2.5: 답장 전에는 다음 패킷을 보내지 않는다
            if time.monotonic() - session.sent_at > UNANSWERED_LIMIT and not session.stall_reported:
                session.stall_reported = True
                self._violation("realtime: packet unanswered for %.0fs - channel stalled (3.2.5)" % UNANSWERED_LIMIT)
            return
        keys = [k for k, v in self.state.items() if session.last_sent.get(k, 0) != v]   # 3.2.3: 바뀐 값만
        keys += [k for k in session.forced if k not in keys]
        session.forced = []
        command = self.first_packet_command if session.sent == 0 else 0
        payload = struct.pack("<I", command) + self._entries(keys)
        try:
            session.conn.sendall(frame(payload))
        except OSError:
            return
        for key in keys:
            session.last_sent[key] = self.state[key]
        session.awaiting_reply = True
        session.sent_at = time.monotonic()
        session.sent += 1

    def _process_engines(self):
        for engine in list(self._running):
            self._running[engine] -= 1
            if self._running[engine] <= 0:
                del self._running[engine]
                self._finish(engine)
                self.state[ENGINE_STATES[engine]] = STATE_STOPPED
        for engine, pending in list(self._pending_engine.items()):
            pending[1] -= 1
            if pending[1] > 0:
                continue
            del self._pending_engine[engine]
            command = pending[0]
            if command == TRANS_GOTO_END:
                self._running.pop(engine, None)
                self._finish(engine)
                self.state[ENGINE_STATES[engine]] = STATE_STOPPED
            elif command == TRANS_RUN:
                self._running[engine] = self.auto_fields
                self.state[ENGINE_STATES[engine]] = STATE_RUNNING
            elif command == TRANS_STOP:
                self._running.pop(engine, None)
                self.state[ENGINE_STATES[engine]] = STATE_STOPPED
            self.state[ENGINES[engine]] = TRANS_READY            # 4.1.8.1.9: 처리 후 Ready

    def _finish(self, engine):
        if engine == "me":
            self._complete_transition()
        elif engine == "dsk":
            on, included = DSK1
            if self.state[included]:
                self.state[on] ^= 1
                self.state[included] = 0

    def _complete_transition(self):
        """M/E 트랜지션 완료: PGM/PVW 교체, 다음 트랜지션 포함 키어는 ON 반전 후 플래그 해제."""
        self.state[PGM], self.state[PST] = self.state[PST], self.state[PGM]
        for on, included in KEYERS:
            if self.state[included]:
                self.state[on] ^= 1
                self.state[included] = 0

    def _process_memory(self):
        if self._memory_busy_fields == 0:
            return
        if not self._memory_started:                  # 명령을 받은 다음 필드에 Busy
            self._memory_started = True
            self.state[MEMORY_STATE] = MEM_STATE_BUSY
        self._memory_busy_fields -= 1
        if self._memory_busy_fields:
            return
        slot, command = self.state[MEMORY_SELECT], self.state[MEMORY_COMMAND]
        state, result = MEM_STATE_READY, MEM_RESULT_OK
        if self.memory_fails:
            state, result = MEM_STATE_ERROR, MEM_RESULT_FAIL
        elif command == MEM_STORE:
            self.stored_memories.add(slot)
        elif command == MEM_LOAD and slot not in self.stored_memories:
            result = MEM_RESULT_NOT_FOUND
        self.state[MEMORY_RESULT] = result
        self.state[MEMORY_STATE] = state
        self.state[MEMORY_COMMAND] = MEM_READY

    # ------------------------------------------------------------ 채널
    def _read_payloads(self, conn, channel):
        buffer = b""
        while True:
            try:
                data = conn.recv(4096)
            except OSError:
                return
            if not data:
                return
            buffer += data
            while len(buffer) >= 4:
                length = struct.unpack_from("<I", buffer)[0]
                if length < 8 or length > 1 << 20:
                    self._violation("%s: invalid packet size %d" % (channel, length))
                    return
                if len(buffer) < length:
                    break
                yield buffer[4:length]
                buffer = buffer[length:]

    def _wait_until_free(self, active) -> bool:
        """이전 연결이 끝나는 중일 수 있으니 잠깐 기다린다. 끝까지 쓰이고 있으면 False."""
        deadline = time.monotonic() + SESSION_RELEASE_WAIT
        while time.monotonic() < deadline:
            with self._lock:
                if not active():
                    return True
            time.sleep(0.01)
        return False

    def _serve_discovery(self, conn):
        request = conn.recv(8)
        if self.discovery_reply is not None:
            reply = self.discovery_reply
        elif request != DISCOVERY_REQUEST:
            self._violation("discovery: malformed request %s (2.1.1)" % request.hex())
            reply = struct.pack("<II", 8, 0x55AA0001)                         # Unknown Command
        elif not self._wait_until_free(lambda: self._sessions or self._command_conns):
            reply = struct.pack("<IHH", 8, 0xFFFF, 0xAA55)                    # No Connection available
        else:
            reply = struct.pack("<IHH", 8, self.realtime_port, 0xAA55)
        try:
            conn.sendall(reply)
        except OSError:
            pass
        conn.close()

    def _serve_realtime(self, conn):
        if not self._wait_until_free(lambda: self._sessions):
            self._violation("connection: realtime port already in use by another controller (1.4)")
            conn.close()
            return
        with self._lock:
            baseline = {} if self.push_on_connect else dict(self.state)          # 3.2.3: 0 기준
            session = _RealtimeSession(conn, baseline)
            self._sessions.append(session)
            self.realtime_connections += 1
        for payload in self._read_payloads(conn, "realtime"):
            with self._lock:
                self.realtime_replies.append(payload)
                if not session.awaiting_reply:
                    self._violation("realtime: reply without a packet from the switcher (3.2.4)")
                session.awaiting_reply = False
                session.replies += 1
                command = struct.unpack_from("<I", payload)[0]
                if len(payload) > 4 and command not in (CMD_GET, CMD_SET):
                    self._violation("realtime: command protocol used on realtime port (2.1.2)")
                self._apply_set(payload, "realtime")                          # 3.2.4.2: 답장에 실린 값 적용
        with self._lock:
            self._sessions.remove(session)
        conn.close()

    def _serve_command(self, conn):
        if not self._wait_until_free(lambda: self._command_conns):
            self._violation("connection: command port already in use by another controller (1.4)")
            conn.close()
            return
        with self._lock:
            self._command_conns.append(conn)
        if self.command_null_mode == "spam":
            threading.Thread(target=self._spam_nulls, args=(conn,), daemon=True).start()
        handlers = {CMD_SET: self._on_set, CMD_GET: self._on_get, CMD_INPUT_NAME: self._on_input_name}
        for payload in self._read_payloads(conn, "command"):
            if len(payload) == 4:
                self.command_nulls += 1
                self._violation("command: null packet on command channel (not in protocol, 2.2)")
                continue
            self.commands_received.append(payload)
            self._check_flood()
            command = struct.unpack_from("<I", payload)[0]
            if command not in DOCUMENTED_COMMANDS:
                self._violation("command: undocumented command %d (2.2)" % command)
                if command == 0x15:
                    self.menu_keys.append(struct.unpack_from("<I", payload, 4)[0])
                continue
            handler = handlers.get(command)
            if handler:
                handler(conn, payload)
        with self._lock:
            self._command_conns.remove(conn)
        conn.close()

    def _check_flood(self):
        now = time.monotonic()
        self._command_times = [t for t in self._command_times if now - t < 1.0] + [now]
        if len(self._command_times) > FLOOD_LIMIT:
            self._violation("command: more than %d packets per second (flooding)" % FLOOD_LIMIT)
            self._command_times.clear()

    @staticmethod
    def _send(conn, payload):
        try:
            conn.sendall(frame(payload))
        except OSError:
            pass

    def _spam_nulls(self, conn):
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            self._send(conn, struct.pack("<I", CMD_SET))

    # ------------------------------------------------------------ 명령 처리
    def _on_set(self, conn, payload):
        with self._lock:
            self._apply_set(payload, "command")
            if self.echo_sets:
                keys = [struct.unpack_from("<HH", payload, o)[::-1] for o in range(4, len(payload) - 7, 8)]
                echo = struct.pack("<I", CMD_SET) + self._entries(keys)
        if self.echo_sets:
            self._send(conn, echo)

    def _apply_set(self, payload, channel):
        body = len(payload) - 4
        if body % 8:
            self._violation("%s: SET with a partial parameter (%d bytes)" % (channel, body))
        for offset in range(4, len(payload) - 7, 8):
            control, section, value = struct.unpack_from("<HHi", payload, offset)
            key = (section, control)
            if not self._value_allowed(key, payload, offset, value, channel):
                continue
            if key == WIPE_LEVEL:                    # T-바: 100 에 닿으면 완료 후 0 으로
                level = struct.unpack_from("<f", payload, offset + 4)[0]
                self.wipe_levels.append(level)
                if level >= 100.0:
                    self._complete_transition()
                continue
            engine = next((name for name, k in ENGINES.items() if k == key), None)
            if engine:
                self._on_engine_command(engine, value)
            elif key == MEMORY_COMMAND:
                self._on_memory_command(value)
            else:
                self.state[key] = value

    def _value_allowed(self, key, payload, offset, value, channel) -> bool:
        if section_of(key) in READ_ONLY_SECTIONS or key in READ_ONLY:
            self._violation("%s: write to read-only control %s" % (channel, key))
            return False
        if key in FLOAT_KEYS:
            raw = struct.unpack_from("<I", payload, offset + 4)[0]
            level = struct.unpack_from("<f", payload, offset + 4)[0]
            if raw and raw < 0x00800000:
                self._violation("%s: int sent where float expected %s" % (channel, key))
                return False
            if not 0.0 <= level <= 100.0:
                self._violation("%s: %s float out of range %r" % (channel, key, level))
                return False
            return True
        if key in VALID_VALUES and value not in VALID_VALUES[key]:
            self._violation("%s: %s value %d not documented" % (channel, key, value))
            return False
        if key in VALID_RANGES:
            low, high = VALID_RANGES[key]
            if not low <= value <= high:
                self._violation("%s: %s value %d outside %d..%d" % (channel, key, value, low, high))
                return False
        return True

    def _on_engine_command(self, engine, command):
        if self.state[ENGINES[engine]] not in (TRANS_READY, TRANS_STOP) or engine in self._pending_engine:
            self._violation("transition: %s command %d before Ready (4.1.8.1.9)" % (engine, command))
        self.state[ENGINES[engine]] = command
        self._pending_engine[engine] = [command, self.engine_fields]

    def _on_memory_command(self, command):
        if self.state[MEMORY_COMMAND] != MEM_READY or self.state[MEMORY_STATE] == MEM_STATE_BUSY:
            self._violation("memory: command %d while not Ready (4.1.9.2)" % command)
        if command == MEM_READY:
            return
        self.state[MEMORY_COMMAND] = command
        self._memory_started = False
        self._memory_busy_fields = self.memory_fields

    def _on_get(self, conn, payload):
        if (len(payload) - 4) % 4 or len(payload) == 4:
            self._violation("command: GET with no or partial parameters")
        if self.ignore_gets > 0:
            self.ignore_gets -= 1
            return
        keys = [struct.unpack_from("<HH", payload, o)[::-1] for o in range(4, len(payload) - 3, 4)]
        with self._lock:
            body = self._entries(keys)
        if self.get_reply_delay:
            time.sleep(self.get_reply_delay)                      # 명령 채널 스레드에서 기다림 -> 순서 유지
        self._send(conn, struct.pack("<I", CMD_SET) + body)       # 2.3.2: SET 형식으로 응답

    def _on_input_name(self, conn, payload):
        """응답을 별도 스레드에서 보내, 컨트롤러가 응답 전에 다음 요청을 보내는지(겹침) 셀 수 있게 한다."""
        number = struct.unpack_from("<I", payload, 4)[0] if len(payload) >= 8 else -1
        if number not in self.input_names:
            self._violation("command: input name for unknown input %d" % number)
            return
        with self._lock:
            self._names_in_flight += 1
            self.max_names_in_flight = max(self.max_names_in_flight, self._names_in_flight)

        def reply():
            if self.name_reply_delay:
                time.sleep(self.name_reply_delay)
            with self._lock:
                self._names_in_flight -= 1
            if not self.answer_input_names:
                return
            name = self.input_names[number].encode("utf-16le")
            self._send(conn, struct.pack("<II", RESULT_INPUT_NAME, len(name) // 2) + name)
        threading.Thread(target=reply, daemon=True).start()


def section_of(key):
    return key[0]


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5001
    device = FakeSE650(realtime_port=port).start()
    print("Fake SE-650: 127.0.0.1 (discovery 5009, realtime %d, command %d)  Ctrl+C to stop" % (port, port + 1))
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        device.stop()


if __name__ == "__main__":
    main()

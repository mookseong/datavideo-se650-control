"""가짜 SE-650: 공식 문서("SE-650 Ethernet Control Protocol E1") 규칙을 그대로 따르는 시뮬레이터.

- 테스트: 클라이언트가 문서 규칙을 어기면 `violations` 에 기록한다 -> 테스트는 위반 0건을 확인
- 장비 없이 GUI 확인:  python -m se650.simulator   ->  GUI 에서 127.0.0.1 로 연결

문서대로 구현한 동작 (괄호는 문서 절 번호)
- 실시간 채널: 장비가 매 필드 패킷을 보내고, 답장을 받기 전에는 다음 패킷을 보내지 않는다 (3.2, 3.2.5)
  첫 패킷은 0 이 아닌 모든 값, 이후로는 바뀐 값만 (3.2.3). 답장에 실린 파라미터/값은 적용한다 (3.2.4.2)
- 명령 채널: 명령-응답. GET 은 SET 형식으로 응답, SET 은 응답 없음 (2.3.1, 2.3.2)
- 트랜지션 엔진: 명령은 필드 간격으로 처리 후 명령 값을 Ready(8) 로 바꾼다 (4.1.8.1.9)
- 유저 메모리: 명령 처리 중 Busy, 끝나면 Ready(0) (4.1.9.2, 4.1.9.3)

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

PGM, PST = (2, 86), (2, 87)
WIPE_LEVEL = (2, 1)                              # float
FLOAT_KEYS = {WIPE_LEVEL}
KEYERS = (((2, 19), (2, 80)), ((2, 49), (2, 81)))            # M/E 트랜지션에 포함되는 키어 (ON, 포함 플래그)
DSK1 = ((2, 91), (2, 127))                                   # DSK 트랜지션에 포함되는 DSK1 (4.1.3.5)
ENGINES = {"me": (7, 0), "dsk": (7, 5), "ftb": (7, 10)}      # 각 엔진의 COMMAND 컨트롤
TRANS_STOP, TRANS_RUN, TRANS_GOTO_END, TRANS_READY = 0, 1, 5, 8
MEMORY_SELECT, MEMORY_COMMAND, MEMORY_STATE, MEMORY_RESULT = (8, 0), (8, 1), (8, 2), (8, 3)
MEM_READY, MEM_LOAD, MEM_STORE = 0, 1, 2
MEM_STATE_READY, MEM_STATE_BUSY = 0, 1
MEM_RESULT_OK, MEM_RESULT_NOT_FOUND = 0, 2
MEMORY_FIELDS = 5                                # 메모리 명령 처리에 걸리는 필드 수


def frame(payload: bytes) -> bytes:
    return struct.pack("<I", len(payload) + 4) + payload


def read_payloads(conn):
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
            if len(buffer) < length:
                break
            yield buffer[4:length]
            buffer = buffer[length:]


def initial_state():
    """(section, control) -> value. 값이 0 인 항목은 문서상 첫 패킷에 실리지 않는다."""
    return {
        PGM: 1, PST: 2, (2, 88): 0, (2, 133): 0,
        (2, 0): 1, (2, 79): 1, (2, 84): 0,                         # 와이프 패턴, TRANS BG, REV
        (2, 19): 0, (2, 80): 0, (2, 49): 0, (2, 81): 0, (2, 91): 0, (2, 127): 0,
        (7, 0): TRANS_READY, (7, 2): 0, (7, 3): 30,
        (7, 5): TRANS_READY, (7, 7): 0, (7, 8): 30,
        (7, 10): TRANS_READY, (7, 12): 0, (7, 13): 30,
        MEMORY_SELECT: 0, MEMORY_COMMAND: MEM_READY, MEMORY_STATE: MEM_STATE_READY, MEMORY_RESULT: MEM_RESULT_OK,
        (5, 15): 4, (6, 0): 0, (6, 2): 1,                          # HDMI 출력, 오디오 소스, 오디오 모드
    }


class _RealtimeSession:
    def __init__(self, conn, first_keys):
        self.conn = conn
        self.dirty = list(first_keys)
        self.awaiting_reply = False
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
        self.command_null_mode = "silent"          # "spam": 명령 채널로 0.5초 동안 null 을 쏟아냄

        # 관찰
        self.violations = []                       # 클라이언트의 문서 규칙 위반
        self.commands_received = []                # 명령 채널로 받은 payload (null 제외)
        self.command_nulls = 0                     # 명령 채널로 받은 null
        self.realtime_replies = []                 # 실시간 채널로 받은 답장 payload
        self.wipe_levels = []                      # 받은 T-바 진행률
        self.menu_keys = []                        # 문서에 없는 0x15 명령으로 받은 값
        self.max_names_in_flight = 0

        self._lock = threading.RLock()
        self._sessions = []
        self._pending_engine = {}                  # 엔진 -> 다음 필드에 처리할 명령
        self._memory_busy_fields = 0
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

    def _accept_loop(self, server, handler):
        while not self._stop.is_set():
            try:
                conn, _ = server.accept()
            except (socket.timeout, OSError):
                continue
            conn.settimeout(None)
            self._clients.append(conn)
            threading.Thread(target=handler, args=(conn,), daemon=True).start()

    # ------------------------------------------------------------ 상태 변경 (모든 실시간 세션에 전파)
    def set_value(self, key, value):
        with self._lock:
            if self.state.get(key) == value:
                return
            self.state[key] = value
            for session in self._sessions:
                if key not in session.dirty:
                    session.dirty.append(key)

    def push(self, keys):
        """값이 그대로여도 다음 필드에 다시 보내게 한다 (테스트용)."""
        with self._lock:
            for session in self._sessions:
                session.dirty.extend(k for k in keys if k not in session.dirty)

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

    def _send_field_packet(self, session):
        if session.awaiting_reply:
            return                                  # 3.2.5: 답장 전에는 다음 패킷을 보내지 않는다
        keys, session.dirty = session.dirty, []
        command = self.first_packet_command if session.sent == 0 else 0
        payload = struct.pack("<I", command) + self._entries(keys)
        try:
            session.conn.sendall(frame(payload))
        except OSError:
            return
        session.awaiting_reply = True
        session.sent += 1

    def _process_engines(self):
        for engine, command in list(self._pending_engine.items()):
            del self._pending_engine[engine]
            if engine == "me" and command in (TRANS_RUN, TRANS_GOTO_END):
                self._complete_transition()
            elif engine == "dsk" and command in (TRANS_RUN, TRANS_GOTO_END):
                on, included = DSK1
                if self.state[included]:
                    self.set_value(on, self.state[on] ^ 1)
                    self.set_value(included, 0)
            self.set_value(ENGINES[engine], TRANS_READY)        # 4.1.8.1.9: 처리 후 Ready

    def _complete_transition(self):
        """M/E 트랜지션 완료: PGM/PVW 교체, 다음 트랜지션 포함 키어는 ON 반전 후 플래그 해제."""
        pgm, pst = self.state[PGM], self.state[PST]
        self.set_value(PGM, pst)
        self.set_value(PST, pgm)
        for on, included in KEYERS:
            if self.state[included]:
                self.set_value(on, self.state[on] ^ 1)
                self.set_value(included, 0)

    def _process_memory(self):
        if self._memory_busy_fields == 0:
            return
        self._memory_busy_fields -= 1
        if self._memory_busy_fields:
            return
        slot, command = self.state[MEMORY_SELECT], self.state[MEMORY_COMMAND]
        result = MEM_RESULT_OK
        if command == MEM_STORE:
            self.stored_memories.add(slot)
        elif command == MEM_LOAD and slot not in self.stored_memories:
            result = MEM_RESULT_NOT_FOUND
        self.set_value(MEMORY_RESULT, result)
        self.set_value(MEMORY_STATE, MEM_STATE_READY)
        self.set_value(MEMORY_COMMAND, MEM_READY)

    # ------------------------------------------------------------ 채널
    def _serve_discovery(self, conn):
        conn.recv(8)
        conn.sendall(self.discovery_reply or struct.pack("<IHH", 8, self.realtime_port, 0xAA55))
        conn.close()

    def _serve_realtime(self, conn):
        with self._lock:
            first = [k for k, v in self.state.items() if v] if self.push_on_connect else []   # 3.2.3: 0 기준
            session = _RealtimeSession(conn, first)
            self._sessions.append(session)
        for payload in read_payloads(conn):
            with self._lock:
                self.realtime_replies.append(payload)
                if not session.awaiting_reply:
                    self.violations.append("realtime: reply without a packet from the switcher (3.2.4)")
                session.awaiting_reply = False
                session.replies += 1
                self._apply_set(payload, source="realtime")          # 3.2.4.2: 답장에 실린 값 적용
        with self._lock:
            self._sessions.remove(session)
        conn.close()

    def _serve_command(self, conn):
        if self.command_null_mode == "spam":
            threading.Thread(target=self._spam_nulls, args=(conn,), daemon=True).start()
        for payload in read_payloads(conn):
            if len(payload) == 4:
                self.command_nulls += 1
                if payload == b"\x00\x00\x00\x00":           # 파라미터 없는 GET -> 파라미터 없는 SET 응답 (2.3.2)
                    self._send(conn, struct.pack("<I", CMD_SET))
                continue
            self.commands_received.append(payload)
            command = struct.unpack_from("<I", payload)[0]
            if command not in DOCUMENTED_COMMANDS:
                self.violations.append("command: undocumented command %d (2.2)" % command)
                if command == 0x15:
                    self.menu_keys.append(struct.unpack_from("<I", payload, 4)[0])
                continue
            if command == CMD_SET:
                with self._lock:
                    self._apply_set(payload, source="command")
            elif command == CMD_GET:
                self._on_get(conn, payload)
            elif command == CMD_INPUT_NAME:
                self._on_input_name(conn, payload)
        conn.close()

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
    def _apply_set(self, payload, source):
        for offset in range(4, len(payload) - 7, 8):
            control, section, value = struct.unpack_from("<HHi", payload, offset)
            key = (section, control)
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
                self.set_value(key, value)

    def _on_engine_command(self, engine, command):
        if self.state[ENGINES[engine]] != TRANS_READY or engine in self._pending_engine:
            self.violations.append("transition: %s command %d before Ready (4.1.8.1.9)" % (engine, command))
        self.state[ENGINES[engine]] = command
        self._pending_engine[engine] = command

    def _on_memory_command(self, command):
        if self.state[MEMORY_COMMAND] != MEM_READY or self.state[MEMORY_STATE] == MEM_STATE_BUSY:
            self.violations.append("memory: command %d while not Ready (4.1.9.2)" % command)
        self.set_value(MEMORY_COMMAND, command)
        self.set_value(MEMORY_STATE, MEM_STATE_BUSY)
        self._memory_busy_fields = MEMORY_FIELDS

    def _on_get(self, conn, payload):
        if self.ignore_gets > 0:
            self.ignore_gets -= 1
            return
        keys = [struct.unpack_from("<HH", payload, o)[::-1] for o in range(4, len(payload) - 3, 4)]
        with self._lock:
            body = self._entries(keys)
        self._send(conn, struct.pack("<I", CMD_SET) + body)       # 2.3.2: SET 형식으로 응답

    def _on_input_name(self, conn, payload):
        """응답을 별도 스레드에서 보내, 컨트롤러가 응답 전에 다음 요청을 보내는지(겹침) 셀 수 있게 한다."""
        with self._lock:
            self._names_in_flight += 1
            self.max_names_in_flight = max(self.max_names_in_flight, self._names_in_flight)
        number = struct.unpack_from("<I", payload, 4)[0]

        def reply():
            if self.name_reply_delay:
                time.sleep(self.name_reply_delay)
            name = self.input_names.get(number, "").encode("utf-16le")
            with self._lock:
                self._names_in_flight -= 1
            self._send(conn, struct.pack("<II", RESULT_INPUT_NAME, len(name) // 2) + name)
        threading.Thread(target=reply, daemon=True).start()


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

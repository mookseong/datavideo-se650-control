"""가짜 SE-650 (DVIP 장비 시뮬레이터).

- 단위/통합 테스트에서 사용
- 장비 없이 GUI 확인:  python -m se650.simulator   ->  GUI 에서 127.0.0.1 로 연결

일부러 se650.protocol 을 쓰지 않고 struct 로 직접 구현했다.
(우리 코덱에 버그가 있어도 시뮬레이터가 같은 버그를 따라가지 않도록 = 독립적인 기준)
"""
import socket
import struct
import sys
import threading
import time

CMD_GET, CMD_SET, CMD_INPUT_NAME, CMD_MENU = 0, 1, 9, 0x15
RESULT_INPUT_NAME = 3

PGM, PST = (2, 86), (2, 87)
ME_COMMAND = (7, 0)
WIPE_LEVEL = (2, 1)            # float
KEYERS = (((2, 19), (2, 80)), ((2, 49), (2, 81)), ((2, 91), (2, 127)))   # (ON, 다음 트랜지션 포함)
TRANSITION_RUN, TRANSITION_GOTO_END = 1, 5


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
    """(section, control) -> value"""
    return {
        PGM: 1, PST: 2, (2, 88): 0, (2, 133): 0,
        (2, 0): 1, (2, 79): 1, (2, 84): 0,            # 와이프 패턴, TRANS BG, REV
        (2, 19): 0, (2, 80): 0, (2, 49): 0, (2, 81): 0, (2, 91): 0, (2, 127): 0,
        (7, 2): 0, (7, 7): 0, (7, 12): 0, (7, 3): 30, (7, 8): 30, (7, 13): 30,
    }


class FakeSE650:
    def __init__(self, host="127.0.0.1", discovery_port=5009, realtime_port=5001, push_on_connect=True):
        self.host = host
        self.discovery_port = discovery_port
        self.realtime_port = realtime_port
        self.push_on_connect = push_on_connect
        self.state = initial_state()
        self.input_names = {1: "CAM 1", 2: "CAM 2", 3: "PC", 4: "노트북"}

        # 테스트 조절 / 관찰용
        self.discovery_reply = None          # 지정하면 포트 조회에 이 바이트를 그대로 응답
        self.first_packet_command = 0x00134E30
        self.ignore_gets = 0                 # 처음 n 번의 GET 에 응답하지 않음
        self.commands_received = []          # 명령 포트로 받은 payload
        self.realtime_replies = []           # 실시간 포트로 받은 payload (전부 null 이어야 정상)
        self.command_nulls = 0               # 명령 포트로 받은 null (접속 인사 1 + 장비가 보낸 null 에 대한 답장)
        # 명령 채널 null 동작 (실제 장비가 어떤지는 문서에 없음):
        #   "silent" 받은 null 무시 / "echo" 받은 null 을 되돌림 / "spam" 0.5초 동안 null 을 쉬지 않고 보냄
        self.command_null_mode = "silent"
        self.menu_keys = []
        self.wipe_levels = []                # 받은 T-바 진행률

        self._realtime_conn = None
        self._push_lock = threading.Lock()
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

    # ------------------------------------------------------------ 송신
    def push(self, keys, command=CMD_SET):
        """실시간 포트로 파라미터/값 패킷 전송. keys = [(section, control), ...]"""
        with self._push_lock:
            if not self._realtime_conn:
                return
            try:
                self._realtime_conn.sendall(frame(struct.pack("<I", command) + self._entries(keys)))
            except OSError:
                pass

    def _entries(self, keys):
        return b"".join(struct.pack("<HHi", control, section, self.state[(section, control)])
                        for section, control in keys if (section, control) in self.state)

    # ------------------------------------------------------------ 채널
    def _serve_discovery(self, conn):
        conn.recv(8)
        conn.sendall(self.discovery_reply or struct.pack("<IHH", 8, self.realtime_port, 0xAA55))
        conn.close()

    def _serve_realtime(self, conn):
        with self._push_lock:
            self._realtime_conn = conn
        if self.push_on_connect:                     # 문서 3.2.3: 첫 패킷 = 전체 상태
            self.push(list(self.state), command=self.first_packet_command)
        for payload in read_payloads(conn):
            self.realtime_replies.append(payload)
        conn.close()

    def _serve_command(self, conn):
        handlers = {CMD_SET: self._on_set, CMD_GET: self._on_get,
                    CMD_INPUT_NAME: self._on_input_name, CMD_MENU: self._on_menu}
        try:
            conn.sendall(frame(struct.pack("<I", CMD_SET)))   # 장비 쪽 null 한 번 -> 컨트롤러가 한 번 되돌려야 함
        except OSError:
            return
        if self.command_null_mode == "spam":
            threading.Thread(target=self._spam_nulls, args=(conn,), daemon=True).start()
        for payload in read_payloads(conn):
            if len(payload) == 4:
                self.command_nulls += 1
                if self.command_null_mode == "echo":
                    try:
                        conn.sendall(frame(payload))
                    except OSError:
                        break
                continue
            self.commands_received.append(payload)
            command = struct.unpack_from("<I", payload)[0]
            try:
                if command in handlers:
                    handlers[command](conn, payload)
            except OSError:
                break                                # stop() 중 소켓이 닫힘
        conn.close()

    @staticmethod
    def _spam_nulls(conn):
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            try:
                conn.sendall(frame(struct.pack("<I", CMD_SET)))
            except OSError:
                return

    # ------------------------------------------------------------ 명령 처리
    def _on_set(self, conn, payload):
        changed = []
        for offset in range(4, len(payload) - 7, 8):
            control, section, value = struct.unpack_from("<HHi", payload, offset)
            if (section, control) == WIPE_LEVEL:                  # T-바: 100 에 닿으면 완료 후 0 으로
                self.wipe_levels.append(struct.unpack_from("<f", payload, offset + 4)[0])
                if self.wipe_levels[-1] >= 100.0:
                    changed += self._complete_transition()
                continue
            self.state[(section, control)] = value
            changed.append((section, control))
            if (section, control) == ME_COMMAND and value in (TRANSITION_RUN, TRANSITION_GOTO_END):
                changed += self._complete_transition()
        self.push(changed)

    def _complete_transition(self):
        """트랜지션 완료: PGM/PVW 교체, '다음 트랜지션 포함' 키어는 ON 반전 후 플래그 해제."""
        self.state[PGM], self.state[PST] = self.state[PST], self.state[PGM]
        changed = [PGM, PST]
        for on, in_transition in KEYERS:
            if self.state[in_transition]:
                self.state[on] ^= 1
                self.state[in_transition] = 0
                changed += [on, in_transition]
        return changed

    def _on_get(self, conn, payload):
        if self.ignore_gets > 0:
            self.ignore_gets -= 1
            return
        keys = [struct.unpack_from("<HH", payload, o)[::-1] for o in range(4, len(payload) - 3, 4)]
        conn.sendall(frame(struct.pack("<I", CMD_SET) + self._entries(keys)))   # 문서 2.3.2

    def _on_input_name(self, conn, payload):
        number = struct.unpack_from("<I", payload, 4)[0]
        name = self.input_names.get(number, "").encode("utf-16le")
        conn.sendall(frame(struct.pack("<II", RESULT_INPUT_NAME, len(name) // 2) + name))

    def _on_menu(self, conn, payload):
        self.menu_keys.append(struct.unpack_from("<I", payload, 4)[0])


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5001
    device = FakeSE650(realtime_port=port).start()
    print("가짜 SE-650 실행 중: 127.0.0.1 (조회 5009, 실시간 %d, 명령 %d)  Ctrl+C 로 종료" % (port, port + 1))
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        device.stop()


if __name__ == "__main__":
    main()

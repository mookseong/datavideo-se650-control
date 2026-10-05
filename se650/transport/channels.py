"""실시간 채널 / 명령 채널. 공식 문서 2장, 3장.

두 채널의 차이는 "받은 패킷에 어떻게 답하는가" 뿐이다.
- 실시간 채널: 장비는 우리 답장을 받아야 다음 패킷을 보낸다 -> 받은 모든 패킷에 null 로 답한다.
               요청하지 않은 패킷은 절대 보내지 않는다 (명령을 보내면 동기가 깨짐, 문서 2.1.2).
- 명령 채널  : 장비가 보낸 null 패킷은 그대로 되돌려 준다. 명령은 여기로 보낸다.
               단, 되돌리기는 속도를 제한한다. 공식 문서에는 명령 채널 null 규칙이 없고(레퍼런스 구현의 동작),
               장비도 null 에 응답하는 경우 서로 되돌리며 초당 수만 패킷이 오가 장비가 멈출 수 있다.
"""
import logging
import socket
import threading
import time

from ..i18n import tr
from .framing import NULL_PAYLOAD, FrameDecoder, encode_frame, is_null

log = logging.getLogger(__name__)


class Channel:
    """소켓 하나 + 수신 스레드. 완성된 payload 를 on_packet 으로 넘긴다."""

    def __init__(self, sock: socket.socket, name: str, on_packet, on_closed):
        self._sock = sock
        self._name = name
        self._on_packet = on_packet
        self._on_closed = on_closed
        self._send_lock = threading.Lock()
        self._closing = threading.Event()
        self._thread = threading.Thread(target=self._receive_loop, name=name, daemon=True)

    def start(self):
        self._thread.start()

    def send(self, payload: bytes):
        with self._send_lock:
            self._sock.sendall(encode_frame(payload))

    def close(self):
        self._closing.set()
        for action in (lambda: self._sock.shutdown(socket.SHUT_RDWR), self._sock.close):
            try:
                action()
            except OSError:
                pass

    def handle(self, payload: bytes):
        raise NotImplementedError

    def _receive_loop(self):
        decoder = FrameDecoder()
        try:
            while True:
                data = self._sock.recv(4096)
                if not data:
                    break
                for payload in decoder.feed(data):
                    self.handle(payload)
        except OSError:
            pass
        if not self._closing.is_set():
            log.debug(tr("transport.log.channel_ended", channel=self._name))
            self._on_closed()


class RealtimeChannel(Channel):
    def start(self):
        self.send(NULL_PAYLOAD)          # 접속 직후 첫 null
        super().start()

    def handle(self, payload: bytes):
        try:
            self.send(NULL_PAYLOAD)      # 장비 패킷마다 정확히 한 번 답장
        except OSError:
            return
        if not is_null(payload):
            self._on_packet(payload)


class CommandChannel(Channel):
    NULL_REPLY_INTERVAL = 0.1        # null 되돌리기 최소 간격 (초당 최대 10회)
    FLOOD_WARNING_COUNT = 50         # 1초에 이만큼 null 을 무시하면 경고 (장비가 null 에 응답하는 기기)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._last_null_reply = 0.0
        self._ignored_nulls = 0
        self._ignored_since = time.monotonic()
        self._flood_warned = False
        self.null_replies = 0        # 진단용 카운터
        self.nulls_ignored = 0

    def start(self):
        self.send(NULL_PAYLOAD)
        super().start()

    def handle(self, payload: bytes):
        if is_null(payload):
            self._answer_null(payload)
            return
        self._on_packet(payload)

    def _answer_null(self, payload: bytes):
        now = time.monotonic()
        if now - self._last_null_reply < self.NULL_REPLY_INTERVAL:
            self._count_ignored(now)
            return
        self._last_null_reply = now
        self.null_replies += 1
        try:
            self.send(payload)           # null echo
        except OSError:
            pass

    def _count_ignored(self, now: float):
        self.nulls_ignored += 1
        if now - self._ignored_since > 1.0:
            self._ignored_since, self._ignored_nulls = now, 0
        self._ignored_nulls += 1
        if self._ignored_nulls >= self.FLOOD_WARNING_COUNT and not self._flood_warned:
            self._flood_warned = True
            log.warning(tr("transport.log.null_flood", count=self._ignored_nulls))

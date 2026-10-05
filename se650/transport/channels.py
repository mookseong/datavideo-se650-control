"""실시간 채널 / 명령 채널. 공식 문서 2장, 3장.

두 채널의 차이는 "받은 패킷에 어떻게 답하는가" 뿐이다.
- 실시간 채널 (3.2): 장비가 먼저, 매 필드 패킷을 보낸다. 장비는 답장을 받아야 다음 패킷을 보낸다.
                    -> 받은 패킷마다 정확히 한 번 답한다. 먼저 보내거나 더 보내지 않는다.
                    답장은 null 이거나, 보낼 값이 있으면 파라미터/값 패킷이다 (3.2.4.2).
- 명령 채널 (2.2): 명령-응답 방식. 컨트롤러가 명령을 보내고 장비가 응답한다.
                    문서에 null 패킷 규칙이 없으므로 null 은 보내지도, 되돌리지도 않는다.
"""
import logging
import socket
import threading

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
    """reply_provider() 가 bytes 를 돌려주면 그 값을 답장에 싣고, None 이면 null 로 답한다."""

    def __init__(self, sock, name, on_packet, on_closed, reply_provider=None):
        super().__init__(sock, name, on_packet, on_closed)
        self._reply_provider = reply_provider or (lambda: None)

    def handle(self, payload: bytes):
        try:
            self.send(self._reply_provider() or NULL_PAYLOAD)     # 장비 패킷마다 정확히 한 번
        except OSError:
            return
        if not is_null(payload):
            self._on_packet(payload)


class CommandChannel(Channel):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.nulls_received = 0          # 진단용: 문서에 없는 null 이 장비에서 온 횟수

    def handle(self, payload: bytes):
        if is_null(payload):
            self.nulls_received += 1     # 응답하지 않는다 (명령-응답 방식에서 응답에 다시 응답할 이유가 없다)
            return
        self._on_packet(payload)

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
from .framing import NULL_PAYLOAD, FrameDecoder, FramingError, encode_frame, is_null

log = logging.getLogger(__name__)


class Channel:
    """소켓 하나 + 수신 스레드. 완성된 payload 를 on_packet 으로 넘긴다.

    소켓 제한 시간은 송신과 수신에 함께 걸린다.
    - 송신이 실패하거나 시간을 넘기면: 프레임이 반쯤 나갔을 수 있으므로(스트림 동기 상실) 즉시 연결을 끝낸다.
    - 수신이 시간을 넘기면: IDLE_IS_NORMAL 이면 계속 기다리고(명령 채널은 평소에도 조용함),
      아니면 장비가 멈춘 것으로 보고 연결을 끝낸다(실시간 채널은 매 필드 패킷이 와야 정상).
    """
    IDLE_IS_NORMAL = False

    def __init__(self, sock: socket.socket, name: str, on_packet, on_closed):
        self._sock = sock
        self._name = name
        self._on_packet = on_packet
        self._on_closed = on_closed
        self._send_lock = threading.Lock()
        self._closing = threading.Event()
        self._failed = threading.Lock()
        self._thread = threading.Thread(target=self._receive_loop, name=name, daemon=True)

    def start(self):
        self._thread.start()

    def send(self, payload: bytes):
        with self._send_lock:
            try:
                self._sock.sendall(encode_frame(payload))
            except OSError:
                self._fail()
                raise

    def close(self):
        self._closing.set()
        self._shutdown()

    def _shutdown(self):
        for action in (lambda: self._sock.shutdown(socket.SHUT_RDWR), self._sock.close):
            try:
                action()
            except OSError:
                pass

    def _fail(self):
        """한 번만: 소켓을 닫고 연결 전체에 알린다 (연결이 나머지 채널도 닫는다)."""
        if self._closing.is_set() or not self._failed.acquire(blocking=False):
            return
        self._shutdown()
        log.debug(tr("transport.log.channel_ended", channel=self._name))
        self._on_closed()

    def handle(self, payload: bytes):
        raise NotImplementedError

    def _receive_loop(self):
        """어떤 이유로든 이 루프가 끝나면 연결 전체를 닫는다.

        수신 스레드만 조용히 죽으면 소켓은 열린 채 장비 패킷에 답하지 못해, 장비의 실시간 채널이 멈추고
        컨트롤러 채널도 반납되지 않는다. 그래서 모든 예외를 잡아 연결을 끝낸다.
        """
        decoder = FrameDecoder()
        try:
            while True:
                try:
                    data = self._sock.recv(4096)
                except socket.timeout:
                    if self.IDLE_IS_NORMAL:
                        continue
                    raise
                if not data:
                    break
                for payload in decoder.feed(data):
                    self.handle(payload)
        except socket.timeout:
            log.warning(tr("transport.log.silent", channel=self._name))
        except FramingError as error:
            log.error(tr("transport.log.bad_frame", channel=self._name, error=error))
        except OSError:
            pass
        except Exception:      # noqa: BLE001 - 패킷 처리 중 어떤 오류든 연결을 정리해야 한다
            log.exception(tr("transport.log.handler_error", channel=self._name))
        self._fail()


class RealtimeChannel(Channel):
    """reply_provider() 가 bytes 를 돌려주면 그 값을 답장에 싣고, None 이면 null 로 답한다."""

    def __init__(self, sock, name, on_packet, on_closed, reply_provider=None):
        super().__init__(sock, name, on_packet, on_closed)
        self._reply_provider = reply_provider or (lambda: None)

    def handle(self, payload: bytes):
        # 장비 패킷마다 정확히 한 번 답한다. 답장을 못 보내면 장비가 기다리므로 예외를 그대로 올려 연결을 끝낸다.
        self.send(self._reply_provider() or NULL_PAYLOAD)
        if not is_null(payload):
            self._on_packet(payload)


class CommandChannel(Channel):
    IDLE_IS_NORMAL = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.nulls_received = 0          # 진단용: 문서에 없는 null 이 장비에서 온 횟수

    def handle(self, payload: bytes):
        if is_null(payload):
            self.nulls_received += 1     # 응답하지 않는다 (명령-응답 방식에서 응답에 다시 응답할 이유가 없다)
            return
        self._on_packet(payload)

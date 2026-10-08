"""DVIP 연결: 포트 조회 + 실시간/명령 채널의 수명 관리."""
import logging
import socket
import threading

from ..i18n import LocalizedError, tr
from .channels import CommandChannel, RealtimeChannel
from .discovery import DISCOVERY_PORT, REALTIME_PORTS, DiscoveryError, request_realtime_port

log = logging.getLogger(__name__)


class NotConnectedError(LocalizedError, ConnectionError):
    def __init__(self):
        super().__init__("transport.error.not_connected")


class ConnectCancelledError(LocalizedError, ConnectionError):
    """연결하는 도중 close() 가 불렸다. 만든 소켓은 이미 닫았다."""

    def __init__(self):
        super().__init__("transport.error.cancelled")


class DvipConnection:
    """
    host          : 스위처 IP
    realtime_port : 지정하면 포트 조회를 건너뛴다 (명령 포트 = realtime_port + 1)
    discovery_port: 포트 조회 채널 (기본 5009)
    allowed_ports : 실시간 포트로 받아들일 번호. 조회 결과와 직접 지정한 포트 모두 검사 (None = 제한 없음)
    silence_limit : 장비는 매 필드(1/50~1/60초) 실시간 패킷을 보낸다. 이만큼 아무것도 없으면 끊긴 것으로 본다
                    (메모리 불러오기 중 잠깐 멈출 수 있어 여유를 둠 - 실제 장비로 확인 권장)
    send_timeout  : 명령 채널 송신 제한 시간. 장비가 받지 않아 송신이 막히면 연결을 끝낸다 (화면이 멈추지 않게)

    연결 객체는 한 번만 쓴다: close() 뒤에는 open() 할 수 없다 (재연결은 새 객체로).
    """

    def __init__(self, host: str, realtime_port: int = None, discovery_port: int = DISCOVERY_PORT,
                 allowed_ports=REALTIME_PORTS, timeout: float = 3.0, silence_limit: float = 5.0,
                 send_timeout: float = 5.0):
        if realtime_port is not None and allowed_ports is not None and realtime_port not in allowed_ports:
            # 짝수(명령) 포트에 실시간 프로토콜을 쓰면 결국 동기가 깨진다 (2.1.2)
            raise DiscoveryError("transport.error.unknown_port", port=realtime_port)
        self.host = host
        self.realtime_port = realtime_port
        self._discovery_port = discovery_port
        self._allowed_ports = allowed_ports
        self._timeout = timeout
        self._silence_limit = silence_limit
        self._send_timeout = send_timeout
        self._realtime = None
        self._command = None
        self._closed_once = threading.Lock()
        self._lifecycle = threading.Lock()       # open 과 close 가 엇갈리지 않게
        self._closing = False
        self.connected = False

    @property
    def command_port(self):
        return self.realtime_port + 1 if self.realtime_port else None

    def open(self, on_realtime_packet, on_command_packet, on_closed, realtime_reply=None):
        """realtime_reply(): 실시간 답장에 실을 payload (없으면 None -> null 답장).

        연결 중에 close() 가 불리면 만든 소켓을 모두 닫고 ConnectCancelledError 를 낸다.
        (예전에는 close() 뒤에 open() 이 끝나 연결이 살아남았다 = 장비 채널 누수)
        """
        self._raise_if_closing()                 # close() 가 open() 보다 먼저 왔어도 연결하지 않는다
        if self.realtime_port is None:
            self.realtime_port = request_realtime_port(
                self.host, self._discovery_port, self._timeout, self._allowed_ports)
        self._raise_if_closing()
        log.info(tr("transport.log.ports", realtime=self.realtime_port, command=self.command_port))

        sockets = []
        try:
            sockets.append(self._connect(self.realtime_port, timeout=self._silence_limit))
            self._raise_if_closing()
            sockets.append(self._connect(self.command_port, timeout=self._send_timeout))
            with self._lifecycle:
                self._raise_if_closing()
                self._closed_once = threading.Lock()
                self._on_closed = on_closed
                self._realtime = RealtimeChannel(sockets[0], "realtime", on_realtime_packet, self._handle_closed,
                                                 realtime_reply)
                self._command = CommandChannel(sockets[1], "command", on_command_packet, self._handle_closed)
                self.connected = True
                self._command.start()
                self._realtime.start()
        except BaseException:
            for sock in sockets:
                sock.close()
            raise
        log.info(tr("transport.log.connected", host=self.host))

    def _raise_if_closing(self):
        if self._closing:
            raise ConnectCancelledError()

    def send_command(self, payload: bytes):
        if not self.connected or self._command is None:
            raise NotConnectedError()
        self._command.send(payload)

    @property
    def diagnostics(self) -> dict:
        return {"nulls_received": getattr(self._command, "nulls_received", 0)}

    def close(self):
        with self._lifecycle:
            self._closing = True                       # 진행 중인 open() 은 소켓을 닫고 중단한다
            was_connected = self.connected
            self.connected = False
            self._closed_once.acquire(blocking=False)  # 의도적 종료는 on_closed 를 부르지 않는다
            channels = (self._realtime, self._command)
        for channel in channels:
            if channel:
                channel.close()
        if was_connected:
            log.info(tr("transport.log.closed_with_stats", **self.diagnostics))

    def _connect(self, port: int, timeout) -> socket.socket:
        """timeout: 연결 후 송수신 제한 시간. 실시간 채널은 침묵 감지, 명령 채널은 송신 막힘 감지용
        (명령 채널의 수신 시간 초과는 정상 - CommandChannel 이 계속 기다린다)."""
        sock = socket.create_connection((self.host, port), timeout=self._timeout)
        sock.settimeout(timeout)
        return sock

    def _handle_closed(self):
        """두 채널 중 하나라도 끊기면 한 번만 통지하고 나머지도 닫는다."""
        if not self._closed_once.acquire(blocking=False):
            return
        self.connected = False
        for channel in (self._realtime, self._command):
            if channel:
                channel.close()
        log.warning(tr("transport.log.lost"))
        self._on_closed()

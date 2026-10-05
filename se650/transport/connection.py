"""DVIP 연결: 포트 조회 + 실시간/명령 채널의 수명 관리."""
import logging
import socket
import threading

from ..i18n import LocalizedError, tr
from .channels import CommandChannel, RealtimeChannel
from .discovery import DISCOVERY_PORT, REALTIME_PORTS, request_realtime_port

log = logging.getLogger(__name__)


class NotConnectedError(LocalizedError, ConnectionError):
    def __init__(self):
        super().__init__("transport.error.not_connected")


class DvipConnection:
    """
    host          : 스위처 IP
    realtime_port : 지정하면 포트 조회를 건너뛴다 (명령 포트 = realtime_port + 1)
    discovery_port: 포트 조회 채널 (기본 5009)
    allowed_ports : 포트 조회로 받아들일 실시간 포트 (None = 제한 없음)
    """

    def __init__(self, host: str, realtime_port: int = None, discovery_port: int = DISCOVERY_PORT,
                 allowed_ports=REALTIME_PORTS, timeout: float = 3.0):
        self.host = host
        self.realtime_port = realtime_port
        self._discovery_port = discovery_port
        self._allowed_ports = allowed_ports
        self._timeout = timeout
        self._realtime = None
        self._command = None
        self._closed_once = threading.Lock()
        self.connected = False

    @property
    def command_port(self):
        return self.realtime_port + 1 if self.realtime_port else None

    def open(self, on_realtime_packet, on_command_packet, on_closed, realtime_reply=None):
        """realtime_reply(): 실시간 답장에 실을 payload (없으면 None -> null 답장)."""
        if self.realtime_port is None:
            self.realtime_port = request_realtime_port(
                self.host, self._discovery_port, self._timeout, self._allowed_ports)
        log.info(tr("transport.log.ports", realtime=self.realtime_port, command=self.command_port))

        self._closed_once = threading.Lock()
        self._on_closed = on_closed
        rt_sock = self._connect(self.realtime_port)
        try:
            cmd_sock = self._connect(self.command_port)
        except OSError:
            rt_sock.close()
            raise
        self._realtime = RealtimeChannel(rt_sock, "realtime", on_realtime_packet, self._handle_closed,
                                         realtime_reply)
        self._command = CommandChannel(cmd_sock, "command", on_command_packet, self._handle_closed)
        self.connected = True
        self._command.start()
        self._realtime.start()
        log.info(tr("transport.log.connected", host=self.host))

    def send_command(self, payload: bytes):
        if not self.connected or self._command is None:
            raise NotConnectedError()
        self._command.send(payload)

    @property
    def diagnostics(self) -> dict:
        return {"nulls_received": getattr(self._command, "nulls_received", 0)}

    def close(self):
        was_connected = self.connected
        self.connected = False
        self._closed_once.acquire(blocking=False)      # 의도적 종료는 on_closed 를 부르지 않는다
        for channel in (self._realtime, self._command):
            if channel:
                channel.close()
        if was_connected:
            log.info(tr("transport.log.closed_with_stats", **self.diagnostics))

    def _connect(self, port: int) -> socket.socket:
        sock = socket.create_connection((self.host, port), timeout=self._timeout)
        sock.settimeout(None)
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

"""테스트 공용 도구."""
import socket
import time

from se650.control import Switcher
from se650.simulator import FakeSE650
from se650.transport import DvipConnection


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def free_port_pair() -> int:
    """p 와 p+1 이 모두 비어 있는 p."""
    for _ in range(50):
        port = free_port()
        try:
            with socket.socket() as s:
                s.bind(("127.0.0.1", port + 1))
            return port
        except OSError:
            continue
    raise RuntimeError("빈 포트를 찾지 못했습니다")


def wait_for(condition, timeout=2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


class DeviceFixture:
    """가짜 장비 + 그 장비에 붙는 연결/스위처를 만든다."""

    def __init__(self, push_on_connect=True):
        self.realtime_port = free_port_pair()
        self.discovery_port = free_port()
        self.device = FakeSE650(discovery_port=self.discovery_port, realtime_port=self.realtime_port,
                                push_on_connect=push_on_connect).start()

    def connection(self, **overrides) -> DvipConnection:
        options = dict(discovery_port=self.discovery_port, allowed_ports=(self.realtime_port,))
        options.update(overrides)
        return DvipConnection("127.0.0.1", **options)

    def switcher(self, on_change=None, retry_interval=0.25) -> Switcher:
        switcher = Switcher(self.connection(), on_change=on_change)
        switcher.STATE_RETRY_INTERVAL = retry_interval
        return switcher

    def factory(self, retry_interval=0.25):
        """MainWindow / run_cli 에 넘길 switcher_factory(host, on_change=None)."""
        def make(host, on_change=None):
            return self.switcher(on_change, retry_interval)
        return make

    def stop(self):
        self.device.stop()


class RecordingConnection:
    """소켓 없이 Switcher 를 검사하기 위한 가짜 Connection."""

    def __init__(self, host="test"):
        self.host = host
        self.connected = False
        self.sent = []
        self._callbacks = None

    def open(self, on_realtime_packet, on_command_packet, on_closed):
        self._callbacks = (on_realtime_packet, on_command_packet, on_closed)
        self.connected = True

    def send_command(self, payload):
        if not self.connected:
            raise ConnectionError("연결되어 있지 않습니다")
        self.sent.append(payload)

    def close(self):
        self.connected = False

    # 장비 쪽 동작 흉내
    def receive_realtime(self, payload):
        self._callbacks[0](payload)

    def receive_command(self, payload):
        self._callbacks[1](payload)

    def drop(self):
        self.connected = False
        self._callbacks[2]()

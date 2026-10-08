"""시뮬레이터 자체 검사: 문서 규칙 위반을 실제로 잡아내는가.

시뮬레이터가 느슨하면 클라이언트 버그가 테스트를 그냥 통과한다 (실제로 그런 일이 있었다).
여기서는 일부러 규칙을 어기는 원시 패킷을 보내 위반이 기록되는지 확인한다.
"""
import socket
import struct
import time
import unittest

from se650.simulator import DISCOVERY_REQUEST, FakeSE650, frame

from .helpers import free_port, free_port_pair, wait_for


def set_packet(*items, floats=()):
    body = b""
    for section, control, value in items:
        fmt = "<HHf" if (section, control) in floats else "<HHi"
        body += struct.pack(fmt, control, section, value)
    return frame(struct.pack("<I", 1) + body)


class SimulatorStrictnessTests(unittest.TestCase):
    def setUp(self):
        self.port = free_port_pair()
        self.discovery = free_port()
        self.device = FakeSE650(discovery_port=self.discovery, realtime_port=self.port).start()
        self.socks = []

    def tearDown(self):
        for sock in self.socks:
            sock.close()
        self.device.stop()

    def connect(self, port):
        sock = socket.create_connection(("127.0.0.1", port), timeout=2)
        self.socks.append(sock)
        return sock

    def command(self, packet):
        sock = self.connect(self.port + 1)
        sock.sendall(packet)
        return sock

    def expect(self, fragment):
        self.assertTrue(wait_for(lambda: any(fragment in v for v in self.device.violations)),
                        "%r not in %r" % (fragment, self.device.violations))

    def test_out_of_range_and_undocumented_values(self):
        self.command(set_packet((2, 86, 99), (2, 0, 0), (8, 0, 1000), (6, 2, 7), (7, 3, 0)))
        for fragment in ("(2, 86) value 99", "(2, 0) value 0", "(8, 0) value 1000", "(6, 2) value 7",
                         "(7, 3) value 0"):
            self.expect(fragment)

    def test_read_only_controls(self):
        self.command(set_packet((7, 2, 2), (8, 2, 0), (0, 27, 1)))
        self.expect("read-only control (7, 2)")
        self.expect("read-only control (8, 2)")
        self.expect("read-only control (0, 27)")

    def test_int_where_float_expected(self):
        self.command(set_packet((2, 1, 50)))
        self.expect("int sent where float")

    def test_null_and_undocumented_command(self):
        sock = self.command(frame(struct.pack("<I", 1)))
        sock.sendall(frame(struct.pack("<II", 0x15, 3)))
        self.expect("null packet on command channel")
        self.expect("undocumented command 21")

    def test_invalid_packet_size(self):
        self.command(struct.pack("<I", 0) + b"xxxx")
        self.expect("invalid packet size 0")

    def test_engine_command_before_ready(self):
        self.device.engine_fields = 50
        self.command(set_packet((7, 0, 5)) + set_packet((7, 0, 5)))
        self.expect("before Ready")

    def test_memory_command_while_busy(self):
        self.device.memory_fields = 50
        sock = self.command(set_packet((8, 0, 1), (8, 1, 1)))
        self.assertTrue(wait_for(lambda: self.device.state[(8, 2)] == 1))          # Busy
        sock.sendall(set_packet((8, 0, 2), (8, 1, 1)))
        self.expect("memory: command 1 while not Ready")

    def test_realtime_reply_without_packet_and_stall(self):
        sock = self.connect(self.port)
        sock.sendall(frame(struct.pack("<I", 0)) * 3)                               # 받기 전에 답장
        self.expect("reply without a packet")

    def test_unanswered_realtime_packet_is_reported(self):
        self.connect(self.port)                                                     # 받기만 하고 답장 안 함
        time.sleep(2.3)
        self.expect("channel stalled")

    def test_second_controller_on_same_port_pair(self):
        self.connect(self.port)
        time.sleep(0.05)
        self.connect(self.port)
        self.expect("realtime port already in use")

    def test_discovery_reports_no_free_channel_while_in_use(self):
        self.connect(self.port)
        time.sleep(0.05)
        sock = self.connect(self.discovery)
        sock.sendall(DISCOVERY_REQUEST)
        self.assertEqual(sock.recv(8), struct.pack("<IHH", 8, 0xFFFF, 0xAA55))

    def test_malformed_discovery_request(self):
        sock = self.connect(self.discovery)
        sock.sendall(struct.pack("<II", 8, 0x12345678))
        self.assertEqual(sock.recv(8), struct.pack("<II", 8, 0x55AA0001))
        self.expect("discovery: malformed request")

    def test_flooding_is_reported(self):
        sock = self.command(b"")
        sock.sendall(set_packet((2, 87, 3)) * 70)
        self.expect("flooding")

    def test_echo_sets_mode_answers_sets(self):
        self.device.echo_sets = True
        sock = self.command(set_packet((2, 87, 3)))
        sock.settimeout(1)
        self.assertEqual(sock.recv(64), frame(struct.pack("<I", 1) + struct.pack("<HHi", 87, 2, 3)))

    def test_change_detection_hides_sub_field_round_trip(self):
        """3.2.3: 마지막으로 보낸 값 기준. 8 -> 5 -> 8 이 한 필드 안이면 실시간으로 보이지 않는다."""
        sock = self.connect(self.port)
        sock.settimeout(1)
        first = sock.recv(65536)
        sock.sendall(frame(struct.pack("<I", 0)))
        self.assertIn(struct.pack("<HHi", 0, 7, 8), first)                       # 첫 패킷: ME 명령 = 8
        self.command(set_packet((7, 0, 5)))
        seen = b""
        for _ in range(10):
            seen += sock.recv(65536)
            sock.sendall(frame(struct.pack("<I", 0)))
        self.assertNotIn(struct.pack("<HHi", 0, 7, 5), seen)
        self.assertNotIn(struct.pack("<HHi", 0, 7, 8), seen)


if __name__ == "__main__":
    unittest.main()

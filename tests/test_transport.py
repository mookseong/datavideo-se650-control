"""전송 계층: 프레이밍, 포트 조회, 채널 답장 규칙, 연결 수명."""
import struct
import threading
import time
import unittest

from se650.transport import (DiscoveryError, DvipConnection, FrameDecoder, NotConnectedError, encode_frame,
                             request_realtime_port)
from se650.transport.discovery import parse_discovery_reply

from .helpers import DeviceFixture, wait_for


class FramingTests(unittest.TestCase):
    def test_encode_prefixes_total_length(self):
        self.assertEqual(encode_frame(b"\x01\x00\x00\x00"), bytes.fromhex("08000000 01000000"))

    def test_two_frames_in_one_chunk(self):
        a, b = encode_frame(b"AAAA"), encode_frame(b"BBBBBBBB")
        self.assertEqual(FrameDecoder().feed(a + b), [b"AAAA", b"BBBBBBBB"])

    def test_frame_split_across_chunks(self):
        data = encode_frame(b"0123456789")
        decoder = FrameDecoder()
        self.assertEqual(decoder.feed(data[:3]), [])
        self.assertEqual(decoder.feed(data[3:9]), [])
        self.assertEqual(decoder.feed(data[9:]), [b"0123456789"])

    def test_invalid_length_resets_stream(self):
        decoder = FrameDecoder()
        self.assertEqual(decoder.feed(struct.pack("<I", 2) + b"zz"), [])
        self.assertEqual(decoder.feed(encode_frame(b"OK!!")), [b"OK!!"])

    def test_oversized_frame_rejected(self):
        decoder = FrameDecoder(max_frame_size=16)
        self.assertEqual(decoder.feed(struct.pack("<I", 100) + b"x" * 96), [])


class DiscoveryReplyTests(unittest.TestCase):
    def test_port_reply(self):
        self.assertEqual(parse_discovery_reply(struct.pack("<IHH", 8, 5003, 0xAA55)), 5003)

    def test_no_free_port(self):
        with self.assertRaises(DiscoveryError) as ctx:
            parse_discovery_reply(struct.pack("<IHH", 8, 0xFFFF, 0xAA55))
        self.assertEqual(ctx.exception.key, "transport.error.no_free_port")

    def test_error_tag(self):
        with self.assertRaises(DiscoveryError) as ctx:
            parse_discovery_reply(struct.pack("<II", 8, 0x55AA0001))
        self.assertEqual(ctx.exception.key, "transport.error.discovery_error_reply")
        self.assertEqual(ctx.exception.params, {"reply": "0100aa55"})

    def test_unexpected_port(self):
        with self.assertRaises(DiscoveryError):
            parse_discovery_reply(struct.pack("<IHH", 8, 6000, 0xAA55))
        self.assertEqual(parse_discovery_reply(struct.pack("<IHH", 8, 6000, 0xAA55), allowed_ports=None), 6000)

    def test_short_reply(self):
        with self.assertRaises(DiscoveryError):
            parse_discovery_reply(b"\x08\x00")


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.fx = DeviceFixture()
        self.realtime, self.command, self.closed = [], [], threading.Event()
        self.conn = self.fx.connection()

    def tearDown(self):
        self.conn.close()
        self.fx.stop()

    def open(self):
        self.conn.open(self.realtime.append, self.command.append, self.closed.set)

    def test_discovery_against_device(self):
        port = request_realtime_port("127.0.0.1", self.fx.discovery_port, allowed_ports=None)
        self.assertEqual(port, self.fx.realtime_port)

    def test_open_uses_discovered_port_pair(self):
        self.open()
        self.assertTrue(self.conn.connected)
        self.assertEqual((self.conn.realtime_port, self.conn.command_port),
                         (self.fx.realtime_port, self.fx.realtime_port + 1))

    def test_fixed_port_skips_discovery(self):
        conn = DvipConnection("127.0.0.1", realtime_port=self.fx.realtime_port, discovery_port=1)
        try:
            conn.open(self.realtime.append, self.command.append, self.closed.set)
            self.assertTrue(conn.connected)
        finally:
            conn.close()

    def test_first_realtime_packet_delivered(self):
        self.open()
        self.assertTrue(wait_for(lambda: self.realtime))
        self.assertGreater(len(self.realtime[0]), 8)

    def test_every_realtime_packet_answered_exactly_once(self):
        """문서 3.2.5: 장비 패킷마다 답장 1개, 요청 없는 패킷은 보내지 않음."""
        self.open()
        wait_for(lambda: self.realtime)
        for _ in range(5):
            self.fx.device.push([(2, 86)])
            time.sleep(0.03)
        expected = 1 + 1 + 5            # 접속 직후 null + 첫 패킷 답장 + 푸시 5
        self.assertTrue(wait_for(lambda: len(self.fx.device.realtime_replies) >= expected))
        time.sleep(0.3)
        self.assertEqual(len(self.fx.device.realtime_replies), expected)

    def test_only_null_packets_on_realtime_port(self):
        """문서 2.1.2: 실시간 포트에 명령 프로토콜을 쓰면 동기가 깨진다."""
        self.open()
        self.conn.send_command(struct.pack("<I", 0) + struct.pack("<HH", 86, 2))
        time.sleep(0.3)
        self.assertTrue(all(p == b"\x00\x00\x00\x00" for p in self.fx.device.realtime_replies))

    def test_command_null_answered_exactly_once(self):
        """명령 채널: 접속 인사 null 1 + 장비 null 에 대한 답장 1. 그 이상이면 핑퐁 버그."""
        self.open()
        self.assertTrue(wait_for(lambda: self.fx.device.command_nulls >= 2))
        time.sleep(0.3)
        self.assertEqual(self.fx.device.command_nulls, 2)

    def test_send_command_reaches_command_port(self):
        self.open()
        self.conn.send_command(struct.pack("<II", 0x15, 3))
        self.assertTrue(wait_for(lambda: self.fx.device.menu_keys == [3]))

    def test_send_before_open_raises(self):
        with self.assertRaises(NotConnectedError) as ctx:
            self.conn.send_command(b"\x01\x00\x00\x00")
        self.assertEqual(ctx.exception.key, "transport.error.not_connected")

    def test_device_drop_notifies_once(self):
        calls = []
        self.conn.open(self.realtime.append, self.command.append, lambda: calls.append(1))
        self.fx.stop()
        self.assertTrue(wait_for(lambda: calls, 3.0))
        time.sleep(0.2)
        self.assertEqual(calls, [1])
        self.assertFalse(self.conn.connected)

    def test_intentional_close_does_not_notify(self):
        self.open()
        self.conn.close()
        time.sleep(0.2)
        self.assertFalse(self.closed.is_set())

    def test_discovery_failure_propagates(self):
        self.fx.device.discovery_reply = struct.pack("<IHH", 8, 0xFFFF, 0xAA55)
        with self.assertRaises(DiscoveryError):
            self.open()
        self.assertFalse(self.conn.connected)


if __name__ == "__main__":
    unittest.main()

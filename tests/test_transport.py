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

    def test_repeated_connect_disconnect_releases_device_sockets(self):
        """연결/해제를 반복해도 장비 쪽에 열린 연결이 남지 않는다 (채널 반납)."""
        self.conn.close()
        for _ in range(5):
            conn = self.fx.connection()
            conn.open(lambda p: None, lambda p: None, lambda: None)
            conn.close()
            self.assertTrue(wait_for(lambda: all(c.fileno() == -1 for c in self.fx.device._clients)),
                            "장비 쪽 소켓이 닫히지 않음")

    # ---------------------------------------------------------------- 실시간 채널 규칙 (3.2)
    def test_realtime_reply_count_matches_device_packets(self):
        """장비 패킷마다 답장 정확히 1개 (3.2.5). 장비보다 먼저 보내지 않는다 (3.2.4)."""
        self.open()
        time.sleep(0.5)                                  # 50Hz -> 약 25 패킷
        session = self.fx.device._sessions[0]
        self.assertGreater(session.sent, 10)
        self.assertIn(session.sent - session.replies, (0, 1))      # 1 = 마지막 패킷의 답장이 오는 중
        self.fx.assert_compliant(self)

    def test_realtime_replies_are_null_without_payload(self):
        self.open()
        time.sleep(0.2)
        self.assertTrue(all(p == b"\x00\x00\x00\x00" for p in self.fx.device.realtime_replies))

    def test_realtime_reply_carries_provided_payload_once(self):
        payloads = [struct.pack("<I", 0) + struct.pack("<HHi", 86, 2, 4)]
        self.conn.open(self.realtime.append, self.command.append, self.closed.set,
                       realtime_reply=lambda: payloads.pop() if payloads else None)
        self.assertTrue(wait_for(lambda: self.fx.device.state[(2, 86)] == 4))     # 답장에 실은 값 적용 (3.2.4.2)
        time.sleep(0.2)
        self.assertEqual(sum(1 for p in self.fx.device.realtime_replies if len(p) > 4), 1)
        self.fx.assert_compliant(self)

    # ---------------------------------------------------------------- 명령 채널 규칙 (2.2)
    def test_no_null_packets_sent_on_command_channel(self):
        """문서에 없는 null 을 명령 채널로 보내지 않는다 (예전: 접속 시 빈 GET)."""
        self.open()
        time.sleep(0.3)
        self.assertEqual(self.fx.device.command_nulls, 0)

    def test_nulls_from_device_are_not_answered(self):
        """장비가 null 을 쏟아내도 응답하지 않는다 -> 핑퐁/부하 불가능."""
        self.fx.device.command_null_mode = "spam"
        self.open()
        time.sleep(0.7)
        self.assertEqual(self.fx.device.command_nulls, 0)
        self.assertGreater(self.conn.diagnostics["nulls_received"], 0)

    def test_send_command_reaches_command_port(self):
        self.open()
        self.conn.send_command(struct.pack("<I", 1) + struct.pack("<HHi", 87, 2, 3))
        self.assertTrue(wait_for(lambda: self.fx.device.state[(2, 87)] == 3))

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

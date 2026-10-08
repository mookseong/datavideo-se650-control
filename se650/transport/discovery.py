"""포트 조회 채널 (5009). 공식 문서 2.1.1.

요청: [08 00 00 00][0x55aa0001]
응답: [08 00 00 00][0xaa55xxxx]  xxxx = 사용 가능한 실시간 포트, 0xffff = 빈 채널 없음
      [08 00 00 00][0x55aa000n]  오류
"""
import socket
import struct

from ..i18n import LocalizedError

DISCOVERY_PORT = 5009
REALTIME_PORTS = (5001, 5003, 5005, 5007)

_REQUEST = struct.pack("<II", 8, 0x55AA0001)
_REPLY = struct.Struct("<IHH")       # 길이, 포트, 태그
_OK_TAG = 0xAA55
_NO_FREE_PORT = 0xFFFF


class DiscoveryError(LocalizedError, ConnectionError):
    pass


def parse_discovery_reply(reply: bytes, allowed_ports=REALTIME_PORTS) -> int:
    if len(reply) != _REPLY.size:
        raise DiscoveryError("transport.error.discovery_reply_length", reply=reply.hex())
    size, port, tag = _REPLY.unpack(reply)
    if size != _REPLY.size:
        raise DiscoveryError("transport.error.discovery_reply_length", reply=reply.hex())
    if tag != _OK_TAG:
        raise DiscoveryError("transport.error.discovery_error_reply", reply=reply[4:].hex())
    if port == _NO_FREE_PORT:
        raise DiscoveryError("transport.error.no_free_port")
    if allowed_ports is not None and port not in allowed_ports:
        raise DiscoveryError("transport.error.unknown_port", port=port)
    return port


def request_realtime_port(host: str, port: int = DISCOVERY_PORT, timeout: float = 3.0,
                          allowed_ports=REALTIME_PORTS) -> int:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(_REQUEST)
        reply = _recv_exactly(sock, _REPLY.size)
    return parse_discovery_reply(reply, allowed_ports)


def _recv_exactly(sock, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            break
        data += chunk
    return data

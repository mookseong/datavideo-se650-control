"""전송 계층: TCP 소켓, 길이 프레이밍, 포트 조회, 채널별 답장 규칙.

섹션/컨트롤/명령의 의미는 모른다. 바이트 payload 만 주고받는다.
"""
from .connection import DvipConnection, NotConnectedError
from .discovery import DISCOVERY_PORT, REALTIME_PORTS, DiscoveryError, request_realtime_port
from .framing import NULL_PAYLOAD, FrameDecoder, encode_frame, is_null

__all__ = [
    "DvipConnection", "NotConnectedError",
    "DISCOVERY_PORT", "REALTIME_PORTS", "DiscoveryError", "request_realtime_port",
    "NULL_PAYLOAD", "FrameDecoder", "encode_frame", "is_null",
]

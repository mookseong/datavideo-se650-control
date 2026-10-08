"""DVIP 프레이밍: 모든 패킷은 [전체 길이 4byte LE][payload] 형식.

이 모듈은 길이 prefix 만 다룬다. payload 안의 의미(명령, 섹션 등)는 모른다.
"""
import struct

LENGTH_FIELD = struct.Struct("<I")
MAX_FRAME_SIZE = 1 << 20          # 첫 실시간 패킷은 전체 상태라 크다
MIN_FRAME_SIZE = 8                # 길이 + 명령 dword (null 패킷이 가장 짧다, 3.2.1)

# 세션 유지용 null payload (명령 dword 만 있고 파라미터 없음)
NULL_PAYLOAD = b"\x00\x00\x00\x00"
NULL_PAYLOAD_COMMAND = b"\x01\x00\x00\x00"


def encode_frame(payload: bytes) -> bytes:
    return LENGTH_FIELD.pack(len(payload) + LENGTH_FIELD.size) + payload


def is_null(payload: bytes) -> bool:
    """파라미터가 없는 패킷 (명령 dword 만 있음)."""
    return len(payload) <= 4


class FramingError(ValueError):
    """길이 필드가 깨졌다. 스트림 동기를 잃었으므로 연결을 끊어야 한다.

    조용히 버리면 장비 패킷 하나가 답장 없이 사라지고, 장비는 답장을 기다리며 실시간 채널이 멈춘다 (3.2.5).
    """


class FrameDecoder:
    """TCP 스트림 조각을 받아 완성된 payload 목록을 돌려준다 (소켓과 무관한 순수 로직)."""

    def __init__(self, max_frame_size: int = MAX_FRAME_SIZE):
        self._buffer = b""
        self._max = max_frame_size

    def feed(self, data: bytes) -> list:
        self._buffer += data
        payloads = []
        while len(self._buffer) >= LENGTH_FIELD.size:
            length = LENGTH_FIELD.unpack_from(self._buffer)[0]
            if not MIN_FRAME_SIZE <= length <= self._max:
                self._buffer = b""
                raise FramingError("invalid frame length %d" % length)
            if len(self._buffer) < length:
                break
            payloads.append(self._buffer[LENGTH_FIELD.size:length])
            self._buffer = self._buffer[length:]
        return payloads

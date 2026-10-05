"""장비가 'Ready' 를 알려줄 때까지 다음 명령을 막는 문 (문서 4.1.8.1.9, 4.1.9.2).

장비는 명령을 필드 간격으로 처리하고, 처리하면 명령 값을 Ready 로 되돌린다.
그 변화가 너무 빨라 실시간 갱신에 안 잡힐 수도 있으므로 두 가지를 함께 쓴다:
  - 보낸 직후 min_interval 동안은 무조건 막는다 (최소 몇 필드는 처리 시간을 준다)
  - 그 뒤에는 장비가 알려준 값이 Ready 면 연다. 아니면 timeout 까지 기다린다 (영원히 잠기지 않게)
"""
import time


class ReadyGate:
    def __init__(self, min_interval: float, timeout: float, clock=time.monotonic):
        self._min_interval = min_interval
        self._timeout = timeout
        self._clock = clock
        self._last_sent = None

    def can_send(self, device_ready: bool) -> bool:
        if self._last_sent is None:
            return device_ready
        elapsed = self._clock() - self._last_sent
        if elapsed < self._min_interval:
            return False
        return device_ready or elapsed >= self._timeout

    def mark_sent(self):
        self._last_sent = self._clock()

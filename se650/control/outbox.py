"""실시간 채널 답장에 실어 보낼 값 (문서 1.1, 3.2.4.2).

T-바처럼 연속으로 바뀌는 값은 별도 명령 패킷 대신, 장비가 매 필드 기다리는 답장에 싣는다.
-> 추가 패킷 0개, 전송 속도는 장비의 필드 속도로 자동 제한.

필드마다 컨트롤당 값 하나만 꺼낸다. 아직 안 보낸 값은 최신 값으로 덮어쓰되,
'반드시 전달' 값(예: T-바 100 = 트랜지션 완료)은 덮어쓰지 않고 순서대로 보낸다.
"""
import threading
from collections import OrderedDict, deque


class RealtimeOutbox:
    def __init__(self):
        self._queues = OrderedDict()      # control -> deque[(value, must_deliver)]
        self._lock = threading.Lock()

    def put(self, control, value, must_deliver: bool = False):
        with self._lock:
            queue = self._queues.setdefault(control, deque())
            if queue and not queue[-1][1]:
                queue[-1] = (value, must_deliver)      # 아직 안 보낸 일반 값은 최신 값으로 교체
            else:
                queue.append((value, must_deliver))

    def take(self) -> list:
        """이번 필드 답장에 실을 [(control, value), ...]"""
        with self._lock:
            items = []
            for control, queue in list(self._queues.items()):
                value, _ = queue.popleft()
                items.append((control, value))
                if not queue:
                    del self._queues[control]
            return items

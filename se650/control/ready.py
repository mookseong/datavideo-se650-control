"""보낸 명령이 장비에서 처리됐는지 확인될 때까지 다음 명령을 막는다 (문서 4.1.8.1.9, 4.1.9.2).

왜 단순 대기로는 안 되는가:
  장비는 명령을 필드 간격으로 처리하고 명령 값을 Ready 로 되돌린다. 실시간 채널은 "마지막으로 보낸 값과
  달라진 값"만 보내므로(3.2.3), 한 필드 안에 8 -> 5 -> 8 로 돌아오면 실시간으로는 아무것도 보이지 않는다.
  그렇다고 시간이 지났다고 그냥 보내면 처리 중인 장비에 명령이 겹친다.

그래서:
  1) 보낸 뒤 "처리 중" 이 보였다가 "Ready" 가 보이면 완료
  2) 아니면 정해진 시각에 그 값을 GET 으로 직접 확인한다. 명령 채널은 순서가 보장되므로
     우리 명령 뒤에 보낸 GET 의 응답은 반드시 명령 이후의 상태다 -> 그 응답이 Ready 면 완료
  3) 확인 횟수를 다 써도 완료가 안 되면 포기하고 보내는 대신 계속 막는다 (새로고침으로 다시 확인)
"""
import time


class ReadyTracker:
    def __init__(self, first_check: float, interval: float, max_checks: int, clock=time.monotonic):
        self._first_check = first_check
        self._interval = interval
        self._max_checks = max_checks
        self._clock = clock
        self.pending = False
        self._seen_working = False
        self._next_check = 0.0
        self._checks = 0
        self.sent_get_seq = 0             # 명령을 보낸 시점까지 보낸 GET 수 (이후 GET 응답만 믿는다)

    @property
    def gave_up(self) -> bool:
        return self.pending and self._checks >= self._max_checks

    def mark_sent(self, get_seq: int):
        # pending 을 마지막에: 다른 스레드가 pending=True 를 보는 순간 나머지 값은 이미 이번 명령 기준이어야 한다
        self.sent_get_seq = get_seq
        self._seen_working = False
        self._checks = 0
        self._next_check = self._clock() + self._first_check
        self.pending = True

    def observe(self, working: bool, ready: bool, trusted: bool) -> bool:
        """장비가 알려준 이 명령의 상태. 방금 완료됐으면 True.
        trusted: 우리 명령 이후에 보낸 GET 의 응답이라 명령 이후 상태임이 확실함."""
        if not self.pending:
            return False
        if working:
            self._seen_working = True
            return False
        if ready and (self._seen_working or trusted):
            self.pending = False
            return True
        return False

    def check_due(self) -> bool:
        """지금 확인용 GET 을 보낼 차례인가. True 면 다음 확인 시각으로 넘어간다."""
        if not self.pending or self._checks >= self._max_checks or self._clock() < self._next_check:
            return False
        self._checks += 1
        self._next_check = self._clock() + self._interval
        return True

    def reset(self):
        """새로고침 등으로 전체 상태를 다시 받을 때: 다음 신뢰 가능한 응답으로 다시 판단한다."""
        if self.pending:
            self._checks = 0
            self._next_check = self._clock()

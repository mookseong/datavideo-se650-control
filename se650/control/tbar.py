"""T-바: 레버 위치를 트랜지션 진행률로 바꾼다. 네트워크와 무관한 순수 로직.

실제 패널의 T-바처럼, 끝까지 밀어 트랜지션이 완료되면 그 자리가 다음 트랜지션의 출발점이 된다
(아래 -> 위 로 한 번, 다음엔 위 -> 아래 로 한 번).
"""

TRAVEL = 100.0          # 위치 범위 0(아래) ~ 100(위), 진행률 범위 0 ~ 100


class TBarTracker:
    def __init__(self, origin: float = 0.0):
        self.origin = origin       # 현재 트랜지션이 시작된 끝 (0 또는 100)

    def level_for(self, position: float) -> float:
        """position(0~100) -> 진행률(0~100). 반대쪽 끝에 닿으면 100 을 돌려주고 출발점을 옮긴다."""
        position = min(max(float(position), 0.0), TRAVEL)
        level = abs(position - self.origin)
        if level >= TRAVEL:
            self.origin = position
            return TRAVEL
        return level

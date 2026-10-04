"""Datavideo SE-650 DVIP 제어 패키지.

계층 (의존은 위 -> 아래 방향만):
    app        GUI, CLI, 설정 저장
    control    상태 모델, 스위처 조작
    protocol   명령/섹션/컨트롤 번호, payload 인코딩·디코딩
    transport  소켓, 프레이밍, 포트 조회, 채널 답장 규칙
"""
import logging

from .control import Switcher
from .transport import DvipConnection

logging.getLogger(__name__).addHandler(logging.NullHandler())   # 라이브러리 관례: 출력은 앱이 결정


def open_switcher(host: str, on_change=None, **connection_options) -> Switcher:
    """연결 객체와 Switcher 를 조립한다 (아직 connect() 는 하지 않음)."""
    return Switcher(DvipConnection(host, **connection_options), on_change=on_change)


__all__ = ["DvipConnection", "Switcher", "open_switcher"]

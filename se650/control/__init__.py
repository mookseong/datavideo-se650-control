"""제어 계층: 스위처 상태 모델과 사용자 조작.

"PGM 키 버튼을 누르면 무엇을 보내야 하나", "받은 값으로 화면 상태가 어떻게 바뀌나" 같은 규칙이 여기 있다.
"""
from .state import KeyerState, SwitcherState, TransitionEngine
from .switcher import Connection, Switcher

__all__ = ["Connection", "KeyerState", "Switcher", "SwitcherState", "TransitionEngine"]

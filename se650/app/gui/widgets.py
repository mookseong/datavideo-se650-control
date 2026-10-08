"""번역을 고려한 공용 위젯."""
from tkinter import ttk
from typing import Callable, Dict



class ChoiceBox(ttk.Combobox):
    """표시는 번역된 이름, value 는 언어와 무관한 ID.

    설정 파일과 Switcher 에는 항상 ID 가 들어가므로 언어를 바꿔도 저장값이 깨지지 않는다.
    """

    def __init__(self, parent, options: Dict[str, str], initial: str = None,
                 on_select: Callable[[str], None] = None, width: int = 10):
        self._ids = list(options)
        super().__init__(parent, values=list(options.values()), state="readonly", width=width)
        self.value = initial
        if on_select:
            self.bind("<<ComboboxSelected>>", lambda _event: on_select(self.value))

    @property
    def value(self):
        index = self.current()
        return self._ids[index] if index >= 0 else None

    @value.setter
    def value(self, option_id):
        if option_id in self._ids:
            self.current(self._ids.index(option_id))

    def select(self, option_id):
        """사용자가 고른 것처럼 값을 바꾸고 선택 이벤트를 발생시킨다 (테스트/단축키용)."""
        self.value = option_id
        self.event_generate("<<ComboboxSelected>>")

"""번역을 고려한 공용 위젯."""
import re
from tkinter import ttk
from typing import Callable, Dict, Iterable

from ...i18n import tr


def choice_key(table_name: str, name: str) -> str:
    """프로토콜 값 표의 이름 -> 번역 키.  ('hdmi_out', 'PGM+DSK') -> 'app.choice.hdmi_out.pgm_dsk'"""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return "app.choice.%s.%s" % (table_name, slug)


def translated_options(table_name: str, names: Iterable[str]) -> Dict[str, str]:
    """{값(언어 무관 ID): 표시 이름(현재 언어)}"""
    return {name: tr(choice_key(table_name, name)) for name in names}


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

"""프로토콜 값 이름 -> 화면 표시 이름. tkinter 없이 쓸 수 있다 (CLI, 테스트)."""
import re
from typing import Dict, Iterable

from ..i18n import tr


def choice_key(table_name: str, name: str) -> str:
    """프로토콜 값 표의 이름 -> 번역 키.  ('hdmi_out', 'PGM+DSK') -> 'app.choice.hdmi_out.pgm_dsk'"""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return "app.choice.%s.%s" % (table_name, slug)


def translated_options(table_name: str, names: Iterable[str]) -> Dict[str, str]:
    """{값(언어 무관 ID): 표시 이름(현재 언어)}"""
    return {name: tr(choice_key(table_name, name)) for name in names}

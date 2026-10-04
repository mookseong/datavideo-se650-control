"""다국어 지원 (계층 공통 기반 모듈).

se650 의 다른 모듈을 import 하지 않으므로 어느 계층에서든 사용할 수 있다.
각 계층은 자기 네임스페이스의 키만 쓴다:  transport.* / protocol.* / control.* / app.*

    from se650.i18n import tr
    tr("transport.log.connected", host="10.0.0.1")
"""
from typing import Dict

from .catalog import LanguagePack, load_all
from .translator import DEFAULT_LOCALE, Translator, detect_system_locale, normalize

_translator = Translator(load_all())


def tr(key: str, **params) -> str:
    return _translator.translate(key, **params)


def set_locale(code: str) -> str:
    """언어 변경. 빈 문자열/None 이면 시스템 언어. 실제 적용된 코드를 돌려준다."""
    return _translator.set_locale(code or detect_system_locale())


def get_locale() -> str:
    return _translator.locale


def available_languages() -> Dict[str, str]:
    """{코드: 그 언어로 쓴 이름}  예) {"en": "English", "ko": "한국어"}"""
    return {code: pack.name for code, pack in _translator.available().items()}


class LocalizedError(Exception):
    """메시지를 키로 들고 있다가 str() 할 때 현재 언어로 번역하는 예외.

    코드/테스트는 언어와 무관하게 .key 로 원인을 구분할 수 있다.
    """

    def __init__(self, key: str, **params):
        super().__init__(key)
        self.key = key
        self.params = params

    def __str__(self):
        return tr(self.key, **self.params)


set_locale("")      # 시작 시 시스템 언어

__all__ = ["DEFAULT_LOCALE", "LanguagePack", "LocalizedError", "Translator", "available_languages",
           "detect_system_locale", "get_locale", "normalize", "set_locale", "tr"]

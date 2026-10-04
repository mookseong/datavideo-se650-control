"""키 -> 현재 언어 문구. 없으면 기본 언어(영어), 그것도 없으면 키 자체를 돌려준다."""
import locale
import logging
import os
import threading
from typing import Dict

from .catalog import LanguagePack

log = logging.getLogger(__name__)

DEFAULT_LOCALE = "en"
ENV_VARIABLE = "SE650_LANG"


class Translator:
    def __init__(self, packs: Dict[str, LanguagePack], locale_code: str = DEFAULT_LOCALE,
                 fallback: str = DEFAULT_LOCALE):
        if fallback not in packs:
            raise ValueError("fallback language pack missing: %s" % fallback)
        self._packs = packs
        self._fallback = fallback
        self._lock = threading.Lock()
        self._locale = fallback
        self.set_locale(locale_code)

    @property
    def locale(self) -> str:
        return self._locale

    def available(self) -> Dict[str, LanguagePack]:
        return dict(self._packs)

    def set_locale(self, code: str) -> str:
        """지원하지 않는 언어면 기본 언어로. 실제로 적용된 코드를 돌려준다."""
        resolved = normalize(code) if normalize(code) in self._packs else self._fallback
        with self._lock:
            self._locale = resolved
        return resolved

    def translate(self, key: str, **params) -> str:
        template = (self._packs[self._locale].messages.get(key)
                    or self._packs[self._fallback].messages.get(key))
        if template is None:
            log.debug("missing translation key: %s", key)
            return key
        try:
            return template.format(**params)
        except (KeyError, IndexError, ValueError):
            log.debug("bad placeholders for %s in %s", key, self._locale)
            return template          # 번역 실수로 프로그램이 죽지 않도록 원문 그대로


def normalize(code: str) -> str:
    """'ko_KR.UTF-8', 'Korean_Korea', 'EN-us' -> 'ko', 'ko', 'en'"""
    text = (code or "").strip().lower()
    if text.startswith("korean"):
        return "ko"
    if text.startswith("english"):
        return "en"
    return text.replace("-", "_").split("_")[0].split(".")[0]


def detect_system_locale() -> str:
    """환경변수 SE650_LANG > OS 언어 설정 > 기본값 순서."""
    candidates = [os.environ.get(ENV_VARIABLE)]
    try:
        candidates.append(locale.getlocale()[0])
    except ValueError:
        pass
    candidates += [os.environ.get(name) for name in ("LC_ALL", "LC_MESSAGES", "LANG")]
    for candidate in candidates:
        if candidate and normalize(candidate) not in ("c", "posix", ""):
            return normalize(candidate)
    return DEFAULT_LOCALE

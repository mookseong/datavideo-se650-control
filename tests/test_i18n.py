"""다국어: 번역 엔진 동작 + 언어팩 자체의 검증.

언어팩 검증은 새 언어를 추가했을 때 빠진 키/잘못된 자리표시자를 바로 잡아낸다.
"""
import json
import os
import pathlib
import string
import tempfile
import unittest
from unittest import mock

from se650 import i18n
from se650.app.gui.widgets import choice_key
from se650.i18n.catalog import LOCALES_DIR, flatten, load_all, load_pack
from se650.i18n.translator import Translator, detect_system_locale, normalize
from se650.protocol.constants import (AUDIO_MODES, AUDIO_SOURCES, HDMI_OUTPUTS, KEY_SOURCES, SOURCES,
                                      TRANSITION_TYPES)

LAYER_FILES = {"transport", "protocol", "control", "app"}

# 방송 장비에서 영어가 표준인 용어: 어느 언어팩이든 영어 원문과 같아야 한다 (README "번역하지 않는 용어")
TERMINOLOGY_PREFIXES = (
    "app.panel.",                        # 실제 패널에 인쇄된 표기: PGM, PVW, MIX, TRANS BG, USER, LOGO, CUT, AUTO ...
    "app.choice.source.",                # Black, In 1~4, Matte, Still 1/2
    "app.choice.transition_type.",       # Mix, Wipe
    "app.choice.hdmi_out.",              # PGM, PVW, PGM+DSK, PVW+DSK, MultiView
    "app.choice.audio_source.in_",       # In 1~4
)
PACKS = load_all()
REFERENCE = PACKS["en"]


def placeholders(text: str) -> set:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


class LanguagePackTests(unittest.TestCase):
    """모든 언어팩이 기준(영어)과 같은 모양인지."""

    def test_english_and_korean_available(self):
        self.assertEqual(i18n.available_languages()["en"], "English")
        self.assertEqual(i18n.available_languages()["ko"], "한국어")

    def test_files_split_by_layer(self):
        for directory in LOCALES_DIR.iterdir():
            files = {p.stem for p in directory.glob("*.json")} - {"_meta"}
            self.assertEqual(files, LAYER_FILES, directory.name)

    def test_same_keys_as_reference(self):
        for code, pack in PACKS.items():
            missing = set(REFERENCE.messages) - set(pack.messages)
            extra = set(pack.messages) - set(REFERENCE.messages)
            self.assertEqual((missing, extra), (set(), set()), code)

    def test_same_placeholders_as_reference(self):
        for code, pack in PACKS.items():
            for key, text in pack.messages.items():
                self.assertEqual(placeholders(text), placeholders(REFERENCE.messages[key]), "%s %s" % (code, key))

    def test_no_empty_messages(self):
        for code, pack in PACKS.items():
            empty = [key for key, text in pack.messages.items() if not text.strip()]
            self.assertEqual(empty, [], code)

    def test_choice_labels_exist_for_every_protocol_value(self):
        tables = {"source": {**SOURCES, **KEY_SOURCES}, "transition_type": TRANSITION_TYPES,
                  "hdmi_out": HDMI_OUTPUTS, "audio_source": AUDIO_SOURCES, "audio_mode": AUDIO_MODES}
        for code, pack in PACKS.items():
            for table, names in tables.items():
                for name in names:
                    self.assertIn(choice_key(table, name), pack.messages, "%s %s.%s" % (code, table, name))

    def test_terminology_kept_in_english(self):
        terminology = [key for key in REFERENCE.messages if key.startswith(TERMINOLOGY_PREFIXES)]
        self.assertGreater(len(terminology), 25)
        for code, pack in PACKS.items():
            changed = {key: pack.messages[key] for key in terminology if pack.messages[key] != REFERENCE.messages[key]}
            self.assertEqual(changed, {}, code)

    def test_korean_actually_translated(self):
        korean = PACKS["ko"].messages
        self.assertEqual(korean["app.connection.connect"], "연결")
        self.assertRegex(korean["transport.error.no_free_port"], "[가-힣]")


class CatalogTests(unittest.TestCase):
    def test_flatten_nested(self):
        self.assertEqual(flatten({"a": {"b": "x"}, "c": "y"}, "app"), {"app.a.b": "x", "app.c": "y"})

    def test_file_name_is_namespace(self):
        with tempfile.TemporaryDirectory() as root:
            pack_dir = pathlib.Path(root) / "xx"
            pack_dir.mkdir()
            (pack_dir / "_meta.json").write_text(json.dumps({"name": "Test"}), encoding="utf-8")
            (pack_dir / "control.json").write_text(json.dumps({"log": {"hi": "hello"}}), encoding="utf-8")
            pack = load_pack(pack_dir)
        self.assertEqual((pack.code, pack.name, pack.messages), ("xx", "Test", {"control.log.hi": "hello"}))


class TranslatorTests(unittest.TestCase):
    def setUp(self):
        en = REFERENCE.__class__("en", "English", "English", {"a.greet": "Hello {name}", "a.only_en": "only en"})
        ko = REFERENCE.__class__("ko", "한국어", "Korean", {"a.greet": "안녕 {name}", "a.broken": "{oops"})
        self.tr = Translator({"en": en, "ko": ko}, "ko")

    def test_translate_with_params(self):
        self.assertEqual(self.tr.translate("a.greet", name="민수"), "안녕 민수")

    def test_falls_back_to_english(self):
        self.assertEqual(self.tr.translate("a.only_en"), "only en")

    def test_unknown_key_returns_key(self):
        self.assertEqual(self.tr.translate("a.nothing"), "a.nothing")

    def test_bad_template_does_not_crash(self):
        self.assertEqual(self.tr.translate("a.broken"), "{oops")

    def test_missing_param_does_not_crash(self):
        self.assertEqual(self.tr.translate("a.greet"), "안녕 {name}")

    def test_unsupported_locale_uses_fallback(self):
        self.assertEqual(self.tr.set_locale("ja"), "en")
        self.assertEqual(self.tr.translate("a.greet", name="x"), "Hello x")


class LocaleDetectionTests(unittest.TestCase):
    def test_normalize(self):
        for raw, expected in (("ko_KR.UTF-8", "ko"), ("Korean_Korea", "ko"), ("EN-us", "en"),
                              ("English_United States", "en"), ("ko", "ko"), ("", "")):
            self.assertEqual(normalize(raw), expected, raw)

    def test_environment_variable_wins(self):
        with mock.patch.dict(os.environ, {"SE650_LANG": "ko"}):
            self.assertEqual(detect_system_locale(), "ko")

    def test_c_locale_ignored(self):
        with mock.patch.dict(os.environ, {"LC_ALL": "C", "LANG": "ko_KR.UTF-8"}, clear=True), \
                mock.patch("locale.getlocale", return_value=(None, None)):
            self.assertEqual(detect_system_locale(), "ko")


class GlobalApiTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_locale("en")

    def test_switch_language(self):
        i18n.set_locale("ko")
        self.assertEqual(i18n.tr("app.connection.connect"), "연결")
        i18n.set_locale("en")
        self.assertEqual(i18n.tr("app.connection.connect"), "Connect")

    def test_localized_error_translates_when_shown(self):
        error = i18n.LocalizedError("transport.log.connected", host="1.2.3.4")
        i18n.set_locale("ko")
        self.assertEqual(str(error), "연결됨: 1.2.3.4")
        i18n.set_locale("en")
        self.assertEqual(str(error), "Connected: 1.2.3.4")
        self.assertEqual(error.key, "transport.log.connected")

    def test_library_errors_are_localized(self):
        from se650.transport import DiscoveryError
        from se650.transport.discovery import parse_discovery_reply
        i18n.set_locale("ko")
        with self.assertRaises(DiscoveryError) as ctx:
            parse_discovery_reply(b"\x08\x00\x00\x00\xff\xff\x55\xaa")
        self.assertRegex(str(ctx.exception), "빈 연결 채널")


if __name__ == "__main__":
    unittest.main()

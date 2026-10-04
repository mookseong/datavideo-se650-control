"""언어팩 로더.

언어팩 = locales/<언어코드>/ 폴더
    _meta.json        {"name": "한국어", "english_name": "Korean"}
    transport.json    전송 계층 문구   -> 키 "transport.<...>"
    protocol.json     명령 계층 문구   -> 키 "protocol.<...>"
    control.json      제어 계층 문구   -> 키 "control.<...>"
    app.json          앱 문구          -> 키 "app.<...>"

파일 이름이 키의 첫 부분(네임스페이스)이 되고, JSON 안의 중첩은 점으로 이어진다.
    app.json {"status": {"connected": "..."}}  ->  "app.status.connected"
"""
import json
import pathlib
from dataclasses import dataclass
from typing import Dict

LOCALES_DIR = pathlib.Path(__file__).resolve().parent / "locales"
META_FILE = "_meta.json"


@dataclass(frozen=True)
class LanguagePack:
    code: str
    name: str              # 그 언어로 쓴 이름 (예: 한국어)
    english_name: str
    messages: Dict[str, str]


def load_pack(directory: pathlib.Path) -> LanguagePack:
    meta = _read_json(directory / META_FILE)
    messages = {}
    for path in sorted(directory.glob("*.json")):
        if path.name == META_FILE:
            continue
        messages.update(flatten(_read_json(path), prefix=path.stem))
    return LanguagePack(code=directory.name, name=meta.get("name", directory.name),
                        english_name=meta.get("english_name", directory.name), messages=messages)


def load_all(root: pathlib.Path = LOCALES_DIR) -> Dict[str, LanguagePack]:
    return {d.name: load_pack(d) for d in sorted(root.iterdir()) if (d / META_FILE).is_file()}


def flatten(tree: dict, prefix: str) -> Dict[str, str]:
    flat = {}
    for key, value in tree.items():
        full_key = "%s.%s" % (prefix, key)
        if isinstance(value, dict):
            flat.update(flatten(value, full_key))
        else:
            flat[full_key] = str(value)
    return flat


def _read_json(path: pathlib.Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

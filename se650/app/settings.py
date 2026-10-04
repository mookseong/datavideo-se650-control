"""사용자 설정을 JSON 파일에 자동 저장/복원."""
import json
import os

DEFAULTS = {
    "ip": "192.168.100.101",       # 공식 문서 1.2 기본 IP
    "auto_connect": False,
    "transition_type": "Mix",
    "duration": 30,
    "hdmi_out": "MultiView",
    "audio_source": "Follow",
    "audio_mode": "Digital",
    "user_memory": 1,
    "geometry": "",
    "language": "",                # "" = 시스템 언어, 아니면 "en" / "ko" ...
}
FILE_NAME = "se650_settings.json"


def default_path() -> str:
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(project_root, FILE_NAME)


class Settings:
    """값이 바뀔 때마다 파일에 저장한다. 파일이 없거나 깨져 있으면 기본값을 쓴다."""

    def __init__(self, path: str = None):
        self.path = path or default_path()
        self._values = dict(DEFAULTS)
        self._load()

    def __getitem__(self, key):
        return self._values[key]

    def set(self, key, value):
        if self._values.get(key) == value:
            return
        self._values[key] = value
        self.save()

    def save(self) -> bool:
        try:
            self._write(self.path)
            return True
        except OSError:
            fallback = os.path.join(os.path.expanduser("~"), FILE_NAME)
            if self.path == fallback:
                return False
            self.path = fallback               # 프로그램 폴더에 쓸 수 없으면 홈 폴더로
            return self.save()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                stored = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(stored, dict):
            return
        for key, default in DEFAULTS.items():
            if isinstance(stored.get(key), type(default)):
                self._values[key] = stored[key]

    def _write(self, path):
        temp = path + ".tmp"
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(self._values, f, ensure_ascii=False, indent=2)
        os.replace(temp, path)

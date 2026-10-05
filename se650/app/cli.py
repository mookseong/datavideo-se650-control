"""명령줄 인터페이스:  python se650_control.py <IP> <명령> [인수]"""
import time

from .. import open_switcher
from ..i18n import tr
from ..protocol.constants import SOURCE_NAMES

READY_TIMEOUT = 2.0
SETTLE_TIME = 0.3


def _commands():
    """명령어 -> (필요 인수 개수, 실행 함수(switcher, args))"""
    return {
        "pgm": (1, lambda sw, a: sw.set_program(a[0])),
        "pvw": (1, lambda sw, a: sw.set_preview(a[0])),
        "auto": (0, lambda sw, a: sw.auto()),
        "cut": (0, lambda sw, a: sw.cut()),
        "dskauto": (0, lambda sw, a: sw.dsk_auto()),
        "dskcut": (0, lambda sw, a: sw.dsk_cut()),
        "ftb": (0, lambda sw, a: sw.fade_to_black()),
        "type": (1, lambda sw, a: sw.set_transition_type(a[0])),
        "dur": (1, lambda sw, a: sw.set_duration(a[0], a[1] if len(a) > 1 else "me")),
        "key1": (1, lambda sw, a: sw.press_keyer("key1", a[0].lower())),
        "pip": (1, lambda sw, a: sw.press_keyer("pip", a[0].lower())),
        "dsk1": (1, lambda sw, a: sw.press_keyer("dsk1", a[0].lower())),
        "hdmi": (1, lambda sw, a: sw.set_hdmi_output(a[0])),
        "audio": (1, lambda sw, a: sw.set_audio_source(" ".join(a))),
        "amode": (1, lambda sw, a: sw.set_audio_mode(a[0])),
        "load": (1, lambda sw, a: sw.load_memory(a[0])),
        "save": (1, lambda sw, a: sw.store_memory(a[0])),
        "menu": (1, _press_menu),
        "status": (0, lambda sw, a: None),
    }


def _press_menu(switcher, args):
    """CLI 에서 menu 를 직접 입력한 것은 명시적 허용으로 본다 (문서에 없는 명령임을 경고)."""
    print(tr("app.cli.undocumented_warning"))
    switcher.allow_undocumented = True
    switcher.press_menu(args[0])


def describe(switcher) -> str:
    state = switcher.state
    keyers = ", ".join("%s(PGM=%d PVW=%d)" % (name, k.on, k.preview) for name, k in state.keyers.items())
    return tr("app.cli.status", pgm=SOURCE_NAMES.get(state.pgm, state.pgm),
              pvw=SOURCE_NAMES.get(state.pvw, state.pvw), keyers=keyers, names=switcher.input_names)


def run_cli(argv, switcher_factory=open_switcher, out=print) -> int:
    commands = _commands()
    if len(argv) < 2 or argv[1].lower() not in commands:
        out(tr("app.cli.usage"))
        return 1
    host, name, args = argv[0], argv[1].lower(), argv[2:]
    required, action = commands[name]
    if len(args) < required:
        out(tr("app.cli.usage"))
        return 1

    switcher = switcher_factory(host)
    switcher.connect()
    try:
        if not switcher.wait_ready(READY_TIMEOUT):
            out(tr("app.cli.no_state", seconds=int(READY_TIMEOUT)))
        action(switcher, args)
        time.sleep(SETTLE_TIME)
        out(describe(switcher))
    finally:
        switcher.close()
    return 0

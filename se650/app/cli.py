"""명령줄 인터페이스:  python se650_control.py <IP> <명령> [인수]"""
import time

from .. import open_switcher
from ..i18n import tr
from ..protocol.constants import SOURCE_NAMES

READY_TIMEOUT = 2.0
SETTLE_TIME = 0.3


ALLOW_UNDOCUMENTED_FLAG = "--allow-undocumented"
CONFIRM_BOOT_SLOT_FLAG = "--confirm-boot-slot"
# 현재 상태를 보고 반대로 바꾸는 명령: 상태를 못 받았으면 엉뚱하게 동작하므로 실행하지 않는다
# + 처리 확인이 필요한 명령(트랜지션, 메모리): 상태 없이는 장비가 Ready 인지 알 수 없다 (4.1.8.1.9, 4.1.9.2)
STATE_DEPENDENT = {"auto", "dskauto", "ftb", "key1", "pip", "dsk1", "cut", "dskcut", "load", "save"}


def _commands(flags):
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
        "save": (1, lambda sw, a: sw.store_memory(a[0], confirm_boot_slot=CONFIRM_BOOT_SLOT_FLAG in flags)),
        "menu": (1, lambda sw, a: _press_menu(sw, a, ALLOW_UNDOCUMENTED_FLAG in flags)),
        "status": (0, lambda sw, a: None),
    }


def _press_menu(switcher, args, allowed):
    """문서에 없는 명령: --allow-undocumented 를 직접 붙였을 때만."""
    switcher.allow_undocumented = allowed
    switcher.press_menu(args[0])


def describe(switcher) -> str:
    state = switcher.state
    keyers = ", ".join("%s(PGM=%d PVW=%d)" % (name, k.on, k.preview) for name, k in state.keyers.items())
    return tr("app.cli.status", pgm=SOURCE_NAMES.get(state.pgm, state.pgm),
              pvw=SOURCE_NAMES.get(state.pvw, state.pvw), keyers=keyers, names=switcher.input_names)


def run_cli(argv, switcher_factory=open_switcher, out=print) -> int:
    flags = {a for a in argv if a.startswith("--")}
    argv = [a for a in argv if not a.startswith("--")]
    commands = _commands(flags)
    if len(argv) < 2 or argv[1].lower() not in commands:
        out(tr("app.cli.usage"))
        return 1
    host, name, args = argv[0], argv[1].lower(), argv[2:]
    required, action = commands[name]
    if len(args) < required:
        out(tr("app.cli.usage"))
        return 1

    switcher = switcher_factory(host)
    try:
        try:
            switcher.connect()
        except Exception as error:      # noqa: BLE001 - 연결 실패는 메시지로 (finally 에서 반드시 닫는다)
            out(tr("app.cli.error", error=error))
            return 2
        if not switcher.wait_ready(READY_TIMEOUT):
            out(tr("app.cli.no_state", seconds=int(READY_TIMEOUT)))
            if name in STATE_DEPENDENT:
                out(tr("app.cli.needs_state"))
                return 3
        try:
            action(switcher, args)
        except Exception as error:      # noqa: BLE001 - 거부된 명령(범위, 처리 중 등)은 메시지로
            out(tr("app.cli.error", error=error))
            return 4
        time.sleep(SETTLE_TIME)
        out(describe(switcher))
    finally:
        switcher.close()
    return 0

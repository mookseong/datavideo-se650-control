"""진입점: 인수가 있으면 CLI, 없으면 GUI.  공통 옵션: --lang <코드>"""
import logging
import sys


def split_language_option(argv):
    """['--lang', 'ko', '1.2.3.4', 'cut'] -> ('ko', ['1.2.3.4', 'cut'])"""
    args = list(argv)
    language = None
    for flag in ("--lang", "-l"):
        if flag in args:
            index = args.index(flag)
            if index + 1 < len(args):
                language = args[index + 1]
                del args[index:index + 2]
    return language, args


def main(argv=None) -> int:
    language, argv = split_language_option(sys.argv[1:] if argv is None else argv)
    if argv:
        from ..i18n import set_locale
        from .cli import run_cli
        set_locale(language or "")
        logging.basicConfig(level=logging.INFO, format="[log] %(message)s")
        return run_cli(argv)
    from .gui import run_gui
    run_gui(language)
    return 0

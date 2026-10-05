"""진단용 로그 파일. 문제가 생겼을 때 무슨 일이 있었는지 나중에 볼 수 있도록 남긴다."""
import logging
import logging.handlers
import os

FILE_NAME = "se650.log"
MAX_BYTES = 1_000_000
BACKUP_COUNT = 3          # se650.log, se650.log.1 ~ .3  (최대 약 4MB)


def enable_file_logging(directory: str) -> str:
    """'se650' 로거 출력을 directory/se650.log 에도 남긴다. 실제 파일 경로를 돌려준다."""
    for candidate in (directory, os.path.expanduser("~")):
        path = os.path.join(candidate, FILE_NAME)
        try:
            handler = logging.handlers.RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT,
                                                           encoding="utf-8")
        except OSError:
            continue
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger = logging.getLogger("se650")
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        return path
    return ""

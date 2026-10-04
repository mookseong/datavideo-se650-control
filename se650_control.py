#!/usr/bin/env python3
"""Datavideo SE-650 DVIP 제어 프로그램 실행 파일.

    python se650_control.py                      GUI
    python se650_control.py <IP> <명령> [인수]     CLI  (python se650_control.py 192.168.100.101 pgm 2)

코드는 se650 패키지에 계층별로 나뉘어 있다 (README.md 참고).
"""
import sys

from se650.app.main import main

if __name__ == "__main__":
    sys.exit(main())

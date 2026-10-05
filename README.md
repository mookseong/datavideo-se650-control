# Datavideo SE-650 DVIP 제어 프로그램

Datavideo SE-650 비디오 스위처를 이더넷(DVIP 프로토콜)으로 제어하는 GUI / CLI 프로그램입니다.
PGM/PVW 전환, AUTO/CUT/FTB, 키어(KEY1 · P-in-P · DSK1), HDMI 출력, 오디오, 유저 메모리, 메뉴 조작을 지원합니다.

- Python 3.8+ 표준 라이브러리만 사용 (tkinter, socket) — 별도 설치 없음
- 한국어 / English 언어팩
- 장비 없이 개발할 수 있는 시뮬레이터 포함

## 실행

```
python se650_control.py                         # GUI
python se650_control.py 192.168.100.101 pgm 2   # CLI (잘못 입력하면 전체 명령 도움말)
python se650_control.py --lang en               # 언어 지정 (GUI/CLI 공통, 생략하면 시스템 언어)
python -m se650.simulator                       # 장비 없이 테스트용 가짜 SE-650 (GUI 에서 127.0.0.1)
```

pip 로 설치하면 `se650` 명령이 생깁니다: `pip install .` 후 `se650` (GUI) / `se650 <IP> cut` (CLI)

PC 와 스위처는 같은 네트워크 대역이어야 합니다. SE-650 기본 IP 는 `192.168.100.101` 입니다.

## 테스트

```
python -m unittest discover -s tests -t .           # Windows / macOS
xvfb-run -a python -m unittest discover -s tests -t .   # 디스플레이 없는 리눅스 (GUI 테스트용)
```

push / PR 마다 GitHub Actions 가 Python 3.10 · 3.12 · 3.13 에서 전체 테스트를 실행합니다 (`.github/workflows/tests.yml`).

## 프로토콜 문서

기준 문서는 Datavideo 공식 **"SE-650 Ethernet Control Protocol E1 (2019-07-09)"** 입니다.
Datavideo 저작물이라 이 레포에는 포함하지 않았습니다. Datavideo 지원팀이나 커뮤니티 아카이브
([RoseOO/datavideo-dvip-docs](https://github.com/RoseOO/datavideo-dvip-docs) 의 `SE650/` 폴더)에서 받을 수 있습니다.
코드 주석의 `(4.1.3.4)` 같은 번호는 이 문서의 절 번호입니다.

## 구조

```
se650/
  transport/   전송 계층   소켓, 길이 프레이밍, 포트 조회(5009), 채널별 답장 규칙
  protocol/    명령 계층   명령·섹션·컨트롤 번호, payload 인코딩/디코딩
  control/     제어 계층   스위처 상태 모델, 사용자 조작 -> 명령, 초기 상태 동기화
  app/         앱          GUI(패널별 클래스), CLI, 설정 저장
  i18n/        다국어      번역 엔진 + 언어팩 (모든 계층이 쓰는 기반 모듈)
  simulator.py             가짜 장비 (테스트/오프라인 확인용)
tests/                     계층별 테스트 + 의존 규칙 검사
```

의존은 위에서 아래로만 흐릅니다. `tests/test_architecture.py` 가 이를 강제합니다.

```
app ──> control ──> protocol
                └─> transport        (protocol 과 transport 는 서로 모름)
모든 계층 ──> i18n                    (i18n 은 어떤 계층도 모름)
```

| 계층 | 아는 것 | 모르는 것 |
|---|---|---|
| transport | 바이트, 소켓, "실시간 채널은 받은 패킷마다 null 로 답한다" | 섹션/컨트롤의 의미 |
| protocol | "PGM_SRC = 섹션 2 / 컨트롤 86", flag 는 하위 1바이트 | 소켓, 스레드, 화면 |
| control | "PGM 키 버튼을 누르면 ON 과 TRANS 를 함께 보낸다", PVW 표시 = ON XOR TRANS | tkinter |
| app | 버튼 배치, 색, 설정 파일 | 패킷 바이트 |

### 주요 파일

- `transport/framing.py` — `FrameDecoder`: TCP 조각 -> 완성된 payload (소켓 없는 순수 로직)
- `transport/channels.py` — `RealtimeChannel`, `CommandChannel`: 답장 규칙의 차이만 구현
- `transport/connection.py` — `DvipConnection`: 포트 조회 + 두 채널 수명, 끊김 1회 통지
- `protocol/constants.py` — 문서의 번호표. 값 표(`SOURCES`, `HDMI_OUTPUTS` …)
- `protocol/codec.py` — `encode_set/get`, `decode_realtime`, `decode_command_reply`
- `control/state.py` — `SwitcherState`, `KeyerState`(키어 규칙), 컨트롤 -> 필드 바인딩 표
- `control/switcher.py` — `Switcher`: 조작 메서드, 수신 반영, 입력 이름, 초기 상태 동기화
- `app/gui/device_panel.py` — 실제 SE-650 패널 배열 (`MenuPad`, `WipeGroup`, `TransitionGroup`, `UserMemoryGroup`,
  `PipKeyerGroup`, `LogoButton`, `SourceRows`, `TBar`, `CutAuto`)
- `app/gui/panels.py` — `ConnectionBar`, `ExtrasPanel`(실제 패널에 없는 기능)
- `control/tbar.py` — T-바 위치 -> 트랜지션 진행률 (끝까지 밀면 다음엔 반대 방향)
- `app/gui/window.py` — `MainWindow`: 패널 조립, 스레드 -> GUI 이벤트 큐, 연결 수명

## 기능 추가 예시

새 컨트롤(예: 와이프 패턴 번호, 문서 4.1.3.1.1)을 추가할 때:

1. `protocol/constants.py` 의 `Controls` 에 `WIPE_PATTERN = ControlId(Section.SWITCHER, 0)` 추가
2. 장비 값을 화면에 보여야 하면 `control/state.py` 의 `SwitcherState` 에 필드를,
   `_BINDINGS` 에 `Controls.WIPE_PATTERN: (("wipe_pattern",), int)` 를 추가
   (조회 목록 `STATE_CONTROLS` 는 바인딩에서 자동 생성)
3. `control/switcher.py` 에 `set_wipe_pattern(n)` 메서드 추가
4. 필요하면 `app/gui/panels.py` 에 위젯 추가
5. 테스트: `test_protocol.py`(바이트), `test_state.py`(반영), `test_switcher.py`(조작)

## GUI: 실제 패널 배열

GUI 는 실제 SE-650 패널과 같은 배열입니다. 패널 버튼 동작은 Datavideo 사용자 매뉴얼(SE-650 E7) 기준입니다.

| 패널 버튼 | 동작 | 프로토콜 |
|---|---|---|
| PROGRAM / PREVIEW 1~4 | 소스 선택 | PGM_SRC / PST_SRC = 1~4 |
| BKG | 매트 배경 | 소스 17 (Matte) |
| STILL | 누를 때마다 Still 1 ↔ Still 2 (2 일 때 `STILL 2` 표시) | 소스 19 / 20 |
| CUT / AUTO | 컷 / 자동 트랜지션 (진행 중 AUTO 점등) | ME_TRANS_COMMAND 5 / 1 |
| T-바 | 수동 트랜지션. 끝까지 밀면 완료, 다음엔 반대 방향 | WIPE_LEVEL (float 0~100) |
| WIPE 프리셋 2개 | 와이프 패턴 1 / 2 | WIPE_PATTERN_NUM |
| REV | 와이프 방향 반전 | TRANS_REVERSE |
| TRANS BG | 배경 트랜지션 포함 | TRANS_BGND |
| MIX / WIPE | 트랜지션 타입 (둘 다 점등 = Clip, 장비에서 설정한 경우 표시만) | TRANS_TYPE 0 / 1 (/ 2) |
| PIP · KEYER PGM / PVW | 키어 (PVW 표시 = ON XOR 다음 트랜지션 포함) | KEY2 / KEY1 ON, TRANS_KEY* |
| LOGO ON/OFF | 로고를 PGM·PVW 에 켜고 끔 | DSK1_KEYER_ON (+ DSK1_TRANS 0) |
| USER 1~3 | 유저 메모리 불러오기 | MEMORY_SELECT + LOAD |
| MENU / ▲▼◀▶ / ENTER | 장비 메뉴 조작 (문서에 없는 명령이라 기본 꺼짐, "추가 기능"에서 허용) | 명령 0x15 |
| RESET | 네트워크 미지원 → 누르면 안내 메시지 | (원격 키 코드 없음) |

장비가 알려주는 입력 이름은 PREVIEW 줄 아래 노란 스티커로 표시됩니다.
FTB, 트랜지션 프레임, 키어 소스, HDMI 출력, 오디오, 메모리 0~999 저장처럼 **실제 패널에 없는 기능**은
아래 "추가 기능" 영역에 따로 모았습니다.

**실제 장비 확인이 필요한 부분** (문서끼리 다르거나 문서에 없는 부분):
- 소스 20: 프로토콜 문서는 "Freeze", 사용자 매뉴얼·키어 소스 표는 "Still 2". 매뉴얼을 따랐습니다.
- T-바: 문서상 WIPE_LEVEL 이 100 이 되면 장비가 완료 처리. 값은 실시간 답장에 실어 보냅니다(문서 3.2.4.2).
  레퍼런스 구현은 이 방식을 쓰지 않으므로 실제 장비에서 T-바가 반응하는지 확인이 필요합니다.
- LOGO ON/OFF: 매뉴얼의 "PGM 과 PVW 에 함께 표시"를 DSK1 ON + 다음 트랜지션 미포함으로 구현했습니다.

## 동작 메모

- 접속 직후 상태는 실시간 채널의 첫 패킷(전체 상태, 문서 3.2.3)으로 받습니다.
  0.6초 안에 오지 않을 때만 명령 채널 GET 으로 보완합니다 (늦은 GET 응답이 최신 값을 덮어쓰지 않도록).
- 키어: 장비 값은 `ON`(PGM 에 올라감)과 `TRANS`(다음 트랜지션에 포함) 두 개입니다.
  패널의 PVW 줄 키 버튼은 `ON XOR TRANS` 입니다. PGM 버튼은 PVW 표시를 유지하도록 두 값을 함께 보냅니다.
- 로그 창의 `[키어 송신/수신] ... ON= TRANS=` 줄은 키어 표시 문제 진단용입니다.
- 설정(`se650_settings.json`)은 IP·트랜지션·출력·오디오·유저 메모리·창 위치를 바꿀 때마다 저장됩니다.

## 프로토콜 문서 준수 (장비 부하 방지)

공식 문서의 규칙을 아래처럼 지킵니다. `se650/simulator.py` 는 이 규칙을 그대로 따르는 가짜 장비로,
클라이언트가 규칙을 어기면 위반으로 기록합니다. 통합·GUI·CLI 테스트는 모두 **위반 0건**을 확인하고 끝나며,
모든 버튼을 연타하는 스트레스 테스트도 포함합니다.

| 문서 규칙 | 구현 | 위치 |
|---|---|---|
| 실시간: 장비가 먼저 보내고, 컨트롤러는 받은 패킷마다 정확히 한 번 답장 (3.2.4, 3.2.5) | 먼저 보내지 않음, 패킷당 답장 1개 | `transport/channels.py` |
| 명령 채널: 명령-응답, null 규칙 없음 (2.2) | null 을 보내지도 되돌리지도 않음 | `transport/channels.py` |
| 명령 목록 0~20 (2.2) | 문서에 없는 메뉴 명령(21)은 "추가 기능"에서 직접 켜야만 전송 | `control/switcher.py` |
| 트랜지션 명령은 엔진이 Ready(8) 일 때만 (4.1.8.1.9) | 연타 시 처리 중이면 보내지 않고 안내 | `control/gates.py` |
| 메모리 명령 전 Ready 확인 (4.1.9.2) | Busy 면 보내지 않고 안내, 결과(성공/없음/실패) 로그 | `control/gates.py` |
| 접속 전 상태 사본은 0 (3.2.3) | 모든 상태 0 으로 시작 (값이 0 인 항목도 정확) | `control/state.py` |
| T-바 같은 연속 값은 실시간 답장에 (1.1, 3.2.4.2) | 추가 패킷 0개, 필드당 최대 1회, 완료(100)는 반드시 전달 | `control/outbox.py` |
| 아날로그 오디오는 입력 1~4 만 (4.1.7.1) | 잘못된 조합은 보내지 않고 안내 | `control/switcher.py` |

그 밖에: 입력 이름 요청은 응답을 받은 뒤 다음 것을 보내고, 새로고침은 1초에 한 번, 해제 직후 재연결은 1.5초 대기,
자동 재연결 루프는 없습니다. 측정치: 대기 중 명령 패킷 0개/초, T-바 드래그 중에도 추가 명령 패킷 0개.

## 문제 해결

모든 로그는 `se650.log` (설정 파일과 같은 폴더, 쓸 수 없으면 홈 폴더)에도 남습니다. 최대 약 4MB로 순환합니다.

- `실시간 포트 5003 / 명령 포트 5004` — 연결할 때마다 찍힙니다. 연결/해제를 반복할 때 번호가
  5003 → 5005 → 5007 로 올라가다 "빈 연결 채널이 없습니다" 가 나오면, 스위처가 이전 채널을 반납하지 않고 있는 것입니다.
- `연결 종료 (명령 채널에서 받은 예상 밖 null 패킷: N)` — N 이 0 이 아니면 스위처가 문서에 없는 null 을 보내는 기기입니다
  (응답하지 않으므로 부하는 없음). 장비 동작을 파악하는 단서로 보내주세요.

PC 가 쓸 수 있는 연결 채널은 3개뿐입니다 (4개 중 1개는 컨트롤 패널).

## 언어팩 (다국어)

현재 English(`en`, 기준 언어) / 한국어(`ko`). GUI 오른쪽 위 "언어"에서 바로 바꿀 수 있고(연결 유지),
선택은 설정에 저장됩니다. 처음 실행 시에는 `SE650_LANG` 환경변수 > OS 언어 > 영어 순으로 고릅니다.

```
se650/i18n/
  catalog.py      언어팩 로더 (파일 이름 = 키 네임스페이스)
  translator.py   키 -> 문구, 영어로 대체, 시스템 언어 감지
  __init__.py     tr(), set_locale(), LocalizedError
  locales/
    en/  _meta.json  transport.json  protocol.json  control.json  app.json
    ko/  (같은 구성)
```

**계층별 분리**: 언어팩 안에서도 파일이 계층별로 나뉘고, 각 계층 코드는 자기 네임스페이스 키만 씁니다.

| 파일 | 키 | 쓰는 곳 | 내용 |
|---|---|---|---|
| transport.json | `transport.*` | se650/transport | 포트 조회 오류, 연결/끊김 로그 |
| protocol.json | `protocol.*` | se650/protocol | 알 수 없는 값 오류 |
| control.json | `control.*` | se650/control | 키어/재요청/입력이름 로그 |
| app.json | `app.*` | se650/app | 화면 문구, 선택지 이름, CLI 도움말 |

- 아래 계층의 오류는 `LocalizedError` 로 **키를 들고** 올라옵니다. 화면에 표시하는 순간 현재 언어로
  번역되고, 코드/테스트는 언어와 무관하게 `error.key` 로 원인을 구분합니다.
- 콤보박스는 번역된 이름을 **보여주고**, 설정 파일과 장비에는 언어와 무관한 ID(`"Analog"` 등)를 저장합니다.
  그래서 언어를 바꿔도 저장된 설정이 깨지지 않습니다.
- 번역이 빠진 키는 영어로, 영어에도 없으면 키 자체가 표시됩니다. 자리표시자 실수가 있어도 프로그램은 죽지 않습니다.

### 번역하지 않는 용어

방송 장비에서 영어가 표준인 용어는 모든 언어팩에서 영어 그대로 둡니다 (실제 SE-650 패널 표기와 일치).

- 버스·버튼: PGM, PVW, AUTO, CUT, FTB, KEY 1, P-in-P, DSK 1, DSK AUTO, DSK CUT
- 소스: Black, In 1~4, Still, Freeze, Matte, Still 1, Still 2
- 트랜지션 타입: Mix, Wipe / 출력: PGM+DSK, PVW+DSK, MultiView

목록은 `tests/test_i18n.py` 의 `TERMINOLOGY_PREFIXES` 이고, 언어팩에서 이 값이 영어와 다르면 테스트가 실패합니다.
섹션 제목, 안내 문구, 메뉴 버튼, 오디오 모드(끔/디지털/아날로그) 같은 일반 단어는 번역합니다.

### 새 언어 추가 (예: 일본어)

1. `se650/i18n/locales/en` 폴더를 `ja` 로 복사
2. `_meta.json` 의 `name` 을 `日本語`, `english_name` 을 `Japanese` 로
3. 나머지 4개 JSON 의 **값만** 번역 (키와 `{host}` 같은 자리표시자, 위의 "번역하지 않는 용어"는 그대로)
4. `python -m unittest tests.test_i18n` — 빠진 키, 남는 키, 자리표시자 불일치, 빈 문구를 잡아냅니다

코드 수정은 필요 없습니다. 언어 선택 목록에 자동으로 나타납니다.

### 코드에 문구 추가할 때

`tr("control.log.something", value=x)` 처럼 쓰고, 모든 언어팩의 해당 계층 파일에 키를 추가합니다.
아래 규칙은 테스트가 강제합니다:
- 다른 계층의 키를 쓰면 실패 (transport 코드에서 `app.*` 사용 등)
- 코드에 쓴 키가 영어 언어팩에 없으면 실패 (오타 방지)
- 코드에 한글 문구를 직접 쓰면 실패 (주석·docstring 은 허용)

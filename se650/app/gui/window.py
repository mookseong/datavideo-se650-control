"""메인 창: 패널 배치, 연결 수명 관리, 스레드 -> GUI 이벤트 전달."""
import logging
import queue
import threading
import time
import tkinter as tk
from tkinter import scrolledtext

from ... import open_switcher
from ...i18n import available_languages, set_locale, tr
from ...protocol.constants import SOURCE_NAMES
from ..settings import Settings
from . import theme
from .device_panel import DevicePanel
from .panels import ConnectionBar, ExtrasPanel

log = logging.getLogger("se650.app")

POLL_INTERVAL_MS = 50
READY_TIMEOUT = 3.0
RECONNECT_DELAY = 1.5          # 해제 직후 재연결 전 대기: 스위처가 이전 채널을 정리할 시간
SHORTCUT_IGNORED_WIDGETS = {"Entry", "TEntry", "TSpinbox", "Spinbox", "Text", "TCombobox"}
SHORTCUTS = {
    "Return": lambda sw: sw.auto(),
    "space": lambda sw: sw.cut(),
    "f": lambda sw: sw.fade_to_black(),
    "F": lambda sw: sw.fade_to_black(),
    **{str(n): (lambda sw, n=n: sw.set_preview(n)) for n in range(1, 5)},
}


class _QueueLogHandler(logging.Handler):
    """다른 스레드의 로그를 GUI 스레드로 넘긴다."""

    def __init__(self, events: queue.Queue):
        super().__init__(logging.INFO)
        self._events = events
        self.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))

    def emit(self, record):
        self._events.put(("log", self.format(record)))


class MainWindow:
    """
    switcher_factory(host, on_change) -> Switcher.  테스트에서 연결 옵션을 바꿔 끼우는 지점.
    """

    def __init__(self, root, settings: Settings = None, switcher_factory=open_switcher, language: str = None):
        self.root = root
        self.settings = settings or Settings()
        self._switcher_factory = switcher_factory
        self.switcher = None
        self._events = queue.Queue()      # 모든 tk 위젯 변경은 GUI 스레드의 _poll 에서만
        self._poll_job = None
        self._status = ("disconnected", None)
        self._last_disconnect = float("-inf")
        self._pending_connect = None

        set_locale(language or self.settings["language"])     # "" = 시스템 언어
        root.resizable(False, False)
        theme.apply_style(root)
        self._build()
        self._log_handler = _QueueLogHandler(self._events)
        self._attach_logging()

        root.bind("<Key>", self._on_key)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._restore_geometry()
        self.log(tr("app.message.hint"))
        self.log(tr("app.message.settings_file", path=self.settings.path))
        self._poll()
        if self.settings["auto_connect"] and self.settings["ip"]:
            root.after(300, self.toggle_connection)

    # ------------------------------------------------------------ 화면 구성
    def _build(self):
        self.root.title(tr("app.window_title"))
        self.connection_bar = ConnectionBar(self.root, self.settings, self.toggle_connection,
                                            self.refresh_from_device, self.change_language)
        self.connection_bar.grid(row=0, column=0, sticky="ew")
        self.device = DevicePanel(self.root, self.run, self.log)
        self.device.grid(row=1, column=0, sticky="ew", padx=10)
        self.extras = ExtrasPanel(self.root, self.run, self.settings, self.log)
        self.extras.grid(row=2, column=0, sticky="ew", padx=10, pady=(8, 6))

        self.log_box = scrolledtext.ScrolledText(self.root, height=7, bg=theme.LOG_BACKGROUND, fg=theme.LOG_TEXT,
                                                 font=theme.FONT_LOG, state="disabled", relief="flat")
        self.log_box.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 10))
        self._panels = (self.device, self.extras)

    def _rebuild(self):
        """언어 변경: 위젯을 새 언어로 다시 만든다. 연결/로그/상태는 유지."""
        log_text = self.log_box.get("1.0", "end-1c")
        for widget in self.root.winfo_children():
            widget.destroy()
        self._build()
        if log_text:
            self._append_log(log_text)
        self._show_status(*self._status)
        self._refresh_panels()

    # ------------------------------------------------------------ 조작
    def run(self, action) -> bool:
        """패널 버튼 공통 진입점: 연결 확인 + 예외를 로그로. 실행했으면 True."""
        if self.switcher is None or not self.switcher.connected:
            self.log(tr("app.message.connect_first"))
            return False
        try:
            action(self.switcher)
        except Exception as error:      # noqa: BLE001 - 어떤 오류든 사용자에게 보여준다
            self.log(tr("app.message.error", error=error))
            return False
        return True

    def log(self, message: str):
        """화면 로그 + 로그 파일 (logging 을 거쳐 _QueueLogHandler 가 화면에 넣는다)."""
        log.info(message)

    def toggle_connection(self):
        if self.switcher and self.switcher.connected:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        host = self.connection_bar.ip
        if not host:
            self.log(tr("app.message.enter_ip"))
            return
        self.settings.set("ip", host)
        self._show_status("connecting")
        wait = RECONNECT_DELAY - (time.monotonic() - self._last_disconnect)
        if wait > 0:
            self._pending_connect = self.root.after(int(wait * 1000), lambda: self._start_connect(host))
        else:
            self._start_connect(host)

    def _start_connect(self, host):
        self._pending_connect = None
        # 접속 도중 도착하는 상태 콜백도 처리되도록 connect() 전에 self.switcher 를 할당한다
        switcher = self._switcher_factory(host, on_change=lambda: self._events.put(("state", None)))
        self.switcher = switcher
        threading.Thread(target=self._connect_worker, args=(switcher,), name="connect", daemon=True).start()

    def disconnect(self):
        if self.switcher:
            self.switcher.close()
            self._last_disconnect = time.monotonic()
        self.switcher = None
        self._show_status("disconnected")
        self._refresh_panels()

    def refresh_from_device(self):
        self.run(lambda sw: sw.refresh())

    def change_language(self, code: str):
        applied = set_locale(code)
        self.settings.set("language", applied)
        self._rebuild()
        self.log(tr("app.message.language_changed", name=available_languages()[applied]))

    def close(self):
        try:
            self.settings.set("geometry", self.root.geometry())
        except tk.TclError:
            pass
        self.stop()
        self.root.destroy()

    def stop(self):
        """창을 닫지 않고 백그라운드 작업만 정리 (테스트에서도 사용)."""
        if self._pending_connect:
            self.root.after_cancel(self._pending_connect)
            self._pending_connect = None
        if self._poll_job:
            try:
                self.root.after_cancel(self._poll_job)
            except tk.TclError:
                pass
            self._poll_job = None
        logging.getLogger("se650").removeHandler(self._log_handler)
        if self.switcher:
            self.switcher.close()

    # ------------------------------------------------------------ 내부
    def _connect_worker(self, switcher):
        try:
            switcher.connect()
        except Exception as error:      # noqa: BLE001
            self.log(tr("app.message.connect_failed", error=error))
            self._events.put(("failed", switcher))
            return
        self._events.put(("connected", switcher))
        if switcher.wait_ready(READY_TIMEOUT):
            state = switcher.state
            self.log(tr("app.message.state_ok", pgm=SOURCE_NAMES.get(state.pgm, state.pgm),
                        pvw=SOURCE_NAMES.get(state.pvw, state.pvw)))
        else:
            self.log(tr("app.message.no_state", seconds=int(READY_TIMEOUT)))

    def _poll(self):
        while True:
            try:
                kind, payload = self._events.get_nowait()
            except queue.Empty:
                break
            self._handle_event(kind, payload)
        self._poll_job = self.root.after(POLL_INTERVAL_MS, self._poll)

    def _handle_event(self, kind, payload):
        if kind == "log":
            self._append_log(payload)
        elif kind == "state":
            self._refresh_panels()
        elif kind == "connected" and payload is self.switcher:
            self._show_status("connected", payload.host)
            self._refresh_panels()
        elif kind == "failed" and payload is self.switcher:
            self.switcher = None
            self._show_status("failed")

    def _refresh_panels(self):
        for panel in self._panels:
            panel.refresh(self.switcher)
        if self.switcher and not self.switcher.connected and self._status[0] == "connected":
            self._last_disconnect = time.monotonic()
            self._show_status("lost")

    def _show_status(self, status, host=None):
        self._status = (status, host)
        self.connection_bar.show(status, host)

    def _append_log(self, line):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _attach_logging(self):
        package_logger = logging.getLogger("se650")
        package_logger.setLevel(logging.INFO)
        package_logger.addHandler(self._log_handler)

    def _restore_geometry(self):
        if self.settings["geometry"]:
            try:
                self.root.geometry(self.settings["geometry"])
            except tk.TclError:
                pass

    def _on_key(self, event):
        if self.switcher is None or not self.switcher.connected:
            return
        if event.widget.winfo_class() in SHORTCUT_IGNORED_WIDGETS:
            return
        action = SHORTCUTS.get(event.keysym)
        if action:
            self.run(action)


def run_gui(language: str = None):
    root = tk.Tk()
    MainWindow(root, language=language)
    root.mainloop()

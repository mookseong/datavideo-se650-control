"""실제 패널 바깥의 영역: 연결 바, 추가 기능 (패널에 없지만 프로토콜로 가능한 기능).

실제 패널 배열은 device_panel.py.
조작은 run(action) 으로 넘긴다. 화면 문구는 app.* 번역 키로 가져온다.
"""
import tkinter as tk
from tkinter import ttk

from ...i18n import available_languages, get_locale, tr
from ...protocol.constants import (AUDIO_MODES, AUDIO_SOURCES, BOOT_MEMORY_SLOT, HDMI_OUTPUTS, KEY_SOURCES,
                                   TransitionStatus)

_DEVICE_CHOICES = (("hdmi", "hdmi_out", HDMI_OUTPUTS), ("audio", "audio_source", AUDIO_SOURCES),
                   ("audio_mode", "audio_mode", AUDIO_MODES))
from . import theme
from ..labels import translated_options
from .widgets import ChoiceBox


class ConnectionBar(ttk.Frame):
    STATUS_COLORS = {"disconnected": theme.MUTED, "connecting": theme.STATUS_BUSY, "connected": theme.STATUS_OK,
                     "failed": theme.STATUS_ERROR, "lost": theme.STATUS_ERROR}

    def __init__(self, parent, settings, on_connect, on_refresh, on_language, on_submit=None):
        """on_connect: 연결/해제 버튼, on_submit: IP 칸 Enter (연결만, 해제하지 않음)"""
        super().__init__(parent, padding=10)
        self._settings = settings
        ttk.Label(self, text=tr("app.connection.ip_label")).grid(row=0, column=0, padx=(0, 6))
        self.ip_var = tk.StringVar(value=settings["ip"])
        entry = ttk.Entry(self, textvariable=self.ip_var, width=18, font=theme.FONT_MONO)
        entry.grid(row=0, column=1)
        entry.bind("<Return>", lambda _e: (on_submit or on_connect)())
        self.connect_button = ttk.Button(self, command=on_connect)
        self.connect_button.grid(row=0, column=2, padx=6)
        self.status_var = tk.StringVar()
        self._status_label = tk.Label(self, textvariable=self.status_var, bg=theme.BACKGROUND, font=theme.FONT)
        self._status_label.grid(row=0, column=3, padx=12)
        ttk.Button(self, text=tr("app.connection.refresh"), command=on_refresh).grid(row=0, column=4)
        self.auto_connect_var = tk.BooleanVar(value=settings["auto_connect"])
        ttk.Checkbutton(self, text=tr("app.connection.auto_connect"), variable=self.auto_connect_var,
                        command=self._save_auto_connect).grid(row=0, column=5, padx=(10, 0))
        ttk.Label(self, text=tr("app.connection.language")).grid(row=0, column=6, padx=(14, 4))
        self.language = ChoiceBox(self, available_languages(), get_locale(), on_language, width=8)
        self.language.grid(row=0, column=7)
        self.show("disconnected")

    @property
    def ip(self) -> str:
        return self.ip_var.get().strip()

    def show(self, status: str, host: str = None):
        self.status_var.set(tr("app.status." + status, host=host))
        self._status_label.configure(fg=self.STATUS_COLORS[status])
        button_key = {"connected": "app.connection.disconnect",
                      "connecting": "app.connection.cancel"}.get(status, "app.connection.connect")
        self.connect_button.configure(text=tr(button_key), state="normal")      # 연결 중에는 취소 버튼

    def _save_auto_connect(self):
        self._settings.set("auto_connect", bool(self.auto_connect_var.get()))


class ExtrasPanel(ttk.Labelframe):
    """실제 패널에 없는 기능 (FTB, 프레임, 키어 소스, 출력/오디오, 메모리 0~999)."""

    KEYER_SOURCES = (("key1", "app.extras.keyer_source"), ("pip", "app.extras.pip_source"),
                     ("dsk1", "app.extras.logo_source"))

    def __init__(self, parent, run, settings, log, on_undocumented_toggle=None, confirm=None):
        """confirm(title, message) -> bool : 위험한 작업 확인 대화상자 (테스트에서 바꿔 끼움)"""
        super().__init__(parent, text=tr("app.extras.title"), padding=8)
        self._run = run
        self._confirm = confirm or _ask_yes_no
        self._settings = settings
        self._log = log
        self._on_undocumented_toggle = on_undocumented_toggle or (lambda allowed: None)

        # 1열: FTB, 트랜지션 프레임
        column = ttk.Frame(self)
        column.grid(row=0, column=0, sticky="n", padx=(0, 18))
        self.ftb_button = tk.Button(column, text=tr("app.panel.ftb"), width=8, height=2, relief="flat",
                                    font=theme.FONT_BIG, fg="#ffffff", bg=theme.FTB_IDLE,
                                    command=lambda: run(lambda sw: sw.fade_to_black()))
        self.ftb_button.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        ttk.Label(column, text=tr("app.extras.frames")).grid(row=1, column=0, columnspan=3, sticky="w")
        self.duration_var = tk.IntVar(value=settings["duration"])
        ttk.Spinbox(column, from_=1, to=300, textvariable=self.duration_var, width=6).grid(row=2, column=0)
        ttk.Button(column, text=tr("app.extras.apply"), width=6, command=self.apply_duration
                   ).grid(row=2, column=1, padx=4)

        # 2열: 키어 소스 (실제 패널은 PVW 버튼을 길게 누르고 소스 버튼으로 고름)
        column = ttk.Frame(self)
        column.grid(row=0, column=1, sticky="n", padx=(0, 18))
        self.sources = {}
        for row, (keyer, label_key) in enumerate(self.KEYER_SOURCES):
            ttk.Label(column, text=tr(label_key)).grid(row=row, column=0, sticky="w", pady=2)
            combo = ChoiceBox(column, translated_options("source", KEY_SOURCES), "In 1",
                              lambda name, k=keyer: run(lambda sw: sw.set_keyer_source(k, name)), width=8)
            combo.grid(row=row, column=1, padx=4, pady=2)
            self.sources[keyer] = combo

        # 3열: 출력 / 오디오
        column = ttk.Frame(self)
        column.grid(row=0, column=2, sticky="n", padx=(0, 18))
        self.hdmi = self._choice(column, 0, "app.extras.hdmi", "hdmi_out", HDMI_OUTPUTS, "hdmi_out",
                                 "set_hdmi_output")
        self.audio = self._choice(column, 1, "app.extras.audio", "audio_source", AUDIO_SOURCES, "audio_source",
                                  "set_audio_source")
        self.audio_mode = self._choice(column, 2, "app.extras.audio_mode", "audio_mode", AUDIO_MODES,
                                       "audio_mode", "set_audio_mode")

        # 4열: 유저 메모리 0~999 (패널의 USER 1~3 은 불러오기만)
        column = ttk.Frame(self)
        column.grid(row=0, column=3, sticky="n")
        ttk.Label(column, text=tr("app.extras.memory")).grid(row=0, column=0, columnspan=2, sticky="w")
        self.memory_var = tk.IntVar(value=settings["user_memory"])
        ttk.Spinbox(column, from_=0, to=999, textvariable=self.memory_var, width=6
                    ).grid(row=1, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Button(column, text=tr("app.extras.load"), width=8, command=lambda: self._memory("load_memory")
                   ).grid(row=2, column=0, pady=2)
        ttk.Button(column, text=tr("app.extras.save"), width=8, command=lambda: self._memory("store_memory")
                   ).grid(row=2, column=1, padx=4, pady=2)

        # 아래 줄: 문서에 없는 명령 허용 (기본 꺼짐)
        self.undocumented_var = tk.BooleanVar(value=settings["undocumented_menu"])
        ttk.Checkbutton(self, text=tr("app.extras.undocumented_menu"), variable=self.undocumented_var,
                        command=self._toggle_undocumented).grid(row=1, column=0, columnspan=4, sticky="w",
                                                                 pady=(8, 0))

    def apply_duration(self):
        try:
            frames = int(self.duration_var.get())
        except (ValueError, tk.TclError):
            self._log(tr("app.message.frames_not_number"))
            return
        self._settings.set("duration", frames)
        self._run(lambda sw: sw.set_duration(frames))

    def _choice(self, parent, row, label_key, table_name, table, setting_key, method_name):
        ttk.Label(parent, text=tr(label_key)).grid(row=row, column=0, sticky="w", pady=2)

        def on_select(name):
            self._settings.set(setting_key, name)
            self._run(lambda sw: getattr(sw, method_name)(name))
        combo = ChoiceBox(parent, translated_options(table_name, table), self._settings[setting_key], on_select)
        combo.grid(row=row, column=1, padx=4, pady=2)
        return combo

    def _toggle_undocumented(self):
        allowed = bool(self.undocumented_var.get())
        self._settings.set("undocumented_menu", allowed)
        self._on_undocumented_toggle(allowed)

    def _memory(self, method_name):
        try:
            number = int(self.memory_var.get())
        except (ValueError, tk.TclError):
            self._log(tr("app.message.memory_not_number"))
            return
        if method_name == "store_memory" and number == BOOT_MEMORY_SLOT:
            if not self._confirm(tr("app.extras.boot_slot_title"), tr("app.extras.boot_slot_confirm")):
                return
            self._run(lambda sw: sw.store_memory(number, confirm_boot_slot=True))
        else:
            self._run(lambda sw: getattr(sw, method_name)(number))
        if 0 <= number <= 999:
            self._settings.set("user_memory", number)

    def refresh(self, switcher):
        state = switcher.state if switcher else None
        fading = bool(state and (state.ftb.running or state.ftb.status == TransitionStatus.AT_END))
        self.ftb_button.configure(bg=theme.FTB_ACTIVE if fading else theme.FTB_IDLE)
        if not state:
            return
        if state.me.duration:
            self.duration_var.set(state.me.duration)
        for attribute, state_field, table in _DEVICE_CHOICES:            # 장비의 실제 값을 표시
            names_by_value = {value: name for name, value in table.items()}
            value = getattr(state, state_field)
            if value in names_by_value:
                getattr(self, attribute).value = names_by_value[value]


def _ask_yes_no(title, message) -> bool:
    from tkinter import messagebox
    return messagebox.askyesno(title, message, icon="warning", default="no")

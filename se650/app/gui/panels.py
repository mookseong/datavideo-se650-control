"""화면 영역별 패널. 각 패널은 위젯 생성(build)과 상태 반영(refresh)만 책임진다.

조작은 run(action) 으로 넘긴다. action 은 Switcher 를 받는 함수이며,
연결 확인과 예외 처리는 MainWindow.run 이 담당한다.
화면 문구는 모두 app.* 번역 키로 가져온다. 언어가 바뀌면 MainWindow 가 패널을 다시 만든다.
"""
import tkinter as tk
from tkinter import ttk

from ...i18n import available_languages, get_locale, tr
from ...protocol.constants import (AUDIO_MODES, AUDIO_SOURCES, HDMI_OUTPUTS, KEY_SOURCES, MENU_KEYS,
                                   SOURCES, TRANSITION_TYPES, TransitionStatus)
from . import theme
from .widgets import ChoiceBox, choice_key, translated_options

INPUT_COUNT = 4


class ConnectionBar(ttk.Frame):
    STATUS_COLORS = {"disconnected": theme.MUTED, "connecting": theme.STATUS_BUSY, "connected": theme.STATUS_OK,
                     "failed": theme.STATUS_ERROR, "lost": theme.STATUS_ERROR}

    def __init__(self, parent, settings, on_connect, on_refresh, on_language):
        super().__init__(parent, padding=10)
        self._settings = settings
        ttk.Label(self, text=tr("app.connection.ip_label")).grid(row=0, column=0, padx=(0, 6))
        self.ip_var = tk.StringVar(value=settings["ip"])
        entry = ttk.Entry(self, textvariable=self.ip_var, width=18, font=theme.FONT_MONO)
        entry.grid(row=0, column=1)
        entry.bind("<Return>", lambda _e: on_connect())
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
        button_key = "app.connection.disconnect" if status == "connected" else "app.connection.connect"
        self.connect_button.configure(text=tr(button_key),
                                      state="disabled" if status == "connecting" else "normal")

    def _save_auto_connect(self):
        self._settings.set("auto_connect", bool(self.auto_connect_var.get()))


class BusPanel(ttk.Frame):
    BUSES = (("pgm", theme.PGM), ("pvw", theme.PVW))

    def __init__(self, parent, run):
        super().__init__(parent, padding=(10, 0, 10, 6))
        self.buttons = {"pgm": {}, "pvw": {}}
        labels = translated_options("source", SOURCES)
        for row, (bus, color) in enumerate(self.BUSES):
            tk.Label(self, text=tr("app.bus." + bus), bg=theme.BACKGROUND, fg=color, font=theme.FONT_BIG,
                     width=5, anchor="w").grid(row=row, column=0, pady=3)
            for column, (name, source) in enumerate(SOURCES.items(), start=1):
                button = tk.Button(self, text=labels[name], width=8, height=2, relief="flat",
                                   font=theme.FONT_BOLD, activebackground="#444444",
                                   command=lambda b=bus, s=source: run(_bus_action(b, s)))
                button.grid(row=row, column=column, padx=2, pady=3)
                self.buttons[bus][source] = button
        self.refresh(None)

    def refresh(self, switcher):
        state = switcher.state if switcher else None
        for bus, color in self.BUSES:
            active = getattr(state, bus) if state else None
            for source, button in self.buttons[bus].items():
                theme.set_lamp(button, source == active, color)
        device_names = switcher.input_names if switcher else {}
        for number in range(1, INPUT_COUNT + 1):
            text = device_names.get(number, "")[:8] or tr(choice_key("source", "In %d" % number))
            for bus in self.buttons:
                self.buttons[bus][number].configure(text=text)


def _bus_action(bus, source):
    if bus == "pgm":
        return lambda sw: sw.set_program(source)
    return lambda sw: sw.set_preview(source)


class TransitionPanel(ttk.Labelframe):
    def __init__(self, parent, run, settings, log):
        super().__init__(parent, text=tr("app.transition.title"), padding=8)
        self._run = run
        self._settings = settings
        self._log = log
        big = dict(width=9, height=2, relief="flat", font=theme.FONT_BIG, fg="#ffffff")
        self.auto_button = tk.Button(self, text=tr("app.transition.auto"), bg=theme.AUTO,
                                     command=lambda: run(lambda sw: sw.auto()), **big)
        self.auto_button.grid(row=0, column=0, padx=2, pady=2)
        tk.Button(self, text=tr("app.transition.cut"), bg=theme.CUT, command=lambda: run(lambda sw: sw.cut()), **big
                  ).grid(row=0, column=1, padx=2, pady=2)
        self.ftb_button = tk.Button(self, text=tr("app.transition.ftb"), bg=theme.FTB_IDLE,
                                    command=lambda: run(lambda sw: sw.fade_to_black()), **big)
        self.ftb_button.grid(row=0, column=2, padx=2, pady=2)

        ttk.Label(self, text=tr("app.transition.type")).grid(row=1, column=0, sticky="e", pady=(6, 0))
        self.transition_type = ChoiceBox(self, translated_options("transition_type", TRANSITION_TYPES),
                                         settings["transition_type"], self._apply_type, width=8)
        self.transition_type.grid(row=1, column=1, pady=(6, 0))

        ttk.Label(self, text=tr("app.transition.frames")).grid(row=2, column=0, sticky="e", pady=(6, 0))
        self.duration_var = tk.IntVar(value=settings["duration"])
        ttk.Spinbox(self, from_=1, to=300, textvariable=self.duration_var, width=6).grid(row=2, column=1, pady=(6, 0))
        ttk.Button(self, text=tr("app.transition.apply"), width=6, command=self.apply_duration
                   ).grid(row=2, column=2, pady=(6, 0))

    def apply_duration(self):
        try:
            frames = int(self.duration_var.get())
        except (ValueError, tk.TclError):
            self._log(tr("app.message.frames_not_number"))
            return
        self._settings.set("duration", frames)
        self._run(lambda sw: sw.set_duration(frames))

    def _apply_type(self, name):
        self._settings.set("transition_type", name)
        self._run(lambda sw: sw.set_transition_type(name))

    def refresh(self, switcher):
        state = switcher.state if switcher else None
        running = bool(state and state.me.running)
        self.auto_button.configure(bg=theme.RUNNING if running else theme.AUTO)
        fading = bool(state and (state.ftb.running or state.ftb.status == TransitionStatus.AT_END))
        self.ftb_button.configure(bg=theme.FTB_ACTIVE if fading else theme.FTB_IDLE)
        if not state:
            return
        names_by_value = {value: name for name, value in TRANSITION_TYPES.items()}
        if state.transition_type in names_by_value:
            self.transition_type.value = names_by_value[state.transition_type]
        if state.me.duration:
            self.duration_var.set(state.me.duration)


class KeyerPanel(ttk.Labelframe):
    KEYERS = ("key1", "pip", "dsk1")
    BUSES = (("pgm", theme.PGM), ("pvw", theme.PVW))

    def __init__(self, parent, run):
        super().__init__(parent, text=tr("app.keyer.title"), padding=8)
        self.buttons = {}
        self.sources = {}
        for row, keyer in enumerate(self.KEYERS):
            ttk.Label(self, text=tr("app.keyer." + keyer), width=7).grid(row=row, column=0, sticky="w")
            for column, (bus, _) in enumerate(self.BUSES, start=1):
                button = tk.Button(self, text=tr("app.bus." + bus), width=6, relief="flat", font=theme.FONT_BOLD,
                                   command=lambda k=keyer, b=bus: run(lambda sw: sw.press_keyer(k, b)))
                button.grid(row=row, column=column, padx=2, pady=2)
                self.buttons[(keyer, bus)] = button
            combo = ChoiceBox(self, translated_options("source", KEY_SOURCES), "In 1",
                              lambda name, k=keyer: run(lambda sw: sw.set_keyer_source(k, name)), width=7)
            combo.grid(row=row, column=3, padx=(8, 0))
            self.sources[keyer] = combo
        small = dict(width=9, relief="flat", fg="#ffffff", font=theme.FONT_BOLD)
        tk.Button(self, text=tr("app.keyer.dsk_auto"), bg=theme.AUTO, command=lambda: run(lambda sw: sw.dsk_auto()),
                  **small).grid(row=3, column=1, columnspan=2, pady=(6, 0), sticky="ew")
        tk.Button(self, text=tr("app.keyer.dsk_cut"), bg=theme.CUT, command=lambda: run(lambda sw: sw.dsk_cut()),
                  **small).grid(row=3, column=3, pady=(6, 0), sticky="ew")
        self.refresh(None)

    def refresh(self, switcher):
        colors = dict(self.BUSES)
        for (keyer, bus), button in self.buttons.items():
            lit = False
            if switcher:
                state = switcher.state.keyers[keyer]
                lit = state.on if bus == "pgm" else state.preview
            theme.set_lamp(button, lit, colors[bus])


class OutputPanel(ttk.Labelframe):
    def __init__(self, parent, run, settings):
        super().__init__(parent, text=tr("app.output.title"), padding=8)
        self._run = run
        self._settings = settings
        self.hdmi = self._choice(0, "app.output.hdmi", "hdmi_out", HDMI_OUTPUTS, "hdmi_out", "set_hdmi_output")
        self.audio = self._choice(1, "app.output.audio", "audio_source", AUDIO_SOURCES, "audio_source",
                                  "set_audio_source")
        self.audio_mode = self._choice(2, "app.output.audio_mode", "audio_mode", AUDIO_MODES, "audio_mode",
                                       "set_audio_mode")

        ttk.Label(self, text=tr("app.output.user")).grid(row=3, column=0, sticky="w")
        self.memory_var = tk.IntVar(value=settings["user_memory"])
        ttk.Spinbox(self, from_=0, to=999, textvariable=self.memory_var, width=5
                    ).grid(row=3, column=1, sticky="w", padx=4, pady=2)
        ttk.Button(self, text=tr("app.output.load"), width=8, command=lambda: self._memory("load_memory")
                   ).grid(row=4, column=0, pady=2)
        ttk.Button(self, text=tr("app.output.save"), width=8, command=lambda: self._memory("store_memory")
                   ).grid(row=4, column=1, pady=2, sticky="w")

        menu = ttk.Frame(self)
        menu.grid(row=5, column=0, columnspan=2, pady=(6, 0))
        for index, key in enumerate(MENU_KEYS):
            ttk.Button(menu, text=tr("app.menu." + key.lower()), width=6,
                       command=lambda k=key: run(lambda sw: sw.press_menu(k))
                       ).grid(row=index // 3, column=index % 3, padx=1, pady=1)

    def _choice(self, row, label_key, table_name, table, setting_key, method_name):
        ttk.Label(self, text=tr(label_key)).grid(row=row, column=0, sticky="w")

        def on_select(name):
            self._settings.set(setting_key, name)
            self._run(lambda sw: getattr(sw, method_name)(name))
        combo = ChoiceBox(self, translated_options(table_name, table), self._settings[setting_key], on_select)
        combo.grid(row=row, column=1, padx=4, pady=2)
        return combo

    def _memory(self, method_name):
        number = int(self.memory_var.get())
        self._settings.set("user_memory", number)
        self._run(lambda sw: getattr(sw, method_name)(number))

    def refresh(self, switcher):
        pass        # 출력/오디오 값은 장비 상태를 읽지 않는다 (설정값 유지)

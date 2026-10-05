"""실제 SE-650 패널 배열을 그대로 옮긴 화면.

    ┌ MENU ┐  ┌── WIPE ──┐        ┌ PIP ┐┌ KEYER ┐   ┌ T-BAR ┐
    │ ▲    │  │ ▯  ▭ REV │        │ PGM ││ PGM   │   │       │
    │◀ENT▶│  ├TRANSITION┤        │ PVW ││ PVW   │   │       │
    │ ▼    │  │TRANS MIX WIPE│     └─────┘└───────┘   │       │
    └──────┘  ├USER MEMORY─────┐  [LOGO ON/OFF]       │       │
              │RESET USER1 2 3 │                      └───────┘
    ┌─────────────── PROGRAM ───────────────┐
    │ BKG  1  2  3  4  STILL                │
    │ BKG  1  2  3  4  STILL                │       [CUT] [AUTO]
    └─────────────── PREVIEW ───────────────┘
              [PC1][PC2][카메라1][카메라2]   <- 장비 입력 이름 스티커

그룹마다 클래스 하나: 위젯 생성 + refresh(switcher) 로 램프 반영.
조작은 run(action) 으로 넘기며 action 은 Switcher 를 받는다.
"""
import tkinter as tk

from ...i18n import tr
from ...protocol.constants import SOURCE_MATTE, SOURCE_STILL_2, STILL_SOURCES, WIPE_PRESETS, TransitionType
from . import theme

INPUT_COUNT = 4
ARROWS = {"up": "▲", "down": "▼", "left": "◀", "right": "▶"}


class MenuPad(tk.Frame):
    """원격 메뉴 키는 SE-650 문서에 없는 명령이라 허용했을 때만 동작한다 (아니면 흐리게 + 안내)."""

    def __init__(self, parent, run):
        super().__init__(parent, bg=theme.DEVICE_BACKGROUND)
        menu_key = {"menu": "MENU", "up": "UP", "down": "DOWN", "left": "LEFT", "right": "RIGHT", "enter": "ENTER"}
        self.buttons = {}

        def add(key, text, row, column, size):
            button = theme.device_button(self, text, lambda: run(lambda sw: sw.press_menu(menu_key[key])), size)
            button.grid(row=row, column=column, padx=2, pady=2)
            self.buttons[key] = button

        add("menu", tr("app.panel.menu"), 0, 1, (72, 30))
        add("up", ARROWS["up"], 1, 1, (72, 40))
        add("left", ARROWS["left"], 2, 0, (44, 60))
        add("enter", tr("app.panel.enter"), 2, 1, (72, 60))
        add("right", ARROWS["right"], 2, 2, (44, 60))
        add("down", ARROWS["down"], 3, 1, (72, 40))

    def refresh(self, switcher):
        allowed = bool(switcher and switcher.allow_undocumented)
        for button in self.buttons.values():
            button.configure(fg=theme.LAMP_OFF_TEXT if allowed else theme.MUTED)


class WipeGroup(tk.LabelFrame):
    def __init__(self, parent, run):
        super().__init__(parent, text=tr("app.panel.wipe"))
        theme_frame(self)
        self._icons = {n: theme.wipe_icon(self, n) for n in WIPE_PRESETS}     # tk 이미지는 참조를 유지해야 보인다
        self.buttons = {}
        for column, pattern in enumerate(WIPE_PRESETS):
            button = theme.device_button(self, image=self._icons[pattern],
                                         command=lambda p=pattern: run(lambda sw: sw.select_wipe_pattern(p)))
            button.grid(row=0, column=column, padx=3, pady=2)
            self.buttons["pattern_%d" % pattern] = button
        self.buttons["rev"] = theme.device_button(self, tr("app.panel.rev"),
                                                  lambda: run(lambda sw: sw.toggle_wipe_reverse()))
        self.buttons["rev"].grid(row=0, column=len(WIPE_PRESETS), padx=3, pady=2)
        self.refresh(None)

    def refresh(self, switcher):
        state = switcher.state if switcher else None
        for pattern in WIPE_PRESETS:
            theme.set_device_lamp(self.buttons["pattern_%d" % pattern],
                                  bool(state and state.wipe_pattern == pattern), theme.WIPE_LIT)
        theme.set_device_lamp(self.buttons["rev"], bool(state and state.wipe_reverse), theme.WIPE_LIT)


class TransitionGroup(tk.LabelFrame):
    def __init__(self, parent, run):
        super().__init__(parent, text=tr("app.panel.transition"))
        theme_frame(self)
        actions = {
            "trans_bg": lambda sw: sw.toggle_transition_background(),
            "mix": lambda sw: sw.set_transition_type("Mix"),
            "wipe": lambda sw: sw.set_transition_type("Wipe"),
        }
        labels = {"trans_bg": "app.panel.trans_bg", "mix": "app.panel.mix", "wipe": "app.panel.wipe_on"}
        self.buttons = {}
        for column, key in enumerate(actions):
            button = theme.device_button(self, tr(labels[key]), lambda a=actions[key]: run(a))
            button.grid(row=0, column=column, padx=3, pady=2)
            self.buttons[key] = button
        self.refresh(None)

    def refresh(self, switcher):
        state = switcher.state if switcher else None
        kind = state.transition_type if state else None
        lamps = {
            "trans_bg": bool(state and state.transition_background),
            "mix": kind in (TransitionType.MIX, TransitionType.CLIP),       # 매뉴얼: MIX+WIPE 동시 = Clip
            "wipe": kind in (TransitionType.WIPE, TransitionType.CLIP),
        }
        for key, lit in lamps.items():
            theme.set_device_lamp(self.buttons[key], lit, theme.TRANSITION_LIT)


class UserMemoryGroup(tk.LabelFrame):
    USER_SLOTS = (1, 2, 3)

    def __init__(self, parent, run, log):
        super().__init__(parent, text=tr("app.panel.user_memory"))
        theme_frame(self)
        self.buttons = {"reset": theme.device_button(self, tr("app.panel.reset"),
                                                     lambda: log(tr("app.message.reset_unsupported")))}
        self.buttons["reset"].configure(fg=theme.MUTED)      # 네트워크로 지원되지 않음을 흐리게 표시
        self.buttons["reset"].grid(row=0, column=0, padx=(3, 10), pady=2)
        for slot in self.USER_SLOTS:
            button = theme.device_button(self, tr("app.panel.user", number=slot),
                                         lambda n=slot: run(lambda sw: sw.load_memory(n)))
            button.grid(row=0, column=slot, padx=3, pady=2)
            self.buttons["user_%d" % slot] = button

    def refresh(self, switcher):
        pass


class PipKeyerGroup(tk.Frame):
    """PIP 와 KEYER 는 패널에서 나란히, 각각 PGM 위 / PVW 아래."""
    COLUMNS = (("pip", "app.panel.pip"), ("key1", "app.panel.keyer"))
    BUSES = (("pgm", theme.PGM), ("pvw", theme.PVW))

    def __init__(self, parent, run):
        super().__init__(parent, bg=theme.DEVICE_BACKGROUND)
        self.buttons = {}
        for column, (keyer, label_key) in enumerate(self.COLUMNS):
            group = theme.group_frame(self, tr(label_key))
            group.grid(row=0, column=column, padx=(0, 4), sticky="n")
            for row, (bus, _) in enumerate(self.BUSES):
                button = theme.device_button(group, tr("app.panel." + bus),
                                             lambda k=keyer, b=bus: run(lambda sw: sw.press_keyer(k, b)))
                button.grid(row=row, column=0, padx=4, pady=3)
                self.buttons[(keyer, bus)] = button
        self.refresh(None)

    def refresh(self, switcher):
        colors = dict(self.BUSES)
        for (keyer, bus), button in self.buttons.items():
            lit = False
            if switcher:
                state = switcher.state.keyers[keyer]
                lit = state.on if bus == "pgm" else state.preview
            theme.set_device_lamp(button, lit, colors[bus])


class LogoButton(tk.Frame):
    def __init__(self, parent, run):
        super().__init__(parent, bg=theme.DEVICE_BACKGROUND)
        self.button = theme.device_button(self, tr("app.panel.logo"), lambda: run(lambda sw: sw.toggle_logo()))
        self.button.pack()
        self.refresh(None)

    def refresh(self, switcher):
        on = bool(switcher and switcher.state.keyers["dsk1"].on)
        theme.set_device_lamp(self.button, on, theme.LOGO_LIT)


class SourceRows(tk.LabelFrame):
    """PROGRAM 줄 / PREVIEW 줄 + 아래 이름 스티커."""
    BUTTONS = ("bkg", "1", "2", "3", "4", "still")
    BUSES = (("pgm", theme.PGM), ("pvw", theme.PVW))

    def __init__(self, parent, run):
        super().__init__(parent, text=tr("app.panel.program"))
        theme_frame(self)
        self._run = run
        self.buttons = {"pgm": {}, "pvw": {}}
        for row, (bus, _) in enumerate(self.BUSES):
            for column, key in enumerate(self.BUTTONS):
                button = theme.device_button(self, self._label(key), self._action(bus, key),
                                             theme.SOURCE_BUTTON_SIZE,
                                             theme.FONT_DIGIT if key.isdigit() else theme.FONT_BIG)
                button.grid(row=row, column=column, padx=5, pady=4)
                self.buttons[bus][key] = button
        tk.Label(self, text=tr("app.panel.preview"), bg=theme.DEVICE_BACKGROUND, fg=theme.DEVICE_LINE,
                 font=theme.FONT_GROUP).grid(row=2, column=0, columnspan=len(self.BUTTONS))
        self.stickers = {}
        for number in range(1, INPUT_COUNT + 1):
            sticker = tk.Label(self, text="", bg=theme.DEVICE_BACKGROUND, fg="#000000", font=theme.FONT, width=11)
            sticker.grid(row=3, column=number, pady=(2, 0))
            self.stickers[number] = sticker
        self.refresh(None)

    @staticmethod
    def _label(key):
        return {"bkg": tr("app.panel.bkg"), "still": tr("app.panel.still")}.get(key, key)

    def _action(self, bus, key):
        if key == "still":
            return lambda: self._run(lambda sw: sw.press_still(bus))
        source = SOURCE_MATTE if key == "bkg" else int(key)       # 매뉴얼: BKG = Matte
        if bus == "pgm":
            return lambda: self._run(lambda sw: sw.set_program(source))
        return lambda: self._run(lambda sw: sw.set_preview(source))

    def refresh(self, switcher):
        state = switcher.state if switcher else None
        for bus, color in self.BUSES:
            active = getattr(state, bus) if state else None
            for key, button in self.buttons[bus].items():
                theme.set_device_lamp(button, active is not None and _matches(key, active), color)
            self.buttons[bus]["still"].configure(
                text=tr("app.panel.still_2") if active == SOURCE_STILL_2 else tr("app.panel.still"))
        names = switcher.input_names if switcher else {}
        for number, sticker in self.stickers.items():
            name = names.get(number, "")
            sticker.configure(text=name[:10], bg=theme.STICKER if name else theme.DEVICE_BACKGROUND)


def _matches(key, source):
    if key == "bkg":
        return source == SOURCE_MATTE
    if key == "still":
        return source in STILL_SOURCES
    return source == int(key)


class TBar(tk.Frame):
    """T-바. 값은 Switcher 가 실시간 답장에 실어 보내므로(필드당 최대 1회) 여기서 속도를 조절하지 않는다."""

    def __init__(self, parent, run):
        super().__init__(parent, bg=theme.DEVICE_BACKGROUND)
        self._run = run
        self._blocked = False         # 연결 안 됨: 이번 드래그 동안은 다시 시도하지 않음 (로그 도배 방지)
        self.scale = tk.Scale(self, from_=100, to=0, orient="vertical", length=250, width=40, sliderlength=56,
                              showvalue=False, bg="#b8b8b8", troughcolor="#111111", activebackground="#e0e0e0",
                              highlightthickness=0, bd=2, relief="sunken", command=self._on_move)
        self.scale.pack(padx=20, pady=4)
        self.scale.bind("<ButtonRelease-1>", self._on_release)

    def _on_move(self, value):
        if self._blocked:
            return
        position = float(value)
        if not self._run(lambda sw: sw.move_tbar(position)):
            self._blocked = True

    def _on_release(self, _event):
        self._blocked = False

    def refresh(self, switcher):
        pass


class CutAuto(tk.Frame):
    def __init__(self, parent, run):
        super().__init__(parent, bg=theme.DEVICE_BACKGROUND)
        self.cut_button = theme.device_button(self, tr("app.panel.cut"), lambda: run(lambda sw: sw.cut()),
                                              theme.SOURCE_BUTTON_SIZE, theme.FONT_BIG)
        self.cut_button.grid(row=0, column=0, padx=5)
        self.auto_button = theme.device_button(self, tr("app.panel.auto"), lambda: run(lambda sw: sw.auto()),
                                               theme.SOURCE_BUTTON_SIZE, theme.FONT_BIG)
        self.auto_button.grid(row=0, column=1, padx=5)
        self.refresh(None)

    def refresh(self, switcher):
        running = bool(switcher and switcher.state.me.running)
        theme.set_device_lamp(self.auto_button, running, theme.RUNNING)


class DevicePanel(tk.Frame):
    def __init__(self, parent, run, log):
        super().__init__(parent, bg=theme.DEVICE_BACKGROUND, padx=14, pady=10, highlightthickness=1,
                         highlightbackground="#111111")
        header = tk.Frame(self, bg=theme.DEVICE_BACKGROUND)
        header.grid(row=0, column=1, columnspan=3, sticky="e", pady=(0, 6))
        tk.Label(header, text=tr("app.panel.model"), bg=theme.DEVICE_BADGE, fg="#1d3b36", font=theme.FONT_BIG,
                 padx=14).pack(side="left", padx=8)
        tk.Label(header, text=tr("app.panel.subtitle"), bg=theme.DEVICE_BACKGROUND, fg="#e8e8e8",
                 font=theme.FONT_BIG).pack(side="left")

        self.menu = MenuPad(self, run)
        self.menu.grid(row=1, column=0, rowspan=3, padx=(0, 12), sticky="n")
        self.wipe = WipeGroup(self, run)
        self.wipe.grid(row=1, column=1, sticky="w", pady=2)
        self.transition = TransitionGroup(self, run)
        self.transition.grid(row=2, column=1, sticky="w", pady=2)
        self.memory = UserMemoryGroup(self, run, log)
        self.memory.grid(row=3, column=1, columnspan=2, sticky="w", pady=2)
        self.keyers = PipKeyerGroup(self, run)
        self.keyers.grid(row=1, column=2, rowspan=2, padx=12, sticky="n")
        self.logo = LogoButton(self, run)
        self.logo.grid(row=3, column=2, sticky="e", padx=12)
        self.tbar = TBar(self, run)
        self.tbar.grid(row=1, column=3, rowspan=3, sticky="n")
        self.sources = SourceRows(self, run)
        self.sources.grid(row=4, column=0, columnspan=3, sticky="w", pady=(10, 0))
        self.cut_auto = CutAuto(self, run)
        self.cut_auto.grid(row=4, column=3, sticky="s", pady=(0, 46))

    @property
    def groups(self):
        return (self.menu, self.wipe, self.transition, self.memory, self.keyers, self.logo, self.tbar,
                self.sources, self.cut_auto)

    def refresh(self, switcher):
        for group in self.groups:
            group.refresh(switcher)


def theme_frame(frame: tk.LabelFrame):
    frame.configure(labelanchor="n", bg=theme.DEVICE_BACKGROUND, fg=theme.DEVICE_LINE, font=theme.FONT_GROUP,
                    bd=2, relief="groove", padx=6, pady=4)

"""색상과 ttk 스타일."""
BACKGROUND = "#1e1e1e"
TEXT = "#dddddd"
MUTED = "#888888"
LOG_BACKGROUND = "#111111"
LOG_TEXT = "#9e9e9e"

LAMP_OFF = "#333333"
LAMP_OFF_TEXT = "#eeeeee"
LAMP_ON_TEXT = "#000000"
PGM = "#f44336"
PVW = "#4caf50"
AUTO = "#1565c0"
CUT = "#6a1b9a"
RUNNING = "#ff9800"
FTB_IDLE = "#424242"
FTB_ACTIVE = "#d32f2f"
STATUS_OK = "#4caf50"
STATUS_BUSY = "#ff9800"
STATUS_ERROR = "#f44336"

FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_BIG = ("Segoe UI", 11, "bold")
FONT_MONO = ("Consolas", 11)
FONT_LOG = ("Consolas", 9)


def apply_style(root):
    from tkinter import TclError, ttk
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except TclError:
        pass
    style.configure("TFrame", background=BACKGROUND)
    style.configure("TLabel", background=BACKGROUND, foreground=TEXT, font=FONT)
    style.configure("TLabelframe", background=BACKGROUND, foreground="#aaaaaa")
    style.configure("TLabelframe.Label", background=BACKGROUND, foreground="#aaaaaa", font=("Segoe UI", 9, "bold"))
    style.configure("TButton", font=FONT, padding=6)
    style.configure("TCheckbutton", background=BACKGROUND, foreground=TEXT)
    root.configure(bg=BACKGROUND)


def set_lamp(button, on: bool, color: str):
    button.configure(bg=color if on else LAMP_OFF, fg=LAMP_ON_TEXT if on else LAMP_OFF_TEXT)


def is_lit(button, color: str) -> bool:
    return button.cget("bg") == color


# ---------------------------------------------------------------- 실제 패널 모양
DEVICE_BACKGROUND = "#2b2d2f"
DEVICE_LINE = "#8fb3a6"           # 패널의 그룹 테두리/글자 (청록빛 회색)
DEVICE_BADGE = "#7fd6c8"
BUTTON_FACE = "#1c1c1c"
BUTTON_RIM = "#cfcfcf"
STICKER = "#f5c518"                # 버튼 아래 이름 스티커
WIPE_LIT = "#ff8a3d"
TRANSITION_LIT = "#ff4d4d"
LOGO_LIT = "#ffb020"
FONT_DIGIT = ("Segoe UI", 18, "bold")
FONT_GROUP = ("Segoe UI", 9, "bold")


BUTTON_SIZE = (86, 46)            # 픽셀. 실제 패널 버튼처럼 글꼴과 무관하게 같은 크기
SOURCE_BUTTON_SIZE = (92, 64)


def device_button(parent, text="", command=None, size=BUTTON_SIZE, font=FONT_BOLD, image=None):
    """실제 패널처럼: 어두운 버튼 면 + 밝은 테두리, 픽셀 고정 크기."""
    import tkinter as tk
    width, height = size
    button = tk.Button(parent, text=text, command=command, image=image or _blank_image(parent), compound="center",
                       width=width, height=height, font=font, bg=BUTTON_FACE, fg=LAMP_OFF_TEXT,
                       activebackground="#3a3a3a", activeforeground="#ffffff", relief="raised", bd=3,
                       highlightthickness=2, highlightbackground=BUTTON_RIM, takefocus=0)
    return button


def _blank_image(widget):
    """tk.Button 은 이미지가 있어야 width/height 를 픽셀로 해석한다 -> 투명 1x1 이미지를 붙인다."""
    import tkinter as tk
    root = widget.winfo_toplevel()
    if not hasattr(root, "_se650_blank_image"):
        root._se650_blank_image = tk.PhotoImage(master=root, width=1, height=1)   # 창마다 따로 (Tk 인스턴스별)
    return root._se650_blank_image


def set_device_lamp(button, on: bool, color: str):
    button.configure(bg=color if on else BUTTON_FACE, fg=LAMP_ON_TEXT if on else LAMP_OFF_TEXT)


def group_frame(parent, text, anchor="n"):
    import tkinter as tk
    return tk.LabelFrame(parent, text=text, labelanchor=anchor, bg=DEVICE_BACKGROUND, fg=DEVICE_LINE,
                         font=FONT_GROUP, bd=2, relief="groove", padx=6, pady=4)


def wipe_icon(master, pattern: int):
    """와이프 프리셋 아이콘 (흰색 = 현재 PGM, 검정 = 들어오는 화면; 매뉴얼 표기 방식)."""
    import tkinter as tk
    width, height = 30, 20
    image = tk.PhotoImage(master=master, width=width, height=height)
    image.put("#000000", to=(0, 0, width, height))
    image.put("#ffffff", to=(2, 2, width - 2, height - 2))
    if pattern == 1:
        image.put("#000000", to=(11, 2, 19, height - 2))         # 세로 띠
    else:
        image.put("#000000", to=(7, 6, width - 7, height - 5))   # 가운데 박스
    return image

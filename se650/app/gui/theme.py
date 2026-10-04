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

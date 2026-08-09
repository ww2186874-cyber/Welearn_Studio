"""Dark palette and point-based interface scaling."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPalette, QWheelEvent
from PySide6.QtWidgets import QApplication

MIN_SCALE = 80
MAX_SCALE = 200
DEFAULT_SCALE = 100

COLORS = {
    "canvas": "#111318",
    "sidebar": "#171a20",
    "panel": "#1d2129",
    "panel_alt": "#222731",
    "line": "#323844",
    "line_strong": "#454d5d",
    "text": "#f1f3f7",
    "muted": "#9ca5b4",
    "disabled": "#676f7c",
    "accent": "#46a8ff",
    "accent_hover": "#6bb9ff",
    "accent_pressed": "#2187de",
    "success": "#4bc98b",
    "warning": "#f2b84b",
    "danger": "#f06a73",
}

ACCOUNT_STATE_COLORS = {
    "pending": "#a9b1bd",
    "signed_in": "#4bc98b",
    "homework": "#46a8ff",
    "time_study": "#b38cff",
    "accepted": "#38c7c1",
    "unknown": "#f2b84b",
    "stopped": "#7e8795",
    "error": "#f06a73",
}

ACCOUNT_STATE_LABELS = {
    "pending": "待处理",
    "signed_in": "已登录",
    "homework": "作业运行中",
    "time_study": "时长学习中",
    "accepted": "请求已接受",
    "unknown": "部分状态未知",
    "stopped": "已停止",
    "error": "错误",
}


def _space(value: int, scale: int) -> int:
    return max(1, round(value * scale / 100))


def build_stylesheet(scale: int) -> str:
    """Build spacing rules while fonts remain in device-independent points."""
    scale = max(MIN_SCALE, min(MAX_SCALE, int(scale)))
    s4, s6, s8, s10, s12 = (_space(value, scale) for value in (4, 6, 8, 10, 12))
    radius = _space(5, scale)
    return f"""
        QWidget {{ color: {COLORS["text"]}; selection-background-color: {COLORS["accent_pressed"]}; }}
        QMainWindow, QWidget#appCanvas {{ background: {COLORS["canvas"]}; }}
        QFrame#sidebar, QFrame#runtimePanel {{ background: {COLORS["sidebar"]}; }}
        QFrame#surface, QWidget#surface {{
            background: {COLORS["panel"]}; border: 1px solid {COLORS["line"]};
            border-radius: {radius}px;
        }}
        QLabel#muted, QLabel[muted="true"] {{ color: {COLORS["muted"]}; }}
        QLabel#sectionTitle {{ font-weight: 600; }}
        QLabel#pageTitle {{ font-size: 15pt; font-weight: 650; }}
        QLabel#accountCountdown {{
            color: {COLORS["success"]};
            font-family: "Cascadia Mono", "Consolas", monospace;
            font-weight: 650;
        }}
        QLabel#stateDot {{ border-radius: {_space(4, scale)}px; min-width: {_space(8, scale)}px; max-width: {_space(8, scale)}px; min-height: {_space(8, scale)}px; max-height: {_space(8, scale)}px; }}
        QPushButton, QToolButton {{
            background: {COLORS["panel_alt"]}; border: 1px solid {COLORS["line"]};
            border-radius: {radius}px; padding: {s6}px {s10}px;
        }}
        QPushButton:hover, QToolButton:hover {{ border-color: {COLORS["line_strong"]}; background: #29303b; }}
        QPushButton:pressed, QToolButton:pressed {{ background: #151920; }}
        QPushButton:disabled, QToolButton:disabled {{ color: {COLORS["disabled"]}; background: #191c22; }}
        QPushButton[primary="true"] {{ background: {COLORS["accent_pressed"]}; border-color: {COLORS["accent"]}; font-weight: 600; }}
        QPushButton[primary="true"]:hover {{ background: {COLORS["accent"]}; }}
        QPushButton[danger="true"] {{ color: #ffb4ba; }}
        QLineEdit, QComboBox, QSpinBox {{
            background: #15181e; border: 1px solid {COLORS["line"]}; border-radius: {radius}px;
            padding: {s6}px {s8}px;
        }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {COLORS["accent"]}; }}
        QFrame#numericStepper {{
            background: #15181e; border: 1px solid {COLORS["line"]};
            border-radius: {radius}px;
        }}
        QSpinBox#numericValue {{
            background: transparent; border: 0; border-radius: 0;
            padding: {s6}px {s10}px; min-height: {_space(28, scale)}px;
        }}
        QSpinBox#numericValue:focus {{ border: 0; }}
        QToolButton[stepButton="true"] {{
            background: {COLORS["panel_alt"]}; border: 0;
            border-left: 1px solid {COLORS["line"]}; border-radius: 0;
            padding: 0; min-width: {_space(30, scale)}px;
        }}
        QToolButton[stepButton="true"]:hover {{ background: #303744; }}
        QToolButton[stepButton="true"]:pressed {{ background: {COLORS["accent_pressed"]}; }}
        QToolButton[stepButton="true"]:disabled {{ background: #191c22; }}
        QToolButton[stepUp="true"] {{
            border-bottom: 1px solid {COLORS["line"]};
            border-top-right-radius: {radius}px;
        }}
        QToolButton[stepDown="true"] {{ border-bottom-right-radius: {radius}px; }}
        QComboBox::drop-down {{ border: 0; width: {_space(24, scale)}px; }}
        QAbstractItemView {{ background: {COLORS["panel"]}; border: 1px solid {COLORS["line"]}; outline: 0; }}
        QListWidget, QTreeWidget {{ background: transparent; border: 0; outline: 0; }}
        QListWidget#activeTaskList {{
            background: #15181e; border: 1px solid {COLORS["line"]};
            border-radius: {radius}px;
        }}
        QListWidget#activeTaskList::item {{
            color: {COLORS["text"]}; padding: {s6}px {s8}px;
            border-bottom: 1px solid {COLORS["line"]}; border-radius: 0;
        }}
        QListWidget#presetList {{
            background: #15181e; border: 1px solid {COLORS["line"]};
            border-radius: {radius}px; padding: {s6}px;
        }}
        QListWidget#presetList::item {{
            color: {COLORS["text"]}; background: {COLORS["panel_alt"]};
            border: 1px solid {COLORS["line"]}; border-radius: {radius}px;
            padding: {s8}px {s10}px;
        }}
        QListWidget#presetList::item:hover {{
            background: #29303b; border-color: {COLORS["line_strong"]};
        }}
        QListWidget#presetList::item:selected {{
            color: white; background: #263b50; border-color: {COLORS["accent"]};
        }}
        QListWidget::item {{ padding: {s4}px; border-radius: {radius}px; }}
        QListWidget::item:selected {{ background: #263b50; }}
        QHeaderView::section {{ background: {COLORS["panel_alt"]}; color: {COLORS["muted"]}; border: 0; border-bottom: 1px solid {COLORS["line"]}; padding: {s6}px; }}
        QProgressBar {{ background: #15181e; border: 1px solid {COLORS["line"]}; border-radius: {radius}px; text-align: center; min-height: {_space(16, scale)}px; }}
        QProgressBar::chunk {{ background: {COLORS["accent"]}; border-radius: {_space(4, scale)}px; }}
        QScrollArea {{ border: 0; background: transparent; }}
        QScrollBar:vertical {{ background: transparent; width: {s10}px; margin: 0; }}
        QScrollBar::handle:vertical {{ background: {COLORS["line_strong"]}; border-radius: {_space(4, scale)}px; min-height: {_space(28, scale)}px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QSplitter::handle {{ background: {COLORS["line"]}; width: 1px; }}
        QToolButton[segment="true"] {{ border-radius: 0; padding: {s8}px {s12}px; }}
        QToolButton[segment="true"]:checked {{ background: #244665; border-color: {COLORS["accent"]}; color: white; }}
        QToolButton[segmentFirst="true"] {{ border-top-left-radius: {radius}px; border-bottom-left-radius: {radius}px; }}
        QToolButton[segmentLast="true"] {{ border-top-right-radius: {radius}px; border-bottom-right-radius: {radius}px; }}
        QFrame[unitRow="true"] {{ background: {COLORS["panel"]}; border-bottom: 1px solid {COLORS["line"]}; }}
        QFrame[unavailable="true"] {{ background: #181b21; }}
        QFrame[unavailable="true"] QLabel {{ color: {COLORS["disabled"]}; }}
    """


def apply_theme(app: QApplication, scale: int = DEFAULT_SCALE) -> int:
    scale = max(MIN_SCALE, min(MAX_SCALE, int(scale)))
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(COLORS["canvas"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(COLORS["panel"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(COLORS["panel_alt"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(COLORS["panel_alt"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(COLORS["accent_pressed"]))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(COLORS["muted"]))
    app.setPalette(palette)
    font = QFont(app.font())
    font.setPointSizeF(10.0 * scale / 100.0)
    app.setFont(font)
    app.setStyleSheet(build_stylesheet(scale))
    app.setProperty("interfaceScale", scale)
    return scale


class ThemeController(QObject):
    scaleChanged = Signal(int)

    def __init__(self, app: QApplication, scale: int = DEFAULT_SCALE) -> None:
        super().__init__(app)
        self._app = app
        self._scale = apply_theme(app, scale)
        app.installEventFilter(self)

    @property
    def scale(self) -> int:
        return self._scale

    def set_scale(self, scale: int) -> None:
        scale = max(MIN_SCALE, min(MAX_SCALE, int(scale)))
        if scale == self._scale:
            return
        self._scale = apply_theme(self._app, scale)
        self.scaleChanged.emit(self._scale)

    def adjust(self, steps: int) -> None:
        self.set_scale(self._scale + (10 * steps))

    def reset(self) -> None:
        self.set_scale(DEFAULT_SCALE)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Wheel and isinstance(event, QWheelEvent):
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                steps = int(event.angleDelta().y() / 120)
                if steps:
                    self.adjust(steps)
                    event.accept()
                    return True
        return super().eventFilter(watched, event)

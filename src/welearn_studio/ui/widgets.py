"""Reusable, presentation-only controls."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSpinBox,
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class SearchField(QLineEdit):
    def __init__(self, placeholder: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self.setAccessibleName(placeholder)


class SegmentedControl(QWidget):
    valueChanged = Signal(str)

    def __init__(self, choices: tuple[tuple[str, str], ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buttons: dict[str, QToolButton] = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        for index, (value, label) in enumerate(choices):
            button = QToolButton(self)
            button.setText(label)
            button.setCheckable(True)
            button.setProperty("segment", True)
            button.setProperty("segmentFirst", index == 0)
            button.setProperty("segmentLast", index == len(choices) - 1)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._buttons[value] = button
            self._group.addButton(button)
            layout.addWidget(button)
            button.clicked.connect(lambda checked=False, key=value: self.valueChanged.emit(key))
        if choices:
            self._buttons[choices[0][0]].setChecked(True)

    def value(self) -> str:
        for value, button in self._buttons.items():
            if button.isChecked():
                return value
        return ""

    def set_value(self, value: str) -> None:
        button = self._buttons.get(value)
        if button is not None and not button.isChecked():
            button.setChecked(True)
            self.valueChanged.emit(value)


class LabeledSpinBox(QWidget):
    valueChanged = Signal(int)

    def __init__(
        self,
        label: str,
        unit: str,
        minimum: int,
        maximum: int,
        value: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.label = QLabel(label, self)
        self.spin_box = QSpinBox(self)
        self.spin_box.setRange(minimum, maximum)
        self.spin_box.setValue(value)
        self.spin_box.setSuffix("")
        self.spin_box.setPrefix("")
        self.spin_box.setAccessibleName(label)
        self.spin_box.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.spin_box.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spin_box.setObjectName("numericValue")
        self.unit_label = QLabel(unit, self)
        self.unit_label.setObjectName("muted")
        self.unit_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.up_button = QToolButton(self)
        self.up_button.setArrowType(Qt.ArrowType.UpArrow)
        self.up_button.setProperty("stepButton", True)
        self.up_button.setProperty("stepUp", True)
        self.up_button.setToolTip(f"增加{label}")
        self.up_button.setAccessibleName(f"增加{label}")
        self.down_button = QToolButton(self)
        self.down_button.setArrowType(Qt.ArrowType.DownArrow)
        self.down_button.setProperty("stepButton", True)
        self.down_button.setProperty("stepDown", True)
        self.down_button.setToolTip(f"减少{label}")
        self.down_button.setAccessibleName(f"减少{label}")
        for button in (self.up_button, self.down_button):
            button.setAutoRepeat(True)
            button.setAutoRepeatDelay(350)
            button.setAutoRepeatInterval(80)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        button_layout = QVBoxLayout()
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(0)
        button_layout.addWidget(self.up_button)
        button_layout.addWidget(self.down_button)

        self.value_frame = QFrame(self)
        self.value_frame.setObjectName("numericStepper")
        self.value_frame.setMinimumWidth(144)
        value_layout = QHBoxLayout(self.value_frame)
        value_layout.setContentsMargins(0, 0, 0, 0)
        value_layout.setSpacing(0)
        value_layout.addWidget(self.spin_box, 1)
        value_layout.addLayout(button_layout)

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(0)
        layout.setColumnMinimumWidth(0, 96)
        layout.setColumnMinimumWidth(1, 144)
        layout.setColumnMinimumWidth(2, 72)
        layout.setColumnStretch(3, 1)
        layout.addWidget(self.label, 0, 0)
        layout.addWidget(self.value_frame, 0, 1)
        layout.addWidget(self.unit_label, 0, 2)
        self.up_button.clicked.connect(self.spin_box.stepUp)
        self.down_button.clicked.connect(self.spin_box.stepDown)
        self.spin_box.valueChanged.connect(self.valueChanged)
        self.spin_box.valueChanged.connect(self._sync_step_buttons)
        self._sync_step_buttons(value)

    def value(self) -> int:
        return self.spin_box.value()

    def set_value(self, value: int) -> None:
        self.spin_box.setValue(value)

    def _sync_step_buttons(self, value: int) -> None:
        self.up_button.setEnabled(value < self.spin_box.maximum())
        self.down_button.setEnabled(value > self.spin_box.minimum())


class CurrentPageStack(QStackedWidget):
    """A stack whose vertical size follows only its visible page."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.currentChanged.connect(lambda _index: self.updateGeometry())

    def sizeHint(self):
        page = self.currentWidget()
        return page.sizeHint() if page is not None else super().sizeHint()

    def minimumSizeHint(self):
        page = self.currentWidget()
        return page.minimumSizeHint() if page is not None else super().minimumSizeHint()


class Surface(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("surface")


class SectionHeading(QWidget):
    def __init__(self, title: str, detail: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        title_label = QLabel(title, self)
        title_label.setObjectName("sectionTitle")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(title_label)
        if detail:
            detail_label = QLabel(detail, self)
            detail_label.setObjectName("muted")
            detail_label.setWordWrap(True)
            layout.addWidget(detail_label)


class StateDot(QLabel):
    def __init__(self, color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("stateDot")
        self.set_color(color)

    def set_color(self, color: str) -> None:
        self.setStyleSheet(f"background-color: {color};")


def clear_layout(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()


def set_standard_icon(button: QAbstractButton, pixmap: QStyle.StandardPixmap) -> None:
    """Use a native Qt icon for familiar commands."""
    button.setIcon(button.style().standardIcon(pixmap))

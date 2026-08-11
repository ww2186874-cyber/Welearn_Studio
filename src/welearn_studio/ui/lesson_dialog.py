"""Lesson-only selector for one course unit."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .presentation import UnitView
from .widgets import SearchField, SelectionCheckBox


class LessonSelectionDialog(QDialog):
    selectionAccepted = Signal(str, object)

    def __init__(
        self,
        unit: UnitView,
        selected_ids: set[str] | frozenset[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("lessonSelectionDialog")
        self.setWindowTitle("选择课时")
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setMinimumSize(520, 500)
        self.resize(640, 620)
        self.unit = unit
        self._checks: dict[str, SelectionCheckBox] = {}
        selected = unit.effective_lesson_ids if selected_ids is None else frozenset(selected_ids)

        title = QLabel("选择课时", self)
        title.setObjectName("pageTitle")
        unit_name = QLabel(f"{unit.number}  {unit.name}", self)
        unit_name.setObjectName("sectionTitle")
        unit_name.setWordWrap(True)
        self.search = SearchField("搜索课时", self)
        self.search.setObjectName("lessonSearch")
        self.select_all_button = QPushButton("全选", self)
        self.select_none_button = QPushButton("全不选", self)
        self.select_all_button.setProperty("toolbarButton", True)
        self.select_none_button.setProperty("toolbarButton", True)
        tools = QHBoxLayout()
        tools.setSpacing(8)
        tools.addWidget(self.search, 1)
        tools.addWidget(self.select_all_button)
        tools.addWidget(self.select_none_button)

        self.list = QListWidget(self)
        self.list.setObjectName("lessonList")
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list.setUniformItemSizes(True)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        row_height = max(40, self.list.fontMetrics().height() + 22)
        for lesson in unit.runnable_lessons:
            item = QListWidgetItem(self.list)
            item.setSizeHint(QSize(0, row_height))
            item.setData(Qt.ItemDataRole.UserRole, lesson.stable_id)
            item.setData(Qt.ItemDataRole.UserRole + 1, lesson.name)
            display_name = _display_lesson_name(unit.name, lesson.name)
            checkbox = SelectionCheckBox(display_name, self.list)
            checkbox.setProperty("lessonSelector", True)
            checkbox.setAccessibleName(lesson.name)
            checkbox.setToolTip(display_name)
            checkbox.setChecked(lesson.stable_id in selected)
            checkbox.stateChanged.connect(lambda _state: self._update_summary())
            self._checks[lesson.stable_id] = checkbox
            self.list.setItemWidget(item, checkbox)

        self.summary = QLabel(self)
        self.summary.setObjectName("muted")
        self.confirm_button = QPushButton("确定", self)
        self.confirm_button.setObjectName("confirmLessonSelectionButton")
        self.confirm_button.setProperty("primary", True)
        self.confirm_button.setProperty("actionButton", True)
        self.cancel_button = QPushButton("取消", self)
        self.cancel_button.setObjectName("cancelLessonSelectionButton")
        self.cancel_button.setProperty("actionButton", True)
        for button in (self.confirm_button, self.cancel_button):
            button.setAutoDefault(False)
            button.setMinimumWidth(88)

        footer = QFrame(self)
        footer.setObjectName("dialogFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 12, 0, 0)
        footer_layout.setSpacing(8)
        footer_layout.addWidget(self.summary)
        footer_layout.addStretch(1)
        footer_layout.addWidget(self.confirm_button)
        footer_layout.addWidget(self.cancel_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(unit_name)
        layout.addLayout(tools)
        layout.addWidget(self.list, 1)
        layout.addWidget(footer)

        self.search.textChanged.connect(self._apply_search)
        self.select_all_button.clicked.connect(lambda: self._set_visible_checked(True))
        self.select_none_button.clicked.connect(lambda: self._set_visible_checked(False))
        self.confirm_button.clicked.connect(self._accept_selection)
        self.cancel_button.clicked.connect(self.reject)
        self._update_summary()

    def selected_ids(self) -> frozenset[str]:
        return frozenset(
            lesson_id for lesson_id, checkbox in self._checks.items() if checkbox.isChecked()
        )

    def _apply_search(self, text: str) -> None:
        query = text.strip().casefold()
        for row in range(self.list.count()):
            item = self.list.item(row)
            lesson_id = str(item.data(Qt.ItemDataRole.UserRole))
            original_name = str(item.data(Qt.ItemDataRole.UserRole + 1))
            display_name = self._checks[lesson_id].text()
            haystack = f"{original_name} {display_name}".casefold()
            item.setHidden(bool(query and query not in haystack))

    def _set_visible_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self.list.count()):
            item = self.list.item(row)
            if not item.isHidden():
                lesson_id = str(item.data(Qt.ItemDataRole.UserRole))
                self._checks[lesson_id].setCheckState(state)

    def _update_summary(self, _item: QListWidgetItem | None = None) -> None:
        self.summary.setText(f"已选 {len(self.selected_ids())} / {self.list.count()} 课时")

    def _accept_selection(self) -> None:
        self.selectionAccepted.emit(self.unit.stable_id, self.selected_ids())
        self.accept()


def _display_lesson_name(unit_name: str, lesson_name: str) -> str:
    """Remove the unit prefix already shown in the dialog heading."""
    normalized_unit = unit_name.strip()
    normalized_lesson = lesson_name.strip()
    for separator in (" > ", ">"):
        prefix = f"{normalized_unit}{separator}"
        if normalized_lesson.casefold().startswith(prefix.casefold()):
            return normalized_lesson[len(prefix) :].strip()
    return normalized_lesson

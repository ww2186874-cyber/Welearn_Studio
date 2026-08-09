"""Lesson-only selector for one course unit."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .presentation import UnitView
from .widgets import SearchField


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
        self.resize(540, 560)
        self.unit = unit
        selected = unit.effective_lesson_ids if selected_ids is None else frozenset(selected_ids)

        title = QLabel(f"{unit.number}  {unit.name}", self)
        title.setObjectName("pageTitle")
        self.search = SearchField("搜索课时", self)
        self.search.setObjectName("lessonSearch")
        self.select_all_button = QPushButton("全选", self)
        self.select_none_button = QPushButton("全不选", self)
        tools = QHBoxLayout()
        tools.setSpacing(8)
        tools.addWidget(self.search, 1)
        tools.addWidget(self.select_all_button)
        tools.addWidget(self.select_none_button)

        self.list = QListWidget(self)
        self.list.setObjectName("lessonList")
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        for lesson in unit.runnable_lessons:
            item = QListWidgetItem(lesson.name, self.list)
            item.setData(Qt.ItemDataRole.UserRole, lesson.stable_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if lesson.stable_id in selected else Qt.CheckState.Unchecked
            )

        self.summary = QLabel(self)
        self.summary.setObjectName("muted")
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok,
            parent=self,
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setProperty("primary", True)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addLayout(tools)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.summary)
        layout.addWidget(self.buttons)

        self.search.textChanged.connect(self._apply_search)
        self.select_all_button.clicked.connect(lambda: self._set_visible_checked(True))
        self.select_none_button.clicked.connect(lambda: self._set_visible_checked(False))
        self.list.itemChanged.connect(self._update_summary)
        self.buttons.accepted.connect(self._accept_selection)
        self.buttons.rejected.connect(self.reject)
        self._update_summary()

    def selected_ids(self) -> frozenset[str]:
        return frozenset(
            str(self.list.item(row).data(Qt.ItemDataRole.UserRole))
            for row in range(self.list.count())
            if self.list.item(row).checkState() == Qt.CheckState.Checked
        )

    def _apply_search(self, text: str) -> None:
        query = text.strip().casefold()
        for row in range(self.list.count()):
            item = self.list.item(row)
            item.setHidden(bool(query and query not in item.text().casefold()))

    def _set_visible_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self.list.count()):
            item = self.list.item(row)
            if not item.isHidden():
                item.setCheckState(state)

    def _update_summary(self, _item: QListWidgetItem | None = None) -> None:
        self.summary.setText(f"已选 {len(self.selected_ids())}/{self.list.count()} 课时")

    def _accept_selection(self) -> None:
        self.selectionAccepted.emit(self.unit.stable_id, self.selected_ids())
        self.accept()

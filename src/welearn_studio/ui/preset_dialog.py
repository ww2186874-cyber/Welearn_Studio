"""Configuration snapshot management without duplicate parameter editors."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from .presentation import PresetView
from .widgets import set_standard_icon


class PresetDialog(QDialog):
    saveRequested = Signal(str)
    applyRequested = Signal(str)
    renameRequested = Signal(str, str)
    deleteRequested = Signal(str)

    def __init__(self, course_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("presetDialog")
        self.setWindowTitle("配置中心")
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.resize(500, 440)

        title = QLabel("配置中心", self)
        title.setObjectName("pageTitle")
        course = QLabel(course_name, self)
        course.setObjectName("muted")
        self.list = QListWidget(self)
        self.list.setObjectName("presetList")
        self.list.setSpacing(6)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.empty_label = QLabel("暂无已保存配置", self)
        self.empty_label.setObjectName("muted")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.save_button = QPushButton("保存当前", self)
        self.save_button.setObjectName("savePresetButton")
        self.save_button.setProperty("primary", True)
        self.apply_button = QPushButton("应用", self)
        self.apply_button.setObjectName("applyPresetButton")
        self.rename_button = QPushButton("重命名", self)
        self.rename_button.setObjectName("renamePresetButton")
        self.delete_button = QPushButton("删除", self)
        self.delete_button.setObjectName("deletePresetButton")
        self.delete_button.setProperty("danger", True)
        set_standard_icon(self.save_button, QStyle.StandardPixmap.SP_DialogSaveButton)
        set_standard_icon(self.apply_button, QStyle.StandardPixmap.SP_DialogApplyButton)
        set_standard_icon(self.delete_button, QStyle.StandardPixmap.SP_TrashIcon)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addWidget(self.save_button)
        actions.addStretch(1)
        actions.addWidget(self.apply_button)
        actions.addWidget(self.rename_button)
        actions.addWidget(self.delete_button)

        close_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        close_buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(course)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.empty_label)
        layout.addLayout(actions)
        layout.addWidget(close_buttons)

        self.list.currentItemChanged.connect(self._update_actions)
        self.list.itemDoubleClicked.connect(lambda _item: self._request_apply())
        self.save_button.clicked.connect(self._request_save)
        self.apply_button.clicked.connect(self._request_apply)
        self.rename_button.clicked.connect(self._request_rename)
        self.delete_button.clicked.connect(self._request_delete)
        close_buttons.rejected.connect(self.reject)
        self._update_actions()

    def set_presets(self, presets: list[PresetView], selected_id: str | None = None) -> None:
        self.list.clear()
        row_height = max(40, self.list.fontMetrics().height() + 20)
        for preset in presets:
            item = QListWidgetItem(preset.name, self.list)
            item.setSizeHint(QSize(0, row_height))
            item.setData(Qt.ItemDataRole.UserRole, preset.stable_id)
            if preset.stable_id == selected_id:
                self.list.setCurrentItem(item)
        self.empty_label.setVisible(not presets)
        self._update_actions()

    def selected_id(self) -> str | None:
        item = self.list.currentItem()
        return None if item is None else str(item.data(Qt.ItemDataRole.UserRole))

    def _update_actions(
        self, _current: QListWidgetItem | None = None, _previous: QListWidgetItem | None = None
    ) -> None:
        enabled = self.list.currentItem() is not None
        self.apply_button.setEnabled(enabled)
        self.rename_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)

    def _request_save(self) -> None:
        name, accepted = QInputDialog.getText(self, "保存配置", "名称")
        if accepted and name.strip():
            self.saveRequested.emit(name.strip())

    def _request_apply(self) -> None:
        preset_id = self.selected_id()
        if preset_id is not None:
            self.applyRequested.emit(preset_id)

    def _request_rename(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        name, accepted = QInputDialog.getText(self, "重命名配置", "名称", text=item.text())
        if accepted and name.strip():
            self.renameRequested.emit(str(item.data(Qt.ItemDataRole.UserRole)), name.strip())

    def _request_delete(self) -> None:
        preset_id = self.selected_id()
        if preset_id is not None:
            self.deleteRequested.emit(preset_id)

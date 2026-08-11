"""Configuration snapshot management without duplicate parameter editors."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedLayout,
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

        name_label = QLabel("预设名称", self)
        name_label.setObjectName("fieldLabel")
        self.name_input = QLineEdit(self)
        self.name_input.setObjectName("presetName")
        self.name_input.setAccessibleName("预设名称")
        self.name_input.setClearButtonEnabled(True)
        self.save_button = QPushButton("保存当前", self)
        self.save_button.setObjectName("savePresetButton")
        self.save_button.setProperty("primary", True)
        self.save_button.setEnabled(False)
        set_standard_icon(self.save_button, QStyle.StandardPixmap.SP_DialogSaveButton)
        save_row = QHBoxLayout()
        save_row.setSpacing(8)
        save_row.addWidget(name_label)
        save_row.addWidget(self.name_input, 1)
        save_row.addWidget(self.save_button)

        self.list = QListWidget(self)
        self.list.setObjectName("presetList")
        self.list.setSpacing(6)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.empty_label = QLabel("暂无配置", self)
        self.empty_label.setObjectName("muted")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        saved_label = QLabel("已保存配置", self)
        saved_label.setObjectName("sectionTitle")
        self.preset_count = QLabel("0 项", self)
        self.preset_count.setObjectName("muted")
        saved_row = QHBoxLayout()
        saved_row.setSpacing(8)
        saved_row.addWidget(saved_label)
        saved_row.addStretch(1)
        saved_row.addWidget(self.preset_count)

        content = QFrame(self)
        content.setObjectName("presetContent")
        self.content_stack = QStackedLayout(content)
        self.content_stack.setContentsMargins(0, 0, 0, 0)
        self.content_stack.addWidget(self.list)
        self.content_stack.addWidget(self.empty_label)

        self.apply_button = QPushButton("应用", self)
        self.apply_button.setObjectName("applyPresetButton")
        self.apply_button.setProperty("actionButton", True)
        self.rename_button = QPushButton("重命名", self)
        self.rename_button.setObjectName("renamePresetButton")
        self.rename_button.setProperty("actionButton", True)
        self.delete_button = QPushButton("删除", self)
        self.delete_button.setObjectName("deletePresetButton")
        self.delete_button.setProperty("danger", True)
        self.delete_button.setProperty("actionButton", True)
        set_standard_icon(self.apply_button, QStyle.StandardPixmap.SP_DialogApplyButton)
        set_standard_icon(self.delete_button, QStyle.StandardPixmap.SP_TrashIcon)
        self.close_button = QPushButton("关闭", self)
        self.close_button.setObjectName("closePresetButton")
        self.close_button.setProperty("danger", True)
        self.close_button.setProperty("actionButton", True)
        self.close_button.setMinimumWidth(104)
        for button in (
            self.save_button,
            self.apply_button,
            self.rename_button,
            self.delete_button,
            self.close_button,
        ):
            button.setAutoDefault(False)
        footer = QFrame(self)
        footer.setObjectName("dialogFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 12, 0, 0)
        footer_layout.setSpacing(8)
        footer_layout.addWidget(self.apply_button)
        footer_layout.addWidget(self.rename_button)
        footer_layout.addWidget(self.delete_button)
        footer_layout.addStretch(1)
        footer_layout.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(course)
        layout.addLayout(save_row)
        layout.addLayout(saved_row)
        layout.addWidget(content, 1)
        layout.addWidget(footer)

        self.list.currentItemChanged.connect(self._update_actions)
        self.list.itemDoubleClicked.connect(lambda _item: self._request_apply())
        self.name_input.textChanged.connect(self._update_save_action)
        self.name_input.returnPressed.connect(self._request_save)
        self.save_button.clicked.connect(self._request_save)
        self.apply_button.clicked.connect(self._request_apply)
        self.rename_button.clicked.connect(self._request_rename)
        self.delete_button.clicked.connect(self._request_delete)
        self.close_button.clicked.connect(self.reject)
        self._update_actions()

    def set_presets(self, presets: list[PresetView], selected_id: str | None = None) -> None:
        self.list.clear()
        self.preset_count.setText(f"{len(presets)} 项")
        row_height = max(40, self.list.fontMetrics().height() + 20)
        for preset in presets:
            item = QListWidgetItem(preset.name, self.list)
            item.setSizeHint(QSize(0, row_height))
            item.setData(Qt.ItemDataRole.UserRole, preset.stable_id)
            if preset.stable_id == selected_id:
                self.list.setCurrentItem(item)
        self.content_stack.setCurrentWidget(self.list if presets else self.empty_label)
        if presets and self.list.currentItem() is None:
            self.list.setCurrentRow(0)
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

    def _update_save_action(self, text: str) -> None:
        self.save_button.setEnabled(bool(text.strip()))

    def _request_save(self) -> None:
        name = self.name_input.text().strip()
        if name:
            self.saveRequested.emit(name)

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

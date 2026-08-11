"""Coordinated three-column desktop window."""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QWheelEvent
from PySide6.QtWidgets import QApplication, QMainWindow, QSplitter, QWidget

from .account_sidebar import AccountSidebar
from .course_workspace import CourseWorkspace
from .lesson_dialog import LessonSelectionDialog
from .presentation import AccountView, CourseView, LogEntry, PresetView, RuntimeView, UnitView
from .preset_dialog import PresetDialog
from .runtime_overview import RuntimeOverview
from .theme import DEFAULT_SCALE, ThemeController


class MainWindow(QMainWindow):
    """Presentation shell; integrations subscribe to signals and provide view data."""

    accountSelected = Signal(str)
    accountAddRequested = Signal()
    accountsImportRequested = Signal()
    accountRemoveRequested = Signal(str)
    coursesRefreshRequested = Signal()
    courseSelected = Signal(str)
    lessonSelectionChanged = Signal(str, object)
    startRequested = Signal(object)
    stopRequested = Signal()
    configurationSaveRequested = Signal(str, str, object)
    configurationApplyRequested = Signal(str, str)
    configurationRenameRequested = Signal(str, str, str)
    configurationDeleteRequested = Signal(str, str)
    restoreDefaultsRequested = Signal(str, str)
    clearLogRequested = Signal()
    interfaceScaleChanged = Signal(int)
    workspaceChanged = Signal(str, object)

    def __init__(
        self,
        settings: QSettings | None = None,
        parent: QWidget | None = None,
        *,
        initial_scale: int = DEFAULT_SCALE,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("mainWindow")
        self.setWindowTitle("WeLearn Studio")
        self.setMinimumSize(900, 640)
        self.resize(1380, 860)
        self._settings = settings or QSettings("WeLearn Studio", "Interface")
        self._current_account_id = ""
        self._context_account_id = ""
        self._context_course_id: str | None = None
        self._presets: list[PresetView] = []
        self._preset_dialog: PresetDialog | None = None
        self._lesson_dialog: LessonSelectionDialog | None = None

        app = QApplication.instance()
        if app is None:
            raise RuntimeError("QApplication must exist before MainWindow")
        stored_scale = initial_scale
        self.theme = ThemeController(app, stored_scale)
        self.theme.scaleChanged.connect(self._scale_changed)

        self.accounts = AccountSidebar(self)
        self.workspace = CourseWorkspace(self)
        self.runtime = RuntimeOverview(self)
        self._apply_scaled_panel_widths(stored_scale)
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setObjectName("mainColumns")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(1)
        self.splitter.addWidget(self.accounts)
        self.splitter.addWidget(self.workspace)
        self.splitter.addWidget(self.runtime)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.setCentralWidget(self.splitter)

        right_width = max(310, min(520, self._settings.value("ui/runtimeWidth", 370, type=int)))
        self._right_width = right_width
        QTimer.singleShot(0, lambda: self.splitter.setSizes((260, 750, self._right_width)))
        QTimer.singleShot(0, self._fit_scaled_layout)

        reset_scale = QAction(self)
        reset_scale.setShortcut(QKeySequence("Ctrl+0"))
        reset_scale.triggered.connect(self.theme.reset)
        self.addAction(reset_scale)

        self.accounts.accountSelected.connect(self._account_selected)
        self.accounts.addRequested.connect(self.accountAddRequested)
        self.accounts.importRequested.connect(self.accountsImportRequested)
        self.accounts.removeRequested.connect(self.accountRemoveRequested)
        self.workspace.courseChanged.connect(self.courseSelected)
        self.workspace.coursesRefreshRequested.connect(self.coursesRefreshRequested)
        self.workspace.pageChanged.connect(self._workspace_changed)
        self.workspace.lessonSelectionRequested.connect(self._open_lesson_dialog)
        self.workspace.startRequested.connect(self.startRequested)
        self.workspace.stopRequested.connect(self.stopRequested)
        self.workspace.presetsRequested.connect(self._open_preset_dialog)
        self.workspace.restoreDefaultsRequested.connect(self._request_restore_defaults)
        self.workspace.runtimeToggleRequested.connect(self.toggle_runtime)
        self.runtime.clearLogRequested.connect(self.clearLogRequested)
        self.splitter.splitterMoved.connect(self._splitter_moved)

    @property
    def interface_scale(self) -> int:
        return self.theme.scale

    def set_accounts(self, accounts: list[AccountView], selected_id: str | None = None) -> None:
        self.accounts.set_accounts(accounts, selected_id)

    def set_account_context(
        self,
        account: AccountView,
        courses: list[CourseView],
        selected_course_id: str | None = None,
    ) -> None:
        effective_course = selected_course_id or (courses[0].stable_id if courses else None)
        if (
            account.stable_id != self._context_account_id
            or effective_course != self._context_course_id
        ):
            self.set_course_units([])
            self.set_presets([])
            self.runtime.set_active_tasks([])
        self._context_account_id = account.stable_id
        self._context_course_id = effective_course
        self._current_account_id = account.stable_id
        self.workspace.set_account_name(account.display_name)
        self.workspace.set_courses(courses, selected_course_id)

    def set_course_units(
        self,
        units: list[UnitView],
        selected_unit_ids: set[str] | frozenset[str] | None = None,
    ) -> None:
        self.workspace.set_units(units, selected_unit_ids)

    def set_presets(self, presets: list[PresetView], selected_id: str | None = None) -> None:
        self._presets = list(presets)
        if self._preset_dialog is not None:
            self._preset_dialog.set_presets(self._presets, selected_id)

    def set_runtime(self, runtime: RuntimeView) -> None:
        self.runtime.set_runtime(runtime)
        self.workspace.set_progress(
            runtime.completed,
            runtime.planned,
            runtime.state in {"homework", "time_study"},
            runtime.remaining_seconds if runtime.estimated_seconds > 0 else None,
        )

    def set_active_tasks(self, tasks: list[str], concurrency: int = 0) -> None:
        self.runtime.set_active_tasks(tasks, concurrency)

    def set_log_entries(self, entries: list[LogEntry]) -> None:
        self.runtime.set_entries(entries)

    def append_log_entry(self, entry: LogEntry) -> None:
        self.runtime.append_entry(entry)

    def toggle_runtime(self) -> None:
        visible = self.runtime.isVisible()
        if visible:
            self._right_width = max(self.runtime.width(), self.runtime.minimumWidth())
            self.runtime.hide()
            self.workspace.runtime_toggle.setChecked(False)
        else:
            self.runtime.show()
            self.workspace.runtime_toggle.setChecked(True)
            QTimer.singleShot(0, self._restore_right_width)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            steps = int(event.angleDelta().y() / 120)
            if steps:
                self.theme.adjust(steps)
                event.accept()
                return
        super().wheelEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.runtime.isVisible():
            self._settings.setValue("ui/runtimeWidth", self.runtime.width())
        self._settings.sync()
        super().closeEvent(event)

    def _account_selected(self, account_id: str) -> None:
        self._current_account_id = account_id
        if self.runtime.isVisible():
            self._right_width = self.splitter.sizes()[2]
        self.accountSelected.emit(account_id)
        QTimer.singleShot(0, self._restore_right_width)

    def _restore_right_width(self) -> None:
        if not self.runtime.isVisible():
            return
        sizes = self.splitter.sizes()
        if len(sizes) != 3:
            return
        right = max(
            self.runtime.minimumWidth(), min(self.runtime.maximumWidth(), self._right_width)
        )
        center = max(self.workspace.minimumSizeHint().width(), sizes[1] + sizes[2] - right)
        self.splitter.setSizes((sizes[0], center, right))

    def _splitter_moved(self, _position: int, _index: int) -> None:
        if self.runtime.isVisible():
            sizes = self.splitter.sizes()
            if len(sizes) == 3 and sizes[2] > 0:
                self._right_width = sizes[2]

    def _scale_changed(self, scale: int) -> None:
        self._apply_scaled_panel_widths(scale)
        self.interfaceScaleChanged.emit(scale)
        QTimer.singleShot(0, self._fit_scaled_layout)

    def _apply_scaled_panel_widths(self, scale: int) -> None:
        self.accounts.setMinimumWidth(round(220 * scale / 100))
        self.accounts.setMaximumWidth(round(310 * scale / 100))
        self.runtime.setMinimumWidth(round(310 * scale / 100))
        self.runtime.setMaximumWidth(round(520 * scale / 100))

    def _fit_scaled_layout(self) -> None:
        central = self.centralWidget()
        screen = self.screen()
        if central is None or screen is None:
            return
        hint = central.sizeHint()
        available = screen.availableGeometry()
        target_width = min(available.width(), max(self.width(), hint.width()))
        target_height = min(available.height(), max(self.height(), hint.height()))
        if target_width != self.width() or target_height != self.height():
            self.resize(target_width, target_height)

    def _open_lesson_dialog(self, unit_id: str) -> None:
        unit = self.workspace.unit(unit_id)
        if unit is None:
            return
        dialog = LessonSelectionDialog(unit, self.workspace.selected_lesson_ids(unit_id), self)
        dialog.selectionAccepted.connect(self._lesson_selection_accepted)
        dialog.finished.connect(lambda _result: self._clear_lesson_dialog(dialog))
        self._lesson_dialog = dialog
        dialog.open()

    def _lesson_selection_accepted(self, unit_id: str, selected_ids: object) -> None:
        normalized = frozenset(str(value) for value in selected_ids)
        self.workspace.set_lesson_selection(unit_id, normalized)
        self.lessonSelectionChanged.emit(unit_id, normalized)

    def _clear_lesson_dialog(self, dialog: LessonSelectionDialog) -> None:
        if self._lesson_dialog is dialog:
            self._lesson_dialog = None

    def _open_preset_dialog(self) -> None:
        dialog = PresetDialog(self.workspace.course_combo.currentText(), self)
        dialog.set_presets(self._presets)
        dialog.saveRequested.connect(self._request_save_configuration)
        dialog.applyRequested.connect(self._request_apply_configuration)
        dialog.renameRequested.connect(self._request_rename_configuration)
        dialog.deleteRequested.connect(self._request_delete_configuration)
        dialog.finished.connect(lambda _result: self._clear_preset_dialog(dialog))
        self._preset_dialog = dialog
        dialog.open()

    def _clear_preset_dialog(self, dialog: PresetDialog) -> None:
        if self._preset_dialog is dialog:
            self._preset_dialog = None

    def _request_save_configuration(self, name: str) -> None:
        course_id = self.workspace.selected_course_id()
        if course_id is not None:
            self.configurationSaveRequested.emit(course_id, name, self.workspace.snapshot())

    def _request_apply_configuration(self, preset_id: str) -> None:
        course_id = self.workspace.selected_course_id()
        if course_id is not None:
            self.configurationApplyRequested.emit(course_id, preset_id)

    def _request_rename_configuration(self, preset_id: str, name: str) -> None:
        course_id = self.workspace.selected_course_id()
        if course_id is not None:
            self.configurationRenameRequested.emit(course_id, preset_id, name)

    def _request_delete_configuration(self, preset_id: str) -> None:
        course_id = self.workspace.selected_course_id()
        if course_id is not None:
            self.configurationDeleteRequested.emit(course_id, preset_id)

    def _request_restore_defaults(self) -> None:
        course_id = self.workspace.selected_course_id()
        if self._current_account_id and course_id is not None:
            self.restoreDefaultsRequested.emit(self._current_account_id, course_id)

    def _workspace_changed(self) -> None:
        course_id = self.workspace.selected_course_id()
        if course_id is not None:
            self.workspaceChanged.emit(course_id, self.workspace.snapshot())

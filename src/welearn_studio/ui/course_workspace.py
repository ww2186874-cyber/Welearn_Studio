"""Course page with shared unit selection and mode-specific parameters."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from .presentation import CoursePageSnapshot, CourseView, UnitView, format_duration
from .widgets import (
    CurrentPageStack,
    LabeledSpinBox,
    SearchField,
    SectionHeading,
    SegmentedControl,
    SelectionCheckBox,
    Surface,
    set_standard_icon,
)

FILTERS = (
    ("all", "全部单元"),
    ("selected", "已选"),
    ("unselected", "未选"),
    ("unavailable", "不可用"),
    ("failed", "加载失败"),
)


class UnitRow(QFrame):
    selectionChanged = Signal(str, bool)
    lessonsRequested = Signal(str)

    def __init__(self, unit: UnitView, selected: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.unit = unit
        self.setProperty("unitRow", True)
        self.setProperty("unavailable", unit.runnable_count == 0 or unit.load_failed)
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if unit.runnable_count else Qt.CursorShape.ArrowCursor
        )
        self.checkbox = SelectionCheckBox(parent=self)
        self.checkbox.setObjectName(f"unitCheck_{unit.stable_id}")
        self.checkbox.setChecked(selected and unit.runnable_count > 0 and not unit.load_failed)
        self.checkbox.setEnabled(unit.runnable_count > 0 and not unit.load_failed)

        number = QLabel(unit.number, self)
        number.setFixedWidth(58)
        name = QLabel(unit.name, self)
        name.setObjectName("sectionTitle")
        name.setWordWrap(True)
        status_text = "加载失败" if unit.load_failed else f"{unit.runnable_count} 个可执行课时"
        status = QLabel(status_text, self)
        status.setObjectName("muted")
        selected_count = len(
            unit.effective_lesson_ids.intersection(
                {item.stable_id for item in unit.runnable_lessons}
            )
        )
        self.summary = QLabel(f"已选 {selected_count}/{unit.runnable_count} 课时", self)
        self.summary.setObjectName("muted")
        self.lesson_button = QPushButton("课时", self)
        self.lesson_button.setObjectName(f"lessonButton_{unit.stable_id}")
        self.lesson_button.setEnabled(unit.runnable_count > 0 and not unit.load_failed)
        set_standard_icon(self.lesson_button, QStyle.StandardPixmap.SP_FileDialogListView)

        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(name)
        text.addWidget(status)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)
        layout.addWidget(self.checkbox)
        layout.addWidget(number)
        layout.addLayout(text, 1)
        layout.addWidget(self.summary)
        layout.addWidget(self.lesson_button)

        self.checkbox.toggled.connect(
            lambda checked: self.selectionChanged.emit(self.unit.stable_id, checked)
        )
        self.lesson_button.clicked.connect(lambda: self.lessonsRequested.emit(self.unit.stable_id))

    def set_selected(self, selected: bool) -> None:
        self.checkbox.setChecked(selected and self.checkbox.isEnabled())

    def set_lesson_selection(self, selected_ids: frozenset[str]) -> None:
        valid = {lesson.stable_id for lesson in self.unit.runnable_lessons}
        self.summary.setText(f"已选 {len(valid.intersection(selected_ids))}/{len(valid)} 课时")

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.checkbox.isEnabled():
            child = self.childAt(event.position().toPoint())
            if child not in {self.checkbox, self.lesson_button}:
                self.checkbox.toggle()
                event.accept()
                return
        super().mouseReleaseEvent(event)


class CourseWorkspace(QWidget):
    courseChanged = Signal(str)
    coursesRefreshRequested = Signal()
    pageChanged = Signal()
    lessonSelectionRequested = Signal(str)
    startRequested = Signal(object)
    stopRequested = Signal()
    presetsRequested = Signal()
    restoreDefaultsRequested = Signal()
    runtimeToggleRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("courseWorkspace")
        self._units: dict[str, UnitView] = {}
        self._unit_rows: dict[str, UnitRow] = {}
        self._selected_unit_ids: set[str] = set()
        self._selected_lessons: dict[str, frozenset[str]] = {}

        self.account_title = QLabel("请选择账号", self)
        self.account_title.setObjectName("pageTitle")
        self.course_combo = QComboBox(self)
        self.course_combo.setObjectName("courseCombo")
        self.course_combo.setMinimumWidth(220)
        self.preset_button = QPushButton("配置", self)
        self.preset_button.setObjectName("presetButton")
        self.preset_button.setProperty("toolbarButton", True)
        self.defaults_button = QPushButton("恢复默认", self)
        self.defaults_button.setObjectName("restoreDefaultsButton")
        self.defaults_button.setProperty("toolbarButton", True)
        self.runtime_toggle = QPushButton("运行状态", self)
        self.runtime_toggle.setObjectName("runtimeToggleButton")
        self.runtime_toggle.setProperty("toolbarButton", True)
        self.runtime_toggle.setCheckable(True)
        self.runtime_toggle.setChecked(True)
        set_standard_icon(self.runtime_toggle, QStyle.StandardPixmap.SP_FileDialogDetailedView)
        set_standard_icon(self.defaults_button, QStyle.StandardPixmap.SP_BrowserReload)
        set_standard_icon(self.preset_button, QStyle.StandardPixmap.SP_FileDialogContentsView)
        self.refresh_courses_button = QPushButton("刷新", self)
        self.refresh_courses_button.setObjectName("refreshCoursesButton")
        self.refresh_courses_button.setProperty("toolbarButton", True)
        self.refresh_courses_button.setToolTip("刷新课程")
        set_standard_icon(self.refresh_courses_button, QStyle.StandardPixmap.SP_BrowserReload)

        header_top = QHBoxLayout()
        header_top.setSpacing(8)
        header_top.addWidget(self.account_title)
        header_top.addStretch(1)
        header_top.addWidget(self.runtime_toggle)
        header_top.addWidget(self.defaults_button)
        header_top.addWidget(self.preset_button)
        header_course = QHBoxLayout()
        course_label = QLabel("课程", self)
        course_label.setObjectName("fieldLabel")
        header_course.addWidget(course_label)
        header_course.addWidget(self.course_combo, 1)
        header_course.addWidget(self.refresh_courses_button)
        header = QVBoxLayout()
        header.setSpacing(10)
        header.addLayout(header_top)
        header.addLayout(header_course)

        self.mode = SegmentedControl((("homework", "作业"), ("time_study", "时长学习")), self)
        self.mode.setObjectName("taskMode")

        self.unit_search = SearchField("搜索单元编号或名称", self)
        self.unit_search.setObjectName("unitSearch")
        self.unit_filter = QComboBox(self)
        self.unit_filter.setObjectName("unitFilter")
        for value, label in FILTERS:
            self.unit_filter.addItem(label, value)
        self.select_all_button = QPushButton("全选", self)
        self.select_all_button.setObjectName("selectAllUnitsButton")
        self.select_none_button = QPushButton("全不选", self)
        self.select_none_button.setObjectName("selectNoUnitsButton")
        unit_tools = QHBoxLayout()
        unit_tools.setSpacing(8)
        unit_tools.addWidget(self.unit_search, 1)
        unit_tools.addWidget(self.unit_filter)
        unit_tools.addWidget(self.select_all_button)
        unit_tools.addWidget(self.select_none_button)

        self.units_container = QWidget(self)
        self.units_container.setObjectName("unitsContainer")
        self.units_layout = QVBoxLayout(self.units_container)
        self.units_layout.setContentsMargins(0, 0, 0, 0)
        self.units_layout.setSpacing(0)
        self.units_layout.addStretch(1)
        self.units_scroll = QScrollArea(self)
        self.units_scroll.setObjectName("unitsScroll")
        self.units_scroll.setWidgetResizable(True)
        self.units_scroll.setWidget(self.units_container)
        self.units_scroll.setMinimumHeight(210)
        self.empty_units = QLabel("暂无单元", self.units_container)
        self.empty_units.setObjectName("muted")
        self.empty_units.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.units_layout.insertWidget(0, self.empty_units)

        units_surface = Surface(self)
        units_surface.setObjectName("surface")
        units_surface.setProperty("workspaceSection", True)
        units_block = QVBoxLayout(units_surface)
        units_block.setContentsMargins(0, 4, 0, 0)
        units_block.setSpacing(10)
        self.unit_count = QLabel("已选 0 / 0", self)
        self.unit_count.setObjectName("muted")
        unit_heading = QHBoxLayout()
        unit_heading.setSpacing(8)
        unit_heading.addWidget(SectionHeading("单元"))
        unit_heading.addStretch(1)
        unit_heading.addWidget(self.unit_count)
        units_block.addLayout(unit_heading)
        units_block.addLayout(unit_tools)
        units_block.addWidget(self.units_scroll, 1)

        self.homework_parameters = Surface(self)
        self.homework_parameters.setProperty("parameterSection", True)
        homework_layout = QVBoxLayout(self.homework_parameters)
        homework_layout.setContentsMargins(0, 12, 0, 4)
        homework_layout.setSpacing(10)
        homework_layout.addWidget(SectionHeading("作业参数"))
        self.accuracy = LabeledSpinBox("正确率", "%", 0, 100, 100, self.homework_parameters)
        self.accuracy.setObjectName("accuracyControl")
        homework_layout.addWidget(self.accuracy)

        self.time_parameters = Surface(self)
        self.time_parameters.setProperty("parameterSection", True)
        time_layout = QVBoxLayout(self.time_parameters)
        time_layout.setContentsMargins(0, 12, 0, 4)
        time_layout.setSpacing(10)
        time_layout.addWidget(SectionHeading("时长参数"))
        self.total_hours = LabeledSpinBox("总时长", "小时", 1, 72, 1, self.time_parameters)
        self.total_hours.setObjectName("totalHoursControl")
        self.random_minutes = LabeledSpinBox("随机浮动", "分钟", 0, 30, 5, self.time_parameters)
        self.random_minutes.setObjectName("randomMinutesControl")
        self.concurrency = LabeledSpinBox("并发数", "个并发", 1, 100, 5, self.time_parameters)
        self.concurrency.setObjectName("concurrencyControl")
        time_layout.addWidget(self.total_hours)
        time_layout.addWidget(self.random_minutes)
        time_layout.addWidget(self.concurrency)

        self.parameter_stack = CurrentPageStack(self)
        self.parameter_stack.setObjectName("parameterStack")
        self.parameter_stack.addWidget(self.homework_parameters)
        self.parameter_stack.addWidget(self.time_parameters)

        self.progress_label = QLabel("就绪", self)
        self.progress_label.setObjectName("progressLabel")
        self.countdown_label = QLabel("剩余 --:--:--", self)
        self.countdown_label.setObjectName("countdownLabel")
        self.countdown_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.progress = QProgressBar(self)
        self.progress.setObjectName("taskProgress")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        progress_row = QHBoxLayout()
        progress_row.setSpacing(10)
        progress_row.addWidget(self.progress_label)
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.countdown_label)

        self.start_button = QPushButton("开始", self)
        self.start_button.setObjectName("startButton")
        self.start_button.setProperty("primary", True)
        self.start_button.setProperty("actionButton", True)
        self.stop_button = QPushButton("停止", self)
        self.stop_button.setObjectName("stopButton")
        self.stop_button.setProperty("danger", True)
        self.stop_button.setProperty("actionButton", True)
        self.stop_button.setEnabled(False)
        self.start_button.setMinimumWidth(112)
        self.stop_button.setMinimumWidth(112)
        set_standard_icon(self.start_button, QStyle.StandardPixmap.SP_MediaPlay)
        set_standard_icon(self.stop_button, QStyle.StandardPixmap.SP_MediaStop)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addStretch(1)
        actions.addWidget(self.start_button)
        actions.addWidget(self.stop_button)

        action_bar = QFrame(self)
        action_bar.setObjectName("actionBar")
        action_layout = QVBoxLayout(action_bar)
        action_layout.setContentsMargins(0, 12, 0, 0)
        action_layout.setSpacing(10)
        action_layout.addLayout(progress_row)
        action_layout.addLayout(actions)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addLayout(header)
        layout.addWidget(self.mode)
        layout.addWidget(units_surface, 1)
        layout.addWidget(self.parameter_stack)
        layout.addWidget(action_bar)

        self.course_combo.currentIndexChanged.connect(self._course_changed)
        self.refresh_courses_button.clicked.connect(self.coursesRefreshRequested)
        self.mode.valueChanged.connect(self._mode_changed)
        self.unit_search.textChanged.connect(self._apply_unit_filter)
        self.unit_filter.currentIndexChanged.connect(self._apply_unit_filter)
        self.select_all_button.clicked.connect(lambda: self._bulk_select(True))
        self.select_none_button.clicked.connect(lambda: self._bulk_select(False))
        self.start_button.clicked.connect(lambda: self.startRequested.emit(self.snapshot()))
        self.stop_button.clicked.connect(self.stopRequested)
        self.preset_button.clicked.connect(self.presetsRequested)
        self.defaults_button.clicked.connect(self.restoreDefaultsRequested)
        self.runtime_toggle.clicked.connect(self.runtimeToggleRequested)
        self.accuracy.valueChanged.connect(self.pageChanged)
        self.total_hours.valueChanged.connect(self.pageChanged)
        self.random_minutes.valueChanged.connect(self.pageChanged)
        self.concurrency.valueChanged.connect(self.pageChanged)

    def set_account_name(self, name: str) -> None:
        self.account_title.setText(name or "请选择账号")

    def set_courses(self, courses: list[CourseView], selected_id: str | None = None) -> None:
        self.course_combo.blockSignals(True)
        self.course_combo.clear()
        for course in courses:
            self.course_combo.addItem(course.name, course.stable_id)
        if selected_id:
            index = self.course_combo.findData(selected_id)
            if index >= 0:
                self.course_combo.setCurrentIndex(index)
        self.course_combo.blockSignals(False)

    def selected_course_id(self) -> str | None:
        value = self.course_combo.currentData()
        return None if value is None else str(value)

    def set_units(
        self, units: list[UnitView], selected_unit_ids: set[str] | frozenset[str] | None = None
    ) -> None:
        if selected_unit_ids is not None:
            self._selected_unit_ids = set(selected_unit_ids)
        self._units = {unit.stable_id: unit for unit in units}
        self._selected_unit_ids.intersection_update(self._units)
        self._selected_lessons = {unit.stable_id: unit.effective_lesson_ids for unit in units}
        for row in self._unit_rows.values():
            row.deleteLater()
        self._unit_rows.clear()
        while self.units_layout.count() > 1:
            item = self.units_layout.takeAt(1)
            if item.widget() is not None:
                item.widget().deleteLater()
        for unit in units:
            if unit.runnable_count == 0 or unit.load_failed:
                self._selected_unit_ids.discard(unit.stable_id)
            row = UnitRow(unit, unit.stable_id in self._selected_unit_ids, self.units_container)
            row.selectionChanged.connect(self._unit_selection_changed)
            row.lessonsRequested.connect(self.lessonSelectionRequested)
            self._unit_rows[unit.stable_id] = row
            self.units_layout.addWidget(row)
        self.units_layout.addStretch(1)
        self._apply_unit_filter()

    def selected_unit_ids(self) -> frozenset[str]:
        return frozenset(self._selected_unit_ids)

    def selected_lesson_ids(self, unit_id: str) -> frozenset[str]:
        return self._selected_lessons.get(unit_id, frozenset())

    def set_lesson_selection(self, unit_id: str, selected_ids: set[str] | frozenset[str]) -> None:
        unit = self._units.get(unit_id)
        if unit is None:
            return
        runnable_ids = {lesson.stable_id for lesson in unit.runnable_lessons}
        normalized = frozenset(runnable_ids.intersection(selected_ids))
        self._selected_lessons[unit_id] = normalized
        row = self._unit_rows.get(unit_id)
        if row is not None:
            row.set_lesson_selection(normalized)
        self.pageChanged.emit()

    def unit(self, unit_id: str) -> UnitView | None:
        return self._units.get(unit_id)

    def snapshot(self) -> CoursePageSnapshot:
        return CoursePageSnapshot(
            mode=self.mode.value(),
            selected_unit_ids=self.selected_unit_ids(),
            selected_lessons=dict(self._selected_lessons),
            accuracy=self.accuracy.value(),
            total_hours=self.total_hours.value(),
            random_minutes=self.random_minutes.value(),
            concurrency=self.concurrency.value(),
        )

    def apply_snapshot(self, snapshot: CoursePageSnapshot) -> None:
        self.mode.set_value(snapshot.mode)
        available_unit_ids = {
            unit_id
            for unit_id, unit in self._units.items()
            if unit.runnable_count > 0 and not unit.load_failed
        }
        self._selected_unit_ids = set(snapshot.selected_unit_ids).intersection(available_unit_ids)
        for unit_id, row in self._unit_rows.items():
            row.set_selected(unit_id in self._selected_unit_ids)
        for unit_id, selected_ids in snapshot.selected_lessons.items():
            self.set_lesson_selection(unit_id, selected_ids)
        self.accuracy.set_value(snapshot.accuracy)
        self.total_hours.set_value(snapshot.total_hours)
        self.random_minutes.set_value(snapshot.random_minutes)
        self.concurrency.set_value(snapshot.concurrency)
        self._apply_unit_filter()

    def set_progress(
        self,
        completed: int,
        planned: int,
        running: bool = True,
        remaining_seconds: int | None = None,
    ) -> None:
        percentage = 0 if planned <= 0 else round(100 * max(0, min(completed, planned)) / planned)
        self.progress.setValue(percentage)
        self.progress_label.setText(f"已处理 {completed}/{planned}" if planned else "就绪")
        if planned <= 0 or remaining_seconds is None:
            self.countdown_label.setText("剩余 --:--:--")
        else:
            self.countdown_label.setText(f"剩余 {format_duration(remaining_seconds)}")
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)

    def _course_changed(self, index: int) -> None:
        value = self.course_combo.itemData(index)
        if value is not None:
            self.courseChanged.emit(str(value))

    def _mode_changed(self, mode: str) -> None:
        self.parameter_stack.setCurrentIndex(0 if mode == "homework" else 1)
        self.pageChanged.emit()

    def _unit_selection_changed(self, unit_id: str, selected: bool) -> None:
        if selected:
            self._selected_unit_ids.add(unit_id)
        else:
            self._selected_unit_ids.discard(unit_id)
        self._apply_unit_filter()
        self.pageChanged.emit()

    def _matches_filter(self, unit: UnitView) -> bool:
        query = self.unit_search.text().strip().casefold()
        if query and query not in unit.number.casefold() and query not in unit.name.casefold():
            return False
        filter_value = self.unit_filter.currentData() or "all"
        selected = unit.stable_id in self._selected_unit_ids
        unavailable = unit.runnable_count == 0 or unit.load_failed
        return {
            "all": True,
            "selected": selected,
            "unselected": not selected and not unavailable,
            "unavailable": unavailable,
            "failed": unit.load_failed,
        }.get(str(filter_value), True)

    def _apply_unit_filter(self, _value: object = None) -> None:
        visible = 0
        for unit_id, row in self._unit_rows.items():
            matched = self._matches_filter(self._units[unit_id])
            row.setVisible(matched)
            visible += int(matched)
        self.empty_units.setText("未找到单元" if self._units else "暂无单元")
        self.empty_units.setVisible(visible == 0)
        available = sum(
            unit.runnable_count > 0 and not unit.load_failed for unit in self._units.values()
        )
        selected = len(self._selected_unit_ids.intersection(self._units))
        self.unit_count.setText(f"已选 {selected} / {available}")
        self.units_layout.setStretch(0, 1 if visible == 0 else 0)
        if self.units_layout.count() > 1:
            self.units_layout.setStretch(self.units_layout.count() - 1, 0 if visible == 0 else 1)

    def _bulk_select(self, selected: bool) -> None:
        target_ids = {
            unit_id
            for unit_id, unit in self._units.items()
            if not self._unit_rows[unit_id].isHidden()
            and unit.runnable_count > 0
            and not unit.load_failed
        }
        if selected:
            self._selected_unit_ids.update(target_ids)
        else:
            self._selected_unit_ids.difference_update(target_ids)
        for unit_id in target_ids:
            row = self._unit_rows[unit_id]
            row.checkbox.blockSignals(True)
            row.set_selected(selected)
            row.checkbox.blockSignals(False)
        self._apply_unit_filter()
        self.pageChanged.emit()

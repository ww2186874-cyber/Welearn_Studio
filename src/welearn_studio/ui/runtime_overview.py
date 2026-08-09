"""Runtime summary and structured log column."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QTextDocument, QTextOption
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .presentation import LogEntry, RuntimeView, format_duration
from .theme import ACCOUNT_STATE_COLORS, ACCOUNT_STATE_LABELS, COLORS
from .widgets import StateDot, set_standard_icon

SEVERITY_COLORS = {
    "debug": COLORS["muted"],
    "info": COLORS["accent"],
    "warning": COLORS["warning"],
    "error": COLORS["danger"],
}

SEVERITY_LABELS = {
    "debug": "调试",
    "info": "信息",
    "warning": "警告",
    "error": "错误",
}


class _LogItemDelegate(QStyledItemDelegate):
    """Wrap only the message column; metadata stays a single centered line."""

    def initStyleOption(self, option: QStyleOptionViewItem, index) -> None:
        super().initStyleOption(option, index)
        if index.column() != 3:
            option.features &= ~QStyleOptionViewItem.ViewItemFeature.WrapText
            option.textElideMode = Qt.TextElideMode.ElideRight


class RuntimeOverview(QFrame):
    clearLogRequested = Signal()

    # Keep the metadata readable while leaving the message column flexible.
    # These values are logical pixels and are adjusted from the current font
    # metrics when the panel is resized or the interface scale changes.
    _LOG_MIN_MESSAGE_WIDTH = 120
    _LOG_MIN_SCOPE_WIDTH = 148

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("runtimePanel")
        self.setMinimumWidth(310)
        self.setMaximumWidth(520)
        self._entries: list[LogEntry] = []

        title = QLabel("运行状态", self)
        title.setObjectName("pageTitle")
        self.state_dot = StateDot(ACCOUNT_STATE_COLORS["stopped"], self)
        self.state_label = QLabel(ACCOUNT_STATE_LABELS["stopped"], self)
        state_row = QHBoxLayout()
        state_row.setSpacing(7)
        state_row.addWidget(self.state_dot)
        state_row.addWidget(self.state_label)
        state_row.addStretch(1)

        self.completed_value = self._stat("0 / 0", "已结束请求")
        self.active_value = self._stat("0 / 0", "当前批次并发")
        self.accepted_value = self._stat("0", "已接受")
        self.rejected_value = self._stat("0", "已拒绝")
        self.unknown_value = self._stat("0", "未知")
        self.cancelled_value = self._stat("0", "已取消")
        self.elapsed_value = self._stat("--:--:--", "已运行")
        self.platform_value = self._stat("--:--:--", "实际可刷")
        self.estimated_value = self._stat("--:--:--", "预计耗时")
        self.remaining_value = self._stat("--:--:--", "剩余时间")
        stats = QGridLayout()
        stats.setHorizontalSpacing(16)
        stats.setVerticalSpacing(8)
        stats.addLayout(self.completed_value[0], 0, 0, 1, 2)
        stats.addLayout(self.active_value[0], 1, 0, 1, 2)
        stats.addLayout(self.elapsed_value[0], 2, 0)
        stats.addLayout(self.remaining_value[0], 2, 1)
        stats.addLayout(self.estimated_value[0], 3, 0)
        stats.addLayout(self.platform_value[0], 3, 1)

        self.active_title = QLabel("当前任务", self)
        self.active_title.setObjectName("sectionTitle")
        self.active_count = QLabel("0 / 0", self)
        self.active_count.setObjectName("muted")
        self.active_title.setVisible(False)
        self.active_count.setVisible(False)
        active_tools = QHBoxLayout()
        active_tools.setSpacing(8)
        active_tools.addWidget(self.active_title)
        active_tools.addWidget(self.active_count)
        active_tools.addStretch(1)
        self.active_tasks = QListWidget(self)
        self.active_tasks.setObjectName("activeTaskList")
        self.active_tasks.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.active_tasks.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.active_tasks.setWordWrap(True)
        self.active_tasks.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.active_tasks.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.active_tasks.setMaximumHeight(176)
        self.active_tasks.setVisible(False)

        log_title = QLabel("运行日志", self)
        log_title.setObjectName("sectionTitle")
        self.severity_filter = QComboBox(self)
        self.severity_filter.setObjectName("logSeverityFilter")
        for label, value in (
            ("全部", "all"),
            ("信息", "info"),
            ("警告", "warning"),
            ("错误", "error"),
        ):
            self.severity_filter.addItem(label, value)
        self.copy_button = QPushButton("复制", self)
        self.copy_button.setObjectName("copyLogButton")
        self.clear_button = QPushButton("清空", self)
        self.clear_button.setObjectName("clearLogButton")
        set_standard_icon(self.clear_button, QStyle.StandardPixmap.SP_TrashIcon)
        log_tools = QHBoxLayout()
        log_tools.setSpacing(6)
        log_tools.addWidget(log_title)
        log_tools.addStretch(1)
        log_tools.addWidget(self.severity_filter)
        log_tools.addWidget(self.copy_button)
        log_tools.addWidget(self.clear_button)

        self.log = QTreeWidget(self)
        self.log.setObjectName("structuredLog")
        self.log.setColumnCount(4)
        self.log.setHeaderLabels(("时间", "级别", "范围", "消息"))
        self.log.setRootIsDecorated(False)
        # Runtime messages can contain long course paths and server details.
        # Wrap them in the message column instead of hiding the tail behind
        # an elipsis or requiring a horizontal scrollbar.
        self.log.setUniformRowHeights(False)
        self.log.setWordWrap(True)
        self.log.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.log.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.log.setAlternatingRowColors(True)
        self.log.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.log.setItemDelegate(_LogItemDelegate(self.log))
        header = self.log.header()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._configure_log_columns()
        header.sectionResized.connect(lambda *_args: self._update_log_row_heights())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 16, 14, 14)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addLayout(state_row)
        layout.addLayout(stats)
        layout.addLayout(active_tools)
        layout.addWidget(self.active_tasks)
        layout.addSpacing(4)
        layout.addLayout(log_tools)
        layout.addWidget(self.log, 1)

        self.severity_filter.currentIndexChanged.connect(self._render_entries)
        self.copy_button.clicked.connect(self.copy_selected)
        self.clear_button.clicked.connect(self.clearLogRequested)

    @staticmethod
    def _stat(value: str, label: str) -> tuple[QVBoxLayout, QLabel]:
        value_label = QLabel(value)
        value_label.setObjectName("sectionTitle")
        text_label = QLabel(label)
        text_label.setObjectName("muted")
        layout = QVBoxLayout()
        layout.setSpacing(1)
        layout.addWidget(value_label)
        layout.addWidget(text_label)
        return layout, value_label

    def set_runtime(self, runtime: RuntimeView) -> None:
        state = runtime.state if runtime.state in ACCOUNT_STATE_COLORS else "unknown"
        self.state_dot.set_color(ACCOUNT_STATE_COLORS[state])
        self.state_label.setText(ACCOUNT_STATE_LABELS[state])
        self.completed_value[1].setText(f"{runtime.completed} / {runtime.planned}")
        limit = runtime.concurrency if runtime.concurrency > 0 else runtime.active
        self.active_value[1].setText(f"{runtime.active} / {limit}")
        self.accepted_value[1].setText(str(runtime.accepted))
        self.rejected_value[1].setText(str(runtime.rejected))
        self.unknown_value[1].setText(str(runtime.unknown))
        self.cancelled_value[1].setText(str(runtime.cancelled))
        self.elapsed_value[1].setText(format_duration(runtime.elapsed_seconds))
        self.platform_value[1].setText(
            format_duration(runtime.platform_seconds)
            if runtime.platform_seconds > 0
            else "--:--:--"
        )
        self.estimated_value[1].setText(
            format_duration(runtime.estimated_seconds)
            if runtime.estimated_seconds > 0
            else "--:--:--"
        )
        self.remaining_value[1].setText(
            format_duration(runtime.remaining_seconds)
            if runtime.estimated_seconds > 0
            else "--:--:--"
        )

    def set_active_tasks(self, tasks: list[str], concurrency: int = 0) -> None:
        """Show only the tasks currently occupying the worker pool."""
        self.active_tasks.clear()
        for message in tasks:
            item = QListWidgetItem(message)
            item.setToolTip(message)
            self.active_tasks.addItem(item)
        self.active_count.setText(
            f"{len(tasks)} / {concurrency}" if concurrency else str(len(tasks))
        )
        visible = bool(tasks)
        self.active_title.setVisible(visible)
        self.active_count.setVisible(visible)
        self.active_tasks.setVisible(visible)

    def set_entries(self, entries: list[LogEntry]) -> None:
        self._entries = list(entries)
        self._render_entries()

    def append_entry(self, entry: LogEntry) -> None:
        self._entries.append(entry)
        if entry.transient:
            return
        selected = str(self.severity_filter.currentData())
        if selected in {"all", entry.severity.casefold()}:
            self.log.addTopLevelItem(self._make_item(entry))
            self._update_log_row_heights()
            self.log.scrollToBottom()

    def clear_entries(self) -> None:
        self._entries.clear()
        self.log.clear()

    def copy_selected(self) -> None:
        items = self.log.selectedItems() or [
            self.log.topLevelItem(index) for index in range(self.log.topLevelItemCount())
        ]
        lines = [
            "\t".join(item.text(column) for column in range(4))
            for item in items
            if item is not None
        ]
        QApplication.clipboard().setText("\n".join(lines))

    def _render_entries(self, _index: int = -1) -> None:
        selected = str(self.severity_filter.currentData() or "all")
        self.log.clear()
        for entry in self._entries:
            if entry.transient:
                continue
            if selected == "all" or entry.severity.casefold() == selected:
                self.log.addTopLevelItem(self._make_item(entry))
        self._update_log_row_heights()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._configure_log_columns()
        self._update_log_row_heights()

    def _configure_log_columns(self) -> None:
        """Set readable metadata widths without starving the message column."""
        if not hasattr(self, "log"):
            return
        metrics = self.log.fontMetrics()
        time_width = max(76, metrics.horizontalAdvance("00:00:00") + 24)
        severity_width = max(62, metrics.horizontalAdvance("警告") + 24)
        # Course/unit paths are the most commonly wrapped metadata in the log.
        scope_width = max(
            self._LOG_MIN_SCOPE_WIDTH,
            metrics.horizontalAdvance("课程 / 单元 / 小课") + 28,
        )

        viewport_width = self.log.viewport().width()
        if viewport_width > 0:
            metadata_width = time_width + severity_width + scope_width
            deficit = metadata_width + self._LOG_MIN_MESSAGE_WIDTH - viewport_width
            if deficit > 0:
                # Preserve readable timestamps and levels first, then reduce
                # the scope column down to its narrow-panel floor.
                reducible_scope = max(0, scope_width - self._LOG_MIN_SCOPE_WIDTH)
                scope_reduction = min(reducible_scope, deficit)
                scope_width -= scope_reduction
                deficit -= scope_reduction
                if deficit > 0:
                    reducible_time = max(0, time_width - 68)
                    time_reduction = min(reducible_time, deficit)
                    time_width -= time_reduction
                    deficit -= time_reduction
                if deficit > 0:
                    severity_width = max(54, severity_width - deficit)

        header = self.log.header()
        header.resizeSection(0, time_width)
        header.resizeSection(1, severity_width)
        header.resizeSection(2, scope_width)

    def _update_log_row_heights(self) -> None:
        """Give wrapped log messages enough vertical space to remain readable."""
        if not hasattr(self, "log") or self.log.topLevelItemCount() == 0:
            return
        message_width = max(1, self.log.columnWidth(3) - 12)
        font = self.log.font()
        for index in range(self.log.topLevelItemCount()):
            item = self.log.topLevelItem(index)
            document = QTextDocument()
            document.setDefaultFont(font)
            option = QTextOption()
            option.setWrapMode(QTextOption.WrapMode.WrapAnywhere)
            document.setDefaultTextOption(option)
            document.setPlainText(item.text(3))
            document.setTextWidth(message_width)
            height = max(24, int(document.size().height()) + 8)
            item.setSizeHint(3, QSize(0, height))
        self.log.doItemsLayout()

    @staticmethod
    def _make_item(entry: LogEntry) -> QTreeWidgetItem:
        severity = entry.severity.casefold()
        item = QTreeWidgetItem(
            (entry.timestamp, SEVERITY_LABELS.get(severity, "未知"), entry.scope, entry.message)
        )
        item.setForeground(
            1, QColor(SEVERITY_COLORS.get(entry.severity.casefold(), COLORS["muted"]))
        )
        item.setToolTip(2, entry.scope)
        item.setToolTip(3, entry.message)
        item.setTextAlignment(0, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignCenter)
        item.setTextAlignment(1, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignCenter)
        item.setTextAlignment(2, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignCenter)
        item.setTextAlignment(3, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        return item

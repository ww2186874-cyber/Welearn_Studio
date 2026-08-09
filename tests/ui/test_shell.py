from __future__ import annotations

import tempfile

from PySide6.QtCore import QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QSignalSpy

from welearn_studio.ui.main_window import MainWindow
from welearn_studio.ui.presentation import (
    AccountView,
    CourseView,
    LogEntry,
    RuntimeView,
    format_duration,
)
from welearn_studio.ui.runtime_overview import RuntimeOverview
from welearn_studio.ui.theme import ACCOUNT_STATE_COLORS, MAX_SCALE, MIN_SCALE

from .support import UiTestCase


class ShellTests(UiTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        settings = QSettings(self.temp_dir.name + "/ui.ini", QSettings.Format.IniFormat)
        self.window = MainWindow(settings)
        self.window.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.window.close()
        self.temp_dir.cleanup()
        super().tearDown()

    def test_all_account_states_have_distinct_colors(self) -> None:
        self.assertEqual(len(ACCOUNT_STATE_COLORS), 8)
        self.assertEqual(len(set(ACCOUNT_STATE_COLORS.values())), len(ACCOUNT_STATE_COLORS))

    def test_account_switch_preserves_runtime_column_width(self) -> None:
        accounts = [
            AccountView("a1", "first@example.test", state="signed_in"),
            AccountView("a2", "second@example.test", state="homework", progress=35),
        ]
        self.window.set_accounts(accounts, "a1")
        self.window.splitter.setSizes((250, 720, 410))
        self.app.processEvents()
        before = self.window.splitter.sizes()[2]
        self.window.accounts.list.setCurrentRow(1)
        self.app.processEvents()
        self.assertLessEqual(abs(self.window.splitter.sizes()[2] - before), 1)

    def test_account_sidebar_shows_time_study_countdown(self) -> None:
        self.window.set_accounts(
            [
                AccountView(
                    "a1",
                    "first@example.test",
                    state="time_study",
                    progress=25,
                    estimated_seconds=3661,
                    remaining_seconds=3599,
                )
            ],
            "a1",
        )
        self.app.processEvents()
        card = self.window.accounts.list.itemWidget(self.window.accounts.list.item(0))
        self.assertEqual(card.countdown_label.text(), "剩余 00:59:59")
        self.assertTrue(card.countdown_label.isVisible())

        self.window.accounts.update_account(
            AccountView("a1", "first@example.test", state="homework", progress=25)
        )
        self.assertFalse(card.countdown_label.isVisible())
        self.assertEqual(card.countdown_label.text(), "")

    def test_task_and_navigation_actions_are_exposed_as_signals(self) -> None:
        self.window.set_accounts([AccountView("a1", "first@example.test")], "a1")
        self.window.set_account_context(
            AccountView("a1", "first@example.test"), [CourseView("c1", "Course")], "c1"
        )
        account_spy = QSignalSpy(self.window.accountSelected)
        start_spy = QSignalSpy(self.window.startRequested)
        self.window.accounts.accountSelected.emit("a1")
        self.window.workspace.startRequested.emit(self.window.workspace.snapshot())
        self.assertEqual(account_spy.at(0), ["a1"])
        self.assertEqual(start_spy.count(), 1)

    def test_runtime_can_collapse_only_through_explicit_control(self) -> None:
        self.assertTrue(self.window.runtime.isVisible())
        self.window.toggle_runtime()
        self.assertFalse(self.window.runtime.isVisible())
        self.assertFalse(self.window.workspace.runtime_toggle.isChecked())
        self.window.toggle_runtime()
        self.app.processEvents()
        self.assertTrue(self.window.runtime.isVisible())

    def test_scale_is_clamped_and_uses_point_sized_application_font(self) -> None:
        self.window.theme.set_scale(1000)
        self.assertEqual(self.window.interface_scale, MAX_SCALE)
        self.assertGreater(self.app.font().pointSizeF(), 0)
        self.window.theme.set_scale(1)
        self.assertEqual(self.window.interface_scale, MIN_SCALE)

    def test_large_scale_expands_fixed_side_columns(self) -> None:
        self.window.theme.set_scale(200)
        self.app.processEvents()

        self.assertEqual(self.window.accounts.minimumWidth(), 440)
        self.assertEqual(self.window.runtime.minimumWidth(), 620)
        self.assertGreaterEqual(
            self.window.accounts.add_button.width(),
            self.window.accounts.add_button.minimumSizeHint().width(),
        )

    def test_ctrl_wheel_over_spinbox_changes_scale_not_parameter(self) -> None:
        self.window.theme.set_scale(100)
        before_value = self.window.workspace.total_hours.value()
        event = QWheelEvent(
            QPointF(5, 5),
            QPointF(5, 5),
            QPoint(),
            QPoint(0, 120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.ControlModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )

        self.app.sendEvent(self.window.workspace.total_hours.spin_box, event)

        self.assertEqual(self.window.interface_scale, 110)
        self.assertEqual(self.window.workspace.total_hours.value(), before_value)

    def test_changing_account_context_clears_stale_course_units(self) -> None:
        from .support import sample_units

        self.window.set_account_context(
            AccountView("a1", "first@example.test"),
            [CourseView("c1", "课程一")],
            "c1",
        )
        self.window.set_course_units(sample_units(), {"unit-1"})
        self.assertTrue(self.window.workspace._units)

        self.window.set_account_context(
            AccountView("a2", "second@example.test"),
            [CourseView("c2", "课程二")],
            "c2",
        )

        self.assertFalse(self.window.workspace._units)
        self.assertFalse(self.window.workspace.selected_unit_ids())


class RuntimeOverviewTests(UiTestCase):
    def test_duration_format_is_exact_and_non_negative(self) -> None:
        self.assertEqual(format_duration(0), "00:00:00")
        self.assertEqual(format_duration(3661), "01:01:01")
        self.assertEqual(format_duration(-5), "00:00:00")

    def test_runtime_summary_and_structured_log(self) -> None:
        panel = RuntimeOverview()
        panel.set_runtime(
            RuntimeView(
                "time_study",
                completed=2,
                planned=5,
                accepted=1,
                rejected=1,
                active=3,
                concurrency=4,
                elapsed_seconds=61,
                estimated_seconds=3661,
                remaining_seconds=3600,
                platform_seconds=3597,
            )
        )
        panel.set_entries(
            [
                LogEntry("10:00:00", "info", "plan", "Started"),
                LogEntry("10:00:01", "error", "lesson", "Rejected"),
            ]
        )
        self.assertEqual(panel.completed_value[1].text(), "2 / 5")
        self.assertEqual(panel.active_value[1].text(), "3 / 4")
        self.assertEqual(panel.elapsed_value[1].text(), "00:01:01")
        self.assertEqual(panel.platform_value[1].text(), "00:59:57")
        self.assertEqual(panel.estimated_value[1].text(), "01:01:01")
        self.assertEqual(panel.remaining_value[1].text(), "01:00:00")
        self.assertEqual(panel.log.columnCount(), 4)
        self.assertEqual(panel.log.topLevelItemCount(), 2)
        panel.severity_filter.setCurrentIndex(panel.severity_filter.findData("error"))
        self.assertEqual(panel.log.topLevelItemCount(), 1)
        panel.close()

    def test_current_tasks_are_separate_from_historical_log(self) -> None:
        panel = RuntimeOverview()
        panel.set_active_tasks(["第一课 · 计划 2 分钟", "第二课 · 计划 2 分钟"], 2)
        panel.set_entries(
            [
                LogEntry("10:00:00", "info", "当前任务", "正在刷 第一课", True),
                LogEntry("10:02:00", "info", "第一课", "接口接受"),
            ]
        )

        self.assertEqual(panel.active_count.text(), "2 / 2")
        self.assertEqual(panel.active_tasks.count(), 2)
        self.assertEqual(panel.log.topLevelItemCount(), 1)
        panel.set_active_tasks([], 2)
        self.assertFalse(panel.active_tasks.isVisible())
        panel.close()

    def test_long_log_messages_wrap_without_horizontal_scrolling(self) -> None:
        panel = RuntimeOverview()
        panel.resize(360, 520)
        message = "请求未确认：" + "课程路径 / 单元 / 小课 / " * 18
        panel.set_entries([LogEntry("10:00:00", "warning", "lesson", message)])
        panel.show()
        self.app.processEvents()

        item = panel.log.topLevelItem(0)
        self.assertEqual(item.text(3), message)
        self.assertEqual(item.toolTip(3), message)
        self.assertTrue(panel.log.wordWrap())
        self.assertFalse(panel.log.uniformRowHeights())
        self.assertGreater(panel.log.sizeHintForRow(0), panel.log.fontMetrics().lineSpacing())
        self.assertEqual(
            panel.log.horizontalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        panel.close()

    def test_log_metadata_is_centered_and_scope_column_is_not_cramped(self) -> None:
        panel = RuntimeOverview()
        panel.resize(520, 520)
        panel.set_entries([LogEntry("10:00:00", "info", "B2U2 The Magic of", "接口接受")])
        panel.show()
        self.app.processEvents()

        item = panel.log.topLevelItem(0)
        self.assertGreaterEqual(panel.log.columnWidth(2), 148)
        self.assertEqual(item.toolTip(2), "B2U2 The Magic of")
        self.assertTrue(item.textAlignment(0) & int(Qt.AlignmentFlag.AlignVCenter))
        self.assertTrue(item.textAlignment(1) & int(Qt.AlignmentFlag.AlignHCenter))
        self.assertTrue(item.textAlignment(2) & int(Qt.AlignmentFlag.AlignHCenter))
        self.assertTrue(item.textAlignment(3) & int(Qt.AlignmentFlag.AlignVCenter))
        panel.close()

    def test_homework_has_no_fake_countdown(self) -> None:
        panel = RuntimeOverview()
        panel.set_runtime(RuntimeView("homework", planned=2))
        self.assertEqual(panel.remaining_value[1].text(), "--:--:--")
        panel.close()

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QAbstractSpinBox

from welearn_studio.ui.course_workspace import CourseWorkspace
from welearn_studio.ui.presentation import CoursePageSnapshot

from .support import UiTestCase, sample_units


class CourseWorkspaceTests(UiTestCase):
    def setUp(self) -> None:
        self.workspace = CourseWorkspace()
        self.workspace.resize(820, 760)
        self.workspace.show()
        self.workspace.set_units(sample_units())
        self.app.processEvents()

    def tearDown(self) -> None:
        self.workspace.close()
        super().tearDown()

    def test_unit_search_and_filters(self) -> None:
        self.workspace.unit_search.setText("practice")
        self.app.processEvents()
        self.assertFalse(self.workspace._unit_rows["unit-2"].isHidden())
        self.assertTrue(self.workspace._unit_rows["unit-1"].isHidden())

        self.workspace.unit_search.clear()
        self.workspace.unit_filter.setCurrentIndex(
            self.workspace.unit_filter.findData("unavailable")
        )
        self.app.processEvents()
        self.assertFalse(self.workspace._unit_rows["unit-3"].isHidden())
        self.assertFalse(self.workspace._unit_rows["unit-4"].isHidden())
        self.assertTrue(self.workspace._unit_rows["unit-1"].isHidden())

    def test_select_buttons_stay_external_and_skip_unavailable_units(self) -> None:
        self.assertIsNot(
            self.workspace.select_all_button.parentWidget(), self.workspace.unit_filter
        )
        QTest.mouseClick(self.workspace.select_all_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.workspace.selected_unit_ids(), frozenset({"unit-1", "unit-2"}))
        self.assertFalse(self.workspace._unit_rows["unit-3"].checkbox.isEnabled())
        self.assertTrue(self.workspace._unit_rows["unit-3"].property("unavailable"))

        QTest.mouseClick(self.workspace.select_none_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.workspace.selected_unit_ids(), frozenset())

    def test_selected_units_are_shared_between_modes(self) -> None:
        self.workspace._unit_rows["unit-1"].checkbox.setChecked(True)
        self.assertEqual(self.workspace.selected_unit_ids(), frozenset({"unit-1"}))
        self.workspace.mode.set_value("time_study")
        self.assertEqual(self.workspace.selected_unit_ids(), frozenset({"unit-1"}))
        self.workspace.mode.set_value("homework")
        self.assertTrue(self.workspace._unit_rows["unit-1"].checkbox.isChecked())

    def test_numeric_fields_have_external_units_and_requested_ranges(self) -> None:
        controls = (
            (self.workspace.accuracy, 100),
            (self.workspace.total_hours, 72),
            (self.workspace.random_minutes, 30),
            (self.workspace.concurrency, 100),
        )
        for control, maximum in controls:
            self.assertEqual(control.spin_box.maximum(), maximum)
            self.assertEqual(control.spin_box.prefix(), "")
            self.assertEqual(control.spin_box.suffix(), "")
            self.assertTrue(control.unit_label.text())
            self.assertEqual(
                control.spin_box.buttonSymbols(), QAbstractSpinBox.ButtonSymbols.NoButtons
            )

        self.assertEqual(self.workspace.concurrency.unit_label.text(), "个并发")

    def test_parameter_rows_align_and_step_buttons_are_vertical_and_reliable(self) -> None:
        self.workspace.mode.set_value("time_study")
        self.app.processEvents()
        controls = (
            self.workspace.total_hours,
            self.workspace.random_minutes,
            self.workspace.concurrency,
        )
        self.assertEqual(len({control.value_frame.geometry().x() for control in controls}), 1)
        self.assertEqual(len({control.unit_label.geometry().x() for control in controls}), 1)

        control = self.workspace.concurrency
        self.assertLess(control.up_button.geometry().y(), control.down_button.geometry().y())
        self.assertEqual(control.up_button.geometry().x(), control.down_button.geometry().x())
        before = control.value()
        for _ in range(12):
            QTest.mouseClick(control.up_button, Qt.MouseButton.LeftButton)
        self.assertEqual(control.value(), before + 12)
        for _ in range(12):
            QTest.mouseClick(control.down_button, Qt.MouseButton.LeftButton)
        self.assertEqual(control.value(), before)

    def test_parameter_stack_height_follows_current_mode(self) -> None:
        self.workspace.mode.set_value("homework")
        homework_height = self.workspace.parameter_stack.sizeHint().height()
        self.workspace.mode.set_value("time_study")
        time_height = self.workspace.parameter_stack.sizeHint().height()

        self.assertLess(homework_height, time_height)

    def test_progress_and_aligned_task_controls(self) -> None:
        self.workspace.set_progress(3, 8, running=True)
        self.assertEqual(self.workspace.progress.value(), 38)
        self.assertEqual(self.workspace.countdown_label.text(), "剩余 --:--:--")
        self.assertFalse(self.workspace.start_button.isEnabled())
        self.assertTrue(self.workspace.stop_button.isEnabled())
        self.assertEqual(
            self.workspace.start_button.minimumWidth(), self.workspace.stop_button.minimumWidth()
        )

    def test_progress_bar_and_countdown_render_runtime_values(self) -> None:
        self.workspace.set_progress(0, 4, running=True, remaining_seconds=3661)
        self.assertEqual(self.workspace.progress.value(), 0)
        self.assertEqual(self.workspace.progress_label.text(), "已处理 0/4")
        self.assertEqual(self.workspace.countdown_label.text(), "剩余 01:01:01")

        self.workspace.set_progress(4, 4, running=False, remaining_seconds=0)
        self.assertEqual(self.workspace.progress.value(), 100)
        self.assertEqual(self.workspace.countdown_label.text(), "剩余 00:00:00")
        self.assertTrue(self.workspace.start_button.isEnabled())
        self.assertFalse(self.workspace.stop_button.isEnabled())

    def test_snapshot_cannot_reselect_an_unavailable_unit(self) -> None:
        snapshot = CoursePageSnapshot(
            mode="time_study",
            selected_unit_ids=frozenset({"unit-3"}),
            selected_lessons={"unit-3": frozenset()},
        )

        self.workspace.apply_snapshot(snapshot)

        self.assertNotIn("unit-3", self.workspace.selected_unit_ids())
        self.assertFalse(self.workspace._unit_rows["unit-3"].checkbox.isChecked())

    def test_start_emits_complete_current_page_snapshot(self) -> None:
        self.workspace._unit_rows["unit-1"].checkbox.setChecked(True)
        self.workspace.accuracy.set_value(92)
        spy = QSignalSpy(self.workspace.startRequested)
        QTest.mouseClick(self.workspace.start_button, Qt.MouseButton.LeftButton)
        self.assertEqual(spy.count(), 1)
        snapshot = spy.at(0)[0]
        self.assertIsInstance(snapshot, CoursePageSnapshot)
        self.assertEqual(snapshot.accuracy, 92)
        self.assertEqual(snapshot.selected_unit_ids, frozenset({"unit-1"}))

from __future__ import annotations

from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QSpinBox

from welearn_studio.ui.lesson_dialog import LessonSelectionDialog
from welearn_studio.ui.presentation import PresetView
from welearn_studio.ui.preset_dialog import PresetDialog

from .support import UiTestCase, sample_units


class DialogTests(UiTestCase):
    def test_lesson_dialog_contains_only_runnable_lessons(self) -> None:
        dialog = LessonSelectionDialog(sample_units()[0])
        self.assertEqual(dialog.list.count(), 2)
        self.assertEqual(dialog.selected_ids(), frozenset({"lesson-1", "lesson-2"}))
        first_id = str(dialog.list.item(0).data(Qt.ItemDataRole.UserRole))
        self.assertNotIn(sample_units()[0].name, dialog._checks[first_id].text())
        self.assertGreater(
            dialog.list.item(0).sizeHint().height(), dialog.list.fontMetrics().height()
        )
        self.assertFalse(bool(dialog.windowFlags() & Qt.WindowType.WindowContextHelpButtonHint))
        dialog.show()
        self.app.processEvents()
        self.assertTrue(dialog._checks[first_id].isVisible())
        dialog.close()

    def test_lesson_search_and_visible_bulk_selection(self) -> None:
        dialog = LessonSelectionDialog(sample_units()[0])
        dialog.search.setText("welcome")
        dialog._set_visible_checked(False)
        self.assertEqual(dialog.selected_ids(), frozenset({"lesson-2"}))
        dialog.close()

    def test_lesson_dialog_controls_respond_to_real_mouse_clicks(self) -> None:
        dialog = LessonSelectionDialog(sample_units()[0])
        dialog.show()
        self.app.processEvents()
        first_id = str(dialog.list.item(0).data(Qt.ItemDataRole.UserRole))
        first_check = dialog._checks[first_id]

        QTest.mouseClick(first_check, Qt.MouseButton.LeftButton, pos=first_check.rect().center())
        self.assertNotIn(first_id, dialog.selected_ids())
        self.assertEqual(dialog.summary.text(), "已选 1 / 2 课时")

        QTest.mouseClick(dialog.select_none_button, Qt.MouseButton.LeftButton)
        self.assertEqual(dialog.selected_ids(), frozenset())
        QTest.mouseClick(dialog.select_all_button, Qt.MouseButton.LeftButton)
        self.assertEqual(dialog.selected_ids(), frozenset({"lesson-1", "lesson-2"}))

        accepted_spy = QSignalSpy(dialog.selectionAccepted)
        QTest.mouseClick(dialog.confirm_button, Qt.MouseButton.LeftButton)
        self.assertEqual(accepted_spy.count(), 1)
        self.assertEqual(accepted_spy.at(0)[0], sample_units()[0].stable_id)

    def test_preset_dialog_is_management_only_and_emits_crud_intents(self) -> None:
        dialog = PresetDialog("Sample course")
        self.assertFalse(dialog.save_button.isEnabled())
        dialog.name_input.setText("Current page")
        self.assertTrue(dialog.save_button.isEnabled())
        dialog.set_presets(
            [PresetView("preset-1", "Focused run"), PresetView("preset-2", "Review run")],
            "preset-1",
        )
        self.assertEqual(dialog.findChildren(QSpinBox), [])
        self.assertFalse(bool(dialog.windowFlags() & Qt.WindowType.WindowContextHelpButtonHint))
        self.assertGreater(dialog.list.spacing(), 0)
        self.assertGreater(
            dialog.list.item(0).sizeHint().height(), dialog.list.fontMetrics().height()
        )
        self.assertTrue(dialog.close_button.property("danger"))
        self.assertGreaterEqual(dialog.close_button.minimumWidth(), 100)
        self.assertEqual(dialog.preset_count.text(), "2 项")

        save_spy = QSignalSpy(dialog.saveRequested)
        apply_spy = QSignalSpy(dialog.applyRequested)
        rename_spy = QSignalSpy(dialog.renameRequested)
        delete_spy = QSignalSpy(dialog.deleteRequested)
        dialog._request_apply()
        dialog._request_save()
        dialog.renameRequested.emit("preset-1", "Renamed")
        dialog._request_delete()
        self.assertEqual(apply_spy.at(0), ["preset-1"])
        self.assertEqual(save_spy.at(0), ["Current page"])
        self.assertEqual(rename_spy.at(0), ["preset-1", "Renamed"])
        self.assertEqual(delete_spy.at(0), ["preset-1"])
        dialog.close()

    def test_preset_dialog_buttons_respond_to_real_mouse_clicks(self) -> None:
        dialog = PresetDialog("Sample course")
        dialog.set_presets([PresetView("preset-1", "Focused run")], "preset-1")
        dialog.show()
        self.app.processEvents()
        save_spy = QSignalSpy(dialog.saveRequested)
        apply_spy = QSignalSpy(dialog.applyRequested)
        rename_spy = QSignalSpy(dialog.renameRequested)
        delete_spy = QSignalSpy(dialog.deleteRequested)

        dialog.name_input.setText("Current page")
        QTest.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
        QTest.mouseClick(dialog.apply_button, Qt.MouseButton.LeftButton)
        with patch(
            "welearn_studio.ui.preset_dialog.QInputDialog.getText",
            return_value=("Renamed", True),
        ):
            QTest.mouseClick(dialog.rename_button, Qt.MouseButton.LeftButton)
        QTest.mouseClick(dialog.delete_button, Qt.MouseButton.LeftButton)

        self.assertEqual(save_spy.at(0), ["Current page"])
        self.assertEqual(apply_spy.at(0), ["preset-1"])
        self.assertEqual(rename_spy.at(0), ["preset-1", "Renamed"])
        self.assertEqual(delete_spy.at(0), ["preset-1"])

        rejected_spy = QSignalSpy(dialog.rejected)
        QTest.mouseClick(dialog.close_button, Qt.MouseButton.LeftButton)
        self.assertEqual(rejected_spy.count(), 1)

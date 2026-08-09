from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy
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
        self.assertFalse(bool(dialog.windowFlags() & Qt.WindowType.WindowContextHelpButtonHint))
        dialog.close()

    def test_lesson_search_and_visible_bulk_selection(self) -> None:
        dialog = LessonSelectionDialog(sample_units()[0])
        dialog.search.setText("welcome")
        dialog._set_visible_checked(False)
        self.assertEqual(dialog.selected_ids(), frozenset({"lesson-2"}))
        dialog.close()

    def test_preset_dialog_is_management_only_and_emits_crud_intents(self) -> None:
        dialog = PresetDialog("Sample course")
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

        apply_spy = QSignalSpy(dialog.applyRequested)
        rename_spy = QSignalSpy(dialog.renameRequested)
        delete_spy = QSignalSpy(dialog.deleteRequested)
        dialog._request_apply()
        dialog.renameRequested.emit("preset-1", "Renamed")
        dialog._request_delete()
        self.assertEqual(apply_spy.at(0), ["preset-1"])
        self.assertEqual(rename_spy.at(0), ["preset-1", "Renamed"])
        self.assertEqual(delete_spy.at(0), ["preset-1"])
        dialog.close()

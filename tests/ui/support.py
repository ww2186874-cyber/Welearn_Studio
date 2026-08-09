from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from welearn_studio.ui.presentation import LessonView, UnitView


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def sample_units() -> list[UnitView]:
    return [
        UnitView(
            "unit-1",
            "01",
            "Foundations",
            (
                LessonView("lesson-1", "Welcome"),
                LessonView("lesson-2", "First steps"),
                LessonView("reference", "Reference", runnable=False),
            ),
        ),
        UnitView("unit-2", "02", "Practice", (LessonView("lesson-3", "Guided practice"),)),
        UnitView("unit-3", "03", "Unavailable", (LessonView("locked", "Locked", runnable=False),)),
        UnitView("unit-4", "04", "Could not load", (), load_failed=True),
    ]


class UiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = application()

    def tearDown(self) -> None:
        self.app.processEvents()

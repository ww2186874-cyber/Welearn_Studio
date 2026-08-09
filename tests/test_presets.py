import unittest

from welearn_studio.domain.models import (
    CourseCatalog,
    CourseIdentity,
    CourseSelection,
    LessonDefinition,
    LessonIdentity,
    TaskMode,
    TaskParameters,
    UnitDefinition,
    UnitIdentity,
    UnitLessonSelection,
)
from welearn_studio.domain.presets import ItemReference, PresetSnapshot, PresetUnitSnapshot
from welearn_studio.services.presets import CourseMismatchError, apply_preset, capture_preset


def unit(
    stable_id: str,
    name: str,
    lessons: list[tuple[str, str, bool]],
) -> UnitDefinition:
    return UnitDefinition(
        UnitIdentity(stable_id, name),
        tuple(
            LessonDefinition(LessonIdentity(lesson_id, lesson_name), runnable)
            for lesson_id, lesson_name, runnable in lessons
        ),
    )


class PresetTests(unittest.TestCase):
    def test_capture_is_complete_including_unselected_units(self) -> None:
        catalog = CourseCatalog(
            CourseIdentity("course-1", "Synthetic course"),
            (
                unit("u1", "Unit One", [("l1", "Lesson One", True)]),
                unit("u2", "Unit Two", [("l2", "Lesson Two", True)]),
            ),
        )
        selection = CourseSelection(
            frozenset({"u1"}),
            (
                UnitLessonSelection("u1", frozenset({"l1"})),
                UnitLessonSelection("u2", frozenset({"l2"})),
            ),
        )

        snapshot = capture_preset(
            preset_id="preset-1",
            name="Full snapshot",
            catalog=catalog,
            parameters=TaskParameters(mode=TaskMode.TIME_STUDY, total_hours=3),
            selection=selection,
        )

        self.assertEqual(len(snapshot.units), 2)
        self.assertTrue(snapshot.units[0].selected)
        self.assertFalse(snapshot.units[1].selected)
        self.assertEqual(snapshot.units[1].selected_lessons[0].stable_id, "l2")

    def test_apply_prefers_stable_id_then_uses_exact_name(self) -> None:
        snapshot = PresetSnapshot(
            "preset-1",
            "Synthetic",
            CourseIdentity("old-course", "Synthetic course"),
            TaskParameters(mode=TaskMode.TIME_STUDY, total_hours=2, concurrency=4),
            (
                PresetUnitSnapshot(
                    ItemReference("stable-unit", "Old unit name"),
                    True,
                    (ItemReference("stable-lesson", "Old lesson name"),),
                ),
                PresetUnitSnapshot(
                    ItemReference("old-unit-id", "Fallback Unit"),
                    False,
                    (
                        ItemReference("old-lesson-id", "Fallback Lesson"),
                        ItemReference("missing", "Missing Lesson"),
                    ),
                ),
            ),
        )
        catalog = CourseCatalog(
            CourseIdentity("new-course", "Synthetic course"),
            (
                unit("stable-unit", "Renamed unit", [("stable-lesson", "Renamed lesson", True)]),
                unit("new-unit-id", "Fallback Unit", [("new-lesson-id", "Fallback Lesson", True)]),
            ),
        )

        applied = apply_preset(snapshot, catalog)

        self.assertEqual(applied.parameters.concurrency, 4)
        self.assertEqual(applied.selection.selected_unit_ids, frozenset({"stable-unit"}))
        self.assertEqual(applied.selection.lessons_for("stable-unit"), frozenset({"stable-lesson"}))
        self.assertEqual(applied.selection.lessons_for("new-unit-id"), frozenset({"new-lesson-id"}))
        self.assertEqual([item.stable_id for item in applied.skipped_lessons], ["missing"])

    def test_ambiguous_name_and_unrunnable_lesson_are_skipped(self) -> None:
        snapshot = PresetSnapshot(
            "p",
            "P",
            CourseIdentity("course", "Course"),
            TaskParameters(),
            (
                PresetUnitSnapshot(
                    ItemReference("u-old", "Unit"),
                    True,
                    (
                        ItemReference("l-old", "Duplicate"),
                        ItemReference("disabled-old", "Disabled"),
                    ),
                ),
            ),
        )
        catalog = CourseCatalog(
            CourseIdentity("course", "Renamed course"),
            (
                unit(
                    "u-old",
                    "Unit renamed",
                    [
                        ("l1", "Duplicate", True),
                        ("l2", "Duplicate", True),
                        ("disabled", "Disabled", False),
                    ],
                ),
            ),
        )

        applied = apply_preset(snapshot, catalog)

        self.assertEqual(applied.selection.lessons_for("u-old"), frozenset())
        self.assertEqual(len(applied.skipped_lessons), 2)

    def test_wrong_course_is_rejected(self) -> None:
        snapshot = PresetSnapshot(
            "p",
            "P",
            CourseIdentity("course-a", "Course A"),
            TaskParameters(),
            (),
        )
        with self.assertRaises(CourseMismatchError):
            apply_preset(snapshot, CourseCatalog(CourseIdentity("course-b", "Course B")))


if __name__ == "__main__":
    unittest.main()

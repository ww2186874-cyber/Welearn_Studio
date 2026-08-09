import json
import tempfile
import unittest
from pathlib import Path

from welearn_studio.domain.models import (
    AccountIdentity,
    CourseIdentity,
    CourseSelection,
    CourseSettings,
    TaskMode,
    TaskParameters,
    UnitLessonSelection,
    WorkspaceSettings,
)
from welearn_studio.domain.presets import ItemReference, PresetSnapshot, PresetUnitSnapshot
from welearn_studio.services.settings import JsonSettingsStore, SettingsError, hashed_key


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name, "nested", "settings.json")
        self.account = AccountIdentity("Account@Example.Test", "Synthetic")
        self.course = CourseIdentity("Course-Stable-ID", "Synthetic Course")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_workspace_and_course_round_trip_with_hashed_keys(self) -> None:
        settings = CourseSettings(
            TaskParameters(TaskMode.TIME_STUDY, 83, 5, 17, 6),
            CourseSelection(
                frozenset({"unit-2", "unit-1"}),
                (UnitLessonSelection("unit-1", frozenset({"lesson-2", "lesson-1"})),),
            ),
        )
        store = JsonSettingsStore(self.path)
        store.save_workspace(WorkspaceSettings(125, "C:/synthetic/accounts.csv"))
        store.save_selected_course(self.account, self.course.stable_id)
        store.save_course(self.account, self.course, settings)

        restored = JsonSettingsStore(self.path)
        self.assertEqual(
            restored.load_workspace(), WorkspaceSettings(125, "C:/synthetic/accounts.csv")
        )
        self.assertEqual(restored.load_selected_course(self.account), self.course.stable_id)
        self.assertEqual(restored.load_course(self.account, self.course), settings)

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        account_key = hashed_key("account", self.account.username.casefold())
        course_key = hashed_key("course", self.course.stable_id)
        self.assertEqual(list(raw["accounts"]), [account_key])
        self.assertEqual(list(raw["accounts"][account_key]["courses"]), [course_key])
        self.assertNotIn(self.account.username, raw["accounts"])
        self.assertFalse(list(self.path.parent.glob("*.tmp")))

    def test_passwords_have_no_settings_field_or_save_path(self) -> None:
        store = JsonSettingsStore(self.path)
        store.save_course(self.account, self.course, CourseSettings())
        document = self.path.read_text(encoding="utf-8").casefold()

        self.assertNotIn("password", document)
        self.assertNotIn("runtime-secret", document)

    def test_presets_are_shared_by_course_and_support_lifecycle(self) -> None:
        snapshot = PresetSnapshot(
            "preset-1",
            "Before",
            self.course,
            TaskParameters(TaskMode.TIME_STUDY, 91, 2, 4, 3),
            (
                PresetUnitSnapshot(
                    ItemReference("unit", "Unit"),
                    True,
                    (ItemReference("lesson", "Lesson"),),
                ),
            ),
        )
        store = JsonSettingsStore(self.path)
        store.save_preset(snapshot)

        self.assertEqual(store.list_presets(self.course), (snapshot,))
        store.rename_preset(self.course, "preset-1", "After")
        self.assertEqual(store.list_presets(self.course)[0].name, "After")
        self.assertTrue(store.delete_preset(self.course, "preset-1"))
        self.assertFalse(store.delete_preset(self.course, "preset-1"))
        self.assertEqual(store.list_presets(self.course), ())

    def test_invalid_json_is_reported_without_overwrite(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not-json", encoding="utf-8")

        with self.assertRaises(SettingsError):
            JsonSettingsStore(self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{not-json")


if __name__ == "__main__":
    unittest.main()

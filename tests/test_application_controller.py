from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import Future
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from welearn_studio.adapters import (
    CourseContext,
    CourseSummary,
    LessonSummary,
    ReadResult,
    UnitSummary,
    Visibility,
    WorkflowResult,
)
from welearn_studio.application import StudioController
from welearn_studio.domain import CourseIdentity, RequestOutcome, WorkspaceSettings
from welearn_studio.services.planning import InsufficientStudyTime
from welearn_studio.services.settings import JsonSettingsStore
from welearn_studio.ui import MainWindow

APP = QApplication.instance() or QApplication([])


class ImmediateExecutor:
    def submit(self, operation):
        future = Future()
        try:
            future.set_result(operation())
        except Exception as exc:
            future.set_exception(exc)
        return future


class FakeRemote:
    def __init__(self) -> None:
        self.closed = False
        self.login_calls = 0
        self.timed_durations: list[int] = []

    def close(self) -> None:
        self.closed = True

    def login(self, _account, _password, _cancellation=None):
        self.login_calls += 1
        return RequestOutcome.accepted()

    def list_courses(self, _cancellation=None):
        return ReadResult(
            RequestOutcome.accepted(),
            (CourseSummary(CourseIdentity("course-a", "综合教程")),),
        )

    def bootstrap_course(self, _course_id, _cancellation=None):
        return ReadResult(RequestOutcome.accepted(), CourseContext("learner", "class-a"))

    def list_units(self, _course_id, _learner_id, _cancellation=None):
        return ReadResult(
            RequestOutcome.accepted(),
            (UnitSummary(0, "第一单元"), UnitSummary(1, "空单元")),
        )

    def list_lessons(self, _course_id, _learner_id, _class_id, unit_index, _cancellation=None):
        lessons = (
            (LessonSummary("lesson-a", "第一课", Visibility.VISIBLE),) if unit_index == 0 else ()
        )
        return ReadResult(RequestOutcome.accepted(), lessons)

    def submit_homework(self, _context, _accuracy, _cancellation=None):
        return WorkflowResult(RequestOutcome.accepted(), ())

    def run_timed_study(self, _context, duration_seconds, _cancellation=None):
        self.timed_durations.append(duration_seconds)
        return WorkflowResult(RequestOutcome.accepted(), ())


class BlockingRemote(FakeRemote):
    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()
        self.two_started = threading.Event()
        self._lock = threading.Lock()
        self._started = 0

    def list_lessons(self, _course_id, _learner_id, _class_id, unit_index, _cancellation=None):
        lessons = (
            tuple(
                LessonSummary(f"lesson-{index}", f"第 {index} 课", Visibility.VISIBLE)
                for index in range(1, 4)
            )
            if unit_index == 0
            else ()
        )
        return ReadResult(RequestOutcome.accepted(), lessons)

    def run_timed_study(self, _context, duration_seconds, _cancellation=None):
        self.timed_durations.append(duration_seconds)
        with self._lock:
            self._started += 1
            if self._started >= 2:
                self.two_started.set()
        self.release.wait(2)
        return WorkflowResult(RequestOutcome.accepted(), ())


class UnknownRemote(FakeRemote):
    def list_lessons(self, _course_id, _learner_id, _class_id, unit_index, _cancellation=None):
        lessons = (
            tuple(
                LessonSummary(f"lesson-{index}", f"第 {index} 课", Visibility.VISIBLE)
                for index in range(1, 9)
            )
            if unit_index == 0
            else ()
        )
        return ReadResult(RequestOutcome.accepted(), lessons)

    def run_timed_study(self, _context, duration_seconds, _cancellation=None):
        self.timed_durations.append(duration_seconds)
        return WorkflowResult(RequestOutcome.unknown("SCO state was malformed"), ())


class ApplicationControllerTests(unittest.TestCase):
    def test_remote_outcomes_have_actionable_safe_messages(self) -> None:
        self.assertEqual(
            StudioController._outcome_text(RequestOutcome.rejected("credentials were rejected")),
            "账号或密码错误",
        )
        self.assertEqual(
            StudioController._outcome_text(RequestOutcome.rejected("server returned HTTP 405")),
            "服务器拒绝请求（HTTP 405）",
        )
        self.assertEqual(
            StudioController._outcome_text(RequestOutcome.unknown("transport request failed")),
            "网络请求失败",
        )
        self.assertEqual(
            StudioController._outcome_text(RequestOutcome.unknown("SCO state was malformed")),
            "平台小课状态响应无法识别",
        )

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        ui_settings = QSettings(self.directory.name + "/ui.ini", QSettings.Format.IniFormat)
        self.window = MainWindow(ui_settings)
        self.remotes: list[FakeRemote] = []

        def remote_factory():
            remote = FakeRemote()
            self.remotes.append(remote)
            return remote

        self.controller = StudioController(
            self.window,
            settings=JsonSettingsStore(self.directory.name + "/workspace.json"),
            remote_factory=remote_factory,
            executor=ImmediateExecutor(),  # type: ignore[arg-type]
            restore_last_file=False,
        )
        self.window.show()
        APP.processEvents()

    def tearDown(self) -> None:
        self.controller.shutdown()
        self.window.close()
        self.directory.cleanup()
        APP.processEvents()

    def test_account_login_course_loading_and_unavailable_unit(self) -> None:
        self.assertTrue(self.controller.add_account("student@example.test", "synthetic"))
        self.controller.refresh_current_courses()
        APP.processEvents()

        session = self.controller.current_session
        self.assertIsNotNone(session)
        assert session is not None
        self.assertEqual(session.state, "signed_in")
        self.assertEqual(self.window.workspace.course_combo.count(), 1)
        self.assertEqual(len(self.window.workspace._units), 2)
        rows = list(self.window.workspace._unit_rows.values())
        self.assertTrue(rows[0].checkbox.isEnabled())
        self.assertTrue(rows[0].checkbox.isChecked())
        self.assertFalse(rows[1].checkbox.isEnabled())
        self.assertTrue(rows[1].property("unavailable"))

        self.controller.refresh_current_courses()
        APP.processEvents()
        self.assertEqual(self.remotes[0].login_calls, 1)

    def test_complete_preset_restores_parameters_units_and_lessons(self) -> None:
        self.controller.add_account("student@example.test", "synthetic")
        self.controller.refresh_current_courses()
        APP.processEvents()
        snapshot = self.window.workspace.snapshot()
        self.controller.save_preset("course-a", "常用配置", snapshot)
        presets = self.controller.settings.list_presets(
            self.controller.current_session.catalogs["course-a"].identity  # type: ignore[union-attr]
        )

        self.window.workspace.accuracy.set_value(55)
        self.window.workspace.select_none_button.click()
        self.controller.apply_saved_preset("course-a", presets[0].preset_id)

        restored = self.window.workspace.snapshot()
        self.assertEqual(restored.accuracy, 100)
        self.assertTrue(restored.selected_unit_ids)
        self.assertEqual(
            restored.selected_lessons[next(iter(restored.selected_unit_ids))],
            frozenset({"lesson-a"}),
        )

    def test_time_task_uses_whole_plan_and_finishes_as_endpoint_accepted(self) -> None:
        self.controller.add_account("student@example.test", "synthetic")
        self.controller.refresh_current_courses()
        APP.processEvents()
        self.window.workspace.mode.set_value("time_study")
        self.window.workspace.total_hours.set_value(1)
        self.window.workspace.random_minutes.set_value(0)
        self.controller.start_task(self.window.workspace.snapshot())
        APP.processEvents()

        session = self.controller.current_session
        self.assertIsNotNone(session)
        assert session is not None
        self.assertEqual(session.state, "accepted")
        self.assertEqual(self.remotes[0].timed_durations, [3600])
        self.assertEqual(session.runtime.platform_seconds, 3600)
        self.assertEqual(session.runtime.accepted, 1)
        self.assertTrue(any(entry.message == "接口接受" for entry in session.logs))

    def test_time_task_is_blocked_when_total_cannot_cover_selected_lessons(self) -> None:
        self.controller.add_account("student@example.test", "synthetic")
        self.controller.refresh_current_courses()
        APP.processEvents()
        self.window.workspace.mode.set_value("time_study")

        with patch(
            "welearn_studio.application.task_execution.create_time_study_plan",
            side_effect=InsufficientStudyTime(55, 70),
        ):
            self.controller.start_task(self.window.workspace.snapshot())
        APP.processEvents()

        session = self.controller.current_session
        self.assertIsNotNone(session)
        assert session is not None
        self.assertIsNone(session.active_handle)
        self.assertIn("总时长不足", session.logs[-1].message)
        self.assertEqual(self.remotes[0].timed_durations, [])

    def test_selecting_restored_account_does_not_contact_remote_service(self) -> None:
        self.controller.add_account("student@example.test", "synthetic")
        APP.processEvents()

        session = self.controller.current_session
        self.assertIsNotNone(session)
        assert session is not None
        self.assertEqual(session.state, "pending")
        self.assertFalse(session.authenticated)
        self.assertEqual(self.window.workspace.course_combo.count(), 0)

    def test_unit_identity_survives_reordering_of_differently_named_units(self) -> None:
        first = StudioController._unit_id("第一单元")
        after_insertion = StudioController._unit_id("第一单元")

        self.assertEqual(first, after_insertion)

    def test_controller_restores_json_interface_scale(self) -> None:
        settings = JsonSettingsStore(self.directory.name + "/scaled-workspace.json")
        settings.save_workspace(WorkspaceSettings(160, None))
        ui_settings = QSettings(self.directory.name + "/scaled-ui.ini", QSettings.Format.IniFormat)
        window = MainWindow(ui_settings, initial_scale=100)
        controller = StudioController(
            window,
            settings=settings,
            remote_factory=FakeRemote,  # type: ignore[arg-type]
            executor=ImmediateExecutor(),  # type: ignore[arg-type]
            restore_last_file=False,
        )
        try:
            self.assertEqual(window.interface_scale, 160)
        finally:
            controller.shutdown()
            window.close()


class QueuedApplicationControllerTests(unittest.TestCase):
    def test_unknown_result_stops_before_sweeping_remaining_lessons(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ui_settings = QSettings(directory + "/failure-ui.ini", QSettings.Format.IniFormat)
            window = MainWindow(ui_settings)
            remote = UnknownRemote()
            controller = StudioController(
                window,
                settings=JsonSettingsStore(directory + "/failure-workspace.json"),
                remote_factory=lambda: remote,  # type: ignore[arg-type]
                restore_last_file=False,
            )
            window.show()
            try:
                controller.add_account("failure@example.test", "synthetic")
                controller.refresh_current_courses()
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    APP.processEvents()
                    session = controller.current_session
                    if session is not None and "course-a" in session.catalogs:
                        break
                    time.sleep(0.01)

                window.workspace.mode.set_value("time_study")
                window.workspace.total_hours.set_value(1)
                window.workspace.random_minutes.set_value(0)
                window.workspace.concurrency.set_value(3)
                controller.start_task(window.workspace.snapshot())

                session = controller.current_session
                assert session is not None
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    APP.processEvents()
                    if session.active_handle is None:
                        break
                    time.sleep(0.01)

                self.assertIsNone(session.active_handle)
                self.assertGreaterEqual(session.runtime.unknown, 1)
                self.assertLessEqual(session.runtime.completed, 3)
                self.assertLessEqual(len(remote.timed_durations), 3)
                self.assertTrue(any("已停止后续任务" in entry.message for entry in session.logs))
                self.assertTrue(
                    any("平台小课状态响应无法识别" in entry.message for entry in session.logs)
                )
            finally:
                controller.shutdown()
                window.close()
                APP.processEvents()

    def test_active_lesson_list_never_exceeds_concurrency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ui_settings = QSettings(directory + "/active-ui.ini", QSettings.Format.IniFormat)
            window = MainWindow(ui_settings)
            remote = BlockingRemote()
            controller = StudioController(
                window,
                settings=JsonSettingsStore(directory + "/active-workspace.json"),
                remote_factory=lambda: remote,  # type: ignore[arg-type]
                restore_last_file=False,
            )
            window.show()
            try:
                controller.add_account("active@example.test", "synthetic")
                controller.refresh_current_courses()
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    APP.processEvents()
                    session = controller.current_session
                    if session is not None and "course-a" in session.catalogs:
                        break
                    time.sleep(0.01)

                window.workspace.mode.set_value("time_study")
                window.workspace.total_hours.set_value(1)
                window.workspace.random_minutes.set_value(0)
                window.workspace.concurrency.set_value(2)
                controller.start_task(window.workspace.snapshot())

                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    APP.processEvents()
                    if remote.two_started.is_set() and window.runtime.active_tasks.count() == 2:
                        break
                    time.sleep(0.01)

                session = controller.current_session
                assert session is not None
                self.assertEqual(session.runtime.active, 2)
                self.assertEqual(session.runtime.concurrency, 2)
                self.assertEqual(window.runtime.active_tasks.count(), 2)
                self.assertEqual(window.runtime.active_value[1].text(), "2 / 2")

                remote.release.set()
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    APP.processEvents()
                    if session.active_handle is None:
                        break
                    time.sleep(0.01)

                self.assertIsNone(session.active_handle)
                self.assertEqual(session.runtime.completed, 3)
                self.assertEqual(session.runtime.accepted, 3)
                self.assertEqual(session.runtime.active, 0)
                self.assertEqual(window.runtime.active_tasks.count(), 0)
            finally:
                remote.release.set()
                controller.shutdown()
                window.close()
                APP.processEvents()

    def test_real_executor_delivers_login_and_course_results_to_qt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ui_settings = QSettings(directory + "/queued-ui.ini", QSettings.Format.IniFormat)
            window = MainWindow(ui_settings)
            controller = StudioController(
                window,
                settings=JsonSettingsStore(directory + "/queued-workspace.json"),
                remote_factory=FakeRemote,  # type: ignore[arg-type]
                restore_last_file=False,
            )
            window.show()
            try:
                controller.add_account("queued@example.test", "synthetic")
                controller.refresh_current_courses()
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    APP.processEvents()
                    session = controller.current_session
                    if session is not None and "course-a" in session.catalogs:
                        break
                    time.sleep(0.01)

                session = controller.current_session
                self.assertIsNotNone(session)
                assert session is not None
                self.assertIn("course-a", session.catalogs)
                self.assertEqual(len(window.workspace._units), 2)
            finally:
                controller.shutdown()
                window.close()
                APP.processEvents()


if __name__ == "__main__":
    unittest.main()

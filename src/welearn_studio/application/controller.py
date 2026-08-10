"""Qt-safe orchestration between the presentation and service boundaries."""

from __future__ import annotations

import hashlib
import math
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable
from uuid import uuid4

from PySide6.QtCore import QObject, QStandardPaths, QTimer, Signal
from PySide6.QtWidgets import QFileDialog, QMessageBox

from welearn_studio.adapters import (
    CourseContext,
    CourseSummary,
    LessonContext,
    WeLearnRemoteClient,
)
from welearn_studio.domain import (
    AccountIdentity,
    CourseCatalog,
    CourseIdentity,
    CourseSelection,
    CourseSettings,
    LessonDefinition,
    LessonIdentity,
    OutcomeKind,
    RequestOutcome,
    TaskMode,
    TaskParameters,
    UnitDefinition,
    UnitIdentity,
    UnitLessonSelection,
    UnitLoadState,
    WorkspaceSettings,
)
from welearn_studio.services.account_import import AccountCredential, parse_account_file
from welearn_studio.services.execution import (
    ExecutionHandle,
    ExecutionReport,
    TaskResult,
)
from welearn_studio.services.planning import InsufficientStudyTime
from welearn_studio.services.presets import apply_preset, capture_preset
from welearn_studio.services.settings import JsonSettingsStore, hashed_key
from welearn_studio.ui import AddAccountDialog
from welearn_studio.ui.main_window import MainWindow
from welearn_studio.ui.presentation import (
    AccountView,
    CoursePageSnapshot,
    CourseView,
    LessonView,
    LogEntry,
    PresetView,
    RuntimeView,
    UnitView,
    format_duration,
)

from .task_execution import (
    MissingLessonContexts,
    NoRunnableLessons,
    PreparedTask,
    PreparedTaskRun,
    TaskRunCallbacks,
    prepare_task_run,
    start_task_run,
)

RemoteFactory = Callable[[], WeLearnRemoteClient]


@dataclass(slots=True)
class _AccountSession:
    stable_id: str
    credential: AccountCredential
    client: WeLearnRemoteClient
    state: str = "pending"
    courses: dict[str, CourseSummary] = field(default_factory=dict)
    catalogs: dict[str, CourseCatalog] = field(default_factory=dict)
    contexts: dict[str, CourseContext] = field(default_factory=dict)
    lesson_contexts: dict[str, dict[tuple[str, str], LessonContext]] = field(default_factory=dict)
    selected_course_id: str | None = None
    logs: list[LogEntry] = field(default_factory=list)
    runtime: RuntimeView = field(default_factory=RuntimeView)
    active_handle: ExecutionHandle[object] | None = None
    active_tasks: dict[str, str] = field(default_factory=dict)
    reported_task_ids: set[str] = field(default_factory=set)
    batch_number: int = 0
    countdown_started_at: float | None = None
    countdown_deadline: float | None = None
    operation_generation: int = 0
    busy: bool = False
    authenticated: bool = False

    @property
    def identity(self) -> AccountIdentity:
        return self.credential.identity


@dataclass(frozen=True, slots=True)
class _LoginResult:
    account_id: str
    generation: int
    outcome: RequestOutcome
    courses: tuple[CourseSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class _CourseResult:
    account_id: str
    generation: int
    course_id: str
    outcome: RequestOutcome
    context: CourseContext | None = None
    catalog: CourseCatalog | None = None
    lesson_contexts: tuple[tuple[tuple[str, str], LessonContext], ...] = ()
    issue_count: int = 0


@dataclass(frozen=True, slots=True)
class _TaskFinished:
    account_id: str
    report: ExecutionReport[object]


@dataclass(frozen=True, slots=True)
class _TaskFailed:
    account_id: str


class StudioController(QObject):
    """Own runtime sessions while keeping every widget update on the Qt thread."""

    _background_finished = Signal(object)
    _task_log = Signal(str, str, str, str)
    _task_started = Signal(str, str, str)
    _task_result = Signal(str, str, str, bool, object)
    _task_batch_started = Signal(str, int, object)
    _task_batch_finished = Signal(str, int, object)
    _task_finished = Signal(object)

    def __init__(
        self,
        window: MainWindow,
        *,
        settings: JsonSettingsStore | None = None,
        remote_factory: RemoteFactory = WeLearnRemoteClient,
        executor: ThreadPoolExecutor | None = None,
        restore_last_file: bool = True,
    ) -> None:
        super().__init__(window)
        self.window = window
        self.settings = settings or JsonSettingsStore(self.default_settings_path())
        self._remote_factory = remote_factory
        self._executor = executor or ThreadPoolExecutor(
            max_workers=8, thread_name_prefix="welearn-studio"
        )
        self._owns_executor = executor is None
        self._futures: set[Future[object]] = set()
        self._accounts: dict[str, _AccountSession] = {}
        self._current_account_id: str | None = None
        self._rendering = False
        self._add_dialog: AddAccountDialog | None = None
        self._shutting_down = False
        self._countdown_timer: QTimer | None = None

        self._background_finished.connect(self._on_background_finished)
        self._task_log.connect(self._on_task_log)
        self._task_started.connect(self._on_task_started)
        self._task_result.connect(self._on_task_result)
        self._task_batch_started.connect(self._on_task_batch_started)
        self._task_batch_finished.connect(self._on_task_batch_finished)
        self._task_finished.connect(self._on_task_finished)
        self._connect_window()

        workspace = self.settings.load_workspace()
        if self.window.interface_scale != workspace.interface_scale_percent:
            self.window.theme.set_scale(workspace.interface_scale_percent)
        if restore_last_file:
            if workspace.last_account_file and Path(workspace.last_account_file).is_file():
                self.import_accounts(workspace.last_account_file, remember=False)
        self._refresh_account_list()

    @staticmethod
    def default_settings_path() -> Path:
        root = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)
        return Path(root) / "workspace.json"

    def _connect_window(self) -> None:
        self.window.accountAddRequested.connect(self.open_add_account)
        self.window.accountsImportRequested.connect(self.choose_account_file)
        self.window.accountRemoveRequested.connect(self.request_remove_account)
        self.window.accountSelected.connect(self.select_account)
        self.window.coursesRefreshRequested.connect(self.refresh_current_courses)
        self.window.courseSelected.connect(self.select_course)
        self.window.lessonSelectionChanged.connect(self._lesson_selection_changed)
        self.window.workspaceChanged.connect(self._workspace_changed)
        self.window.startRequested.connect(self.start_task)
        self.window.stopRequested.connect(self.stop_current_task)
        self.window.configurationSaveRequested.connect(self.save_preset)
        self.window.configurationApplyRequested.connect(self.apply_saved_preset)
        self.window.configurationRenameRequested.connect(self.rename_preset)
        self.window.configurationDeleteRequested.connect(self.delete_preset)
        self.window.restoreDefaultsRequested.connect(self.request_restore_defaults)
        self.window.clearLogRequested.connect(self.clear_current_log)
        self.window.interfaceScaleChanged.connect(self._save_interface_scale)

    @staticmethod
    def _account_id(username: str) -> str:
        return hashed_key("account-view", username.casefold())[:24]

    def add_account(
        self, username: str, password: str, nickname: str = "", *, select: bool = True
    ) -> bool:
        try:
            credential = AccountCredential(AccountIdentity(username, nickname or None), password)
        except ValueError:
            return False
        duplicate = next(
            (
                item
                for item in self._accounts.values()
                if item.identity.username.casefold() == credential.identity.username.casefold()
            ),
            None,
        )
        if duplicate is not None:
            return False
        account_id = self._account_id(credential.identity.username)
        self._accounts[account_id] = _AccountSession(account_id, credential, self._remote_factory())
        self._refresh_account_list(account_id if select else self._current_account_id)
        return True

    def open_add_account(self) -> None:
        if self._add_dialog is not None:
            self._add_dialog.raise_()
            self._add_dialog.activateWindow()
            return
        dialog = AddAccountDialog(self.window)
        dialog.credentialsAccepted.connect(self._add_dialog_account)
        dialog.finished.connect(lambda _result: self._clear_add_dialog(dialog))
        self._add_dialog = dialog
        dialog.open()

    def _add_dialog_account(self, username: str, password: str, nickname: str) -> None:
        if not self.add_account(username, password, nickname):
            self._append_current_log("warning", "账号", "账号无效或已经存在")

    def _clear_add_dialog(self, dialog: AddAccountDialog) -> None:
        if self._add_dialog is dialog:
            self._add_dialog = None

    def choose_account_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self.window,
            "导入账号",
            "",
            "账号文件 (*.csv *.txt);;所有文件 (*)",
        )
        if path:
            self.import_accounts(path)

    def import_accounts(self, path: str, *, remember: bool = True) -> int:
        try:
            result = parse_account_file(path)
        except (OSError, UnicodeError):
            self._append_current_log("error", "账号", "账号文件读取失败")
            return 0
        added = 0
        for credential in result.accounts:
            if self.add_account(
                credential.identity.username,
                credential.password,
                credential.identity.nickname or "",
                select=False,
            ):
                added += 1
        if remember:
            current = self.settings.load_workspace()
            self.settings.save_workspace(
                WorkspaceSettings(current.interface_scale_percent, str(Path(path).resolve()))
            )
        selected = self._current_account_id
        if selected is None and self._accounts:
            selected = next(iter(self._accounts))
        self._refresh_account_list(selected)
        if result.issues:
            self._append_current_log(
                "warning", "账号", f"导入时跳过 {len(result.issues)} 行无效数据"
            )
        return added

    def request_remove_account(self, account_id: str) -> None:
        session = self._accounts.get(account_id)
        if session is None:
            return
        if session.active_handle is not None or session.busy:
            self._append_log(session, "warning", "账号", "运行或加载期间不能移除账号")
            return
        answer = QMessageBox.question(
            self.window,
            "移除账号",
            f"确定移除“{session.identity.nickname or session.identity.username}”吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.remove_account(account_id)

    def remove_account(self, account_id: str) -> bool:
        session = self._accounts.get(account_id)
        if session is None or session.active_handle is not None or session.busy:
            return False
        session.client.close()
        del self._accounts[account_id]
        if self._current_account_id == account_id:
            self._current_account_id = None
        next_id = next(iter(self._accounts), None)
        self._refresh_account_list(next_id)
        if next_id is None:
            self._render_empty_workspace()
        return True

    def select_account(self, account_id: str) -> None:
        session = self._accounts.get(account_id)
        if session is None:
            return
        self._current_account_id = account_id
        self._render_session(session)
        if session.selected_course_id and session.courses:
            self.select_course(session.selected_course_id)

    def refresh_current_courses(self) -> None:
        if self._current_account_id is not None:
            self.refresh_courses(self._current_account_id)

    def refresh_courses(self, account_id: str) -> None:
        session = self._accounts.get(account_id)
        if session is None or session.busy:
            return
        if session.active_handle is not None:
            self._append_log(session, "warning", "课程", "任务运行期间不能刷新课程")
            return
        session.busy = True
        session.operation_generation += 1
        generation = session.operation_generation
        session.state = "pending"
        self._publish_account(session)
        if session.authenticated:
            self._append_log(session, "info", "课程", "正在刷新课程")
        else:
            self._append_log(session, "info", "登录", "正在登录并读取课程")

        def operation() -> _LoginResult:
            if not session.authenticated:
                login = session.client.login(session.identity.username, session.credential.password)
                if login.kind is not OutcomeKind.ACCEPTED:
                    return _LoginResult(account_id, generation, login)
            courses = session.client.list_courses()
            if courses.outcome.kind is not OutcomeKind.ACCEPTED:
                return _LoginResult(account_id, generation, courses.outcome)
            return _LoginResult(
                account_id, generation, RequestOutcome.accepted(), courses.value or ()
            )

        self._submit(
            operation,
            failure=lambda: _LoginResult(
                account_id,
                generation,
                RequestOutcome.unknown("background login failed"),
            ),
        )

    def select_course(self, course_id: str) -> None:
        session = self.current_session
        if session is None or course_id not in session.courses:
            return
        session.selected_course_id = course_id
        self.settings.save_selected_course(session.identity, course_id)
        if course_id in session.catalogs:
            self._render_course(session, course_id)
            return
        if session.busy or session.active_handle is not None:
            return
        session.busy = True
        session.operation_generation += 1
        generation = session.operation_generation
        summary = session.courses[course_id]
        self._append_log(session, "info", "课程", f"正在读取 {summary.identity.name}")

        def operation() -> _CourseResult:
            bootstrap = session.client.bootstrap_course(course_id)
            if bootstrap.outcome.kind is not OutcomeKind.ACCEPTED:
                return _CourseResult(session.stable_id, generation, course_id, bootstrap.outcome)
            assert bootstrap.value is not None
            units_read = session.client.list_units(course_id, bootstrap.value.learner_id)
            if units_read.outcome.kind is not OutcomeKind.ACCEPTED:
                return _CourseResult(session.stable_id, generation, course_id, units_read.outcome)
            domain_units: list[UnitDefinition] = []
            contexts: list[tuple[tuple[str, str], LessonContext]] = []
            issue_count = 0
            name_occurrences: dict[str, int] = {}
            for remote_unit in units_read.value or ():
                normalized_name = remote_unit.name.casefold().strip()
                occurrence = name_occurrences.get(normalized_name, 0)
                name_occurrences[normalized_name] = occurrence + 1
                unit_id = self._unit_id(remote_unit.name, occurrence)
                identity = UnitIdentity(unit_id, remote_unit.name, str(remote_unit.index + 1))
                lesson_read = session.client.list_lessons(
                    course_id,
                    bootstrap.value.learner_id,
                    bootstrap.value.class_id,
                    remote_unit.index,
                )
                if lesson_read.outcome.kind is not OutcomeKind.ACCEPTED:
                    issue_count += 1
                    domain_units.append(UnitDefinition(identity, (), UnitLoadState.FAILED))
                    continue
                lessons: list[LessonDefinition] = []
                issue_count += len(lesson_read.issues)
                for position, remote_lesson in enumerate(lesson_read.value or (), 1):
                    lesson_name = remote_lesson.location or f"课时 {position}"
                    lesson = LessonDefinition(
                        LessonIdentity(remote_lesson.sco_id, lesson_name),
                        remote_lesson.runnable,
                    )
                    lessons.append(lesson)
                    if lesson.runnable:
                        contexts.append(
                            (
                                (unit_id, lesson.identity.stable_id),
                                LessonContext(
                                    course_id,
                                    bootstrap.value.learner_id,
                                    bootstrap.value.class_id,
                                    lesson.identity.stable_id,
                                ),
                            )
                        )
                domain_units.append(UnitDefinition(identity, tuple(lessons)))
            catalog = CourseCatalog(summary.identity, tuple(domain_units))
            return _CourseResult(
                session.stable_id,
                generation,
                course_id,
                RequestOutcome.accepted(),
                bootstrap.value,
                catalog,
                tuple(contexts),
                issue_count,
            )

        self._submit(
            operation,
            failure=lambda: _CourseResult(
                session.stable_id,
                generation,
                course_id,
                RequestOutcome.unknown("background course load failed"),
            ),
        )

    @staticmethod
    def _unit_id(name: str, occurrence: int = 0) -> str:
        payload = f"{name.casefold().strip()}\0{occurrence}".encode("utf-8")
        return "unit-" + hashlib.sha256(payload).hexdigest()[:20]

    def start_task(self, snapshot: CoursePageSnapshot) -> None:
        session = self.current_session
        if session is None or session.selected_course_id is None:
            return
        if session.active_handle is not None or session.busy:
            return
        prepared = self._prepare_current_task(session, snapshot)
        if prepared is None:
            return

        if prepared.mode is TaskMode.TIME_STUDY:
            self._append_log(
                session,
                "info",
                "计划",
                f"{len(prepared.tasks)} 个课时，"
                f"实际可刷 {format_duration(prepared.platform_seconds)}；"
                f"舍弃余数 {format_duration(prepared.discarded_remainder_seconds)}；"
                f"预计实际运行 {format_duration(prepared.estimated_seconds)}",
            )

        handle = start_task_run(
            prepared,
            session.client,
            self._task_callbacks(session),
            defer_start=True,
        )
        self._activate_task_session(session, prepared, handle)
        handle.activate()

        def await_report() -> _TaskFinished:
            return _TaskFinished(session.stable_id, handle.join())

        self._submit(
            await_report,
            task_result=True,
            failure=lambda: _TaskFailed(session.stable_id),
        )

    def _prepare_current_task(
        self,
        session: _AccountSession,
        snapshot: CoursePageSnapshot,
    ) -> PreparedTaskRun | None:
        assert session.selected_course_id is not None
        course_id = session.selected_course_id
        catalog = session.catalogs.get(course_id)
        if catalog is None:
            self._append_log(session, "warning", "任务", "课程内容尚未加载")
            return None
        try:
            settings = self._snapshot_to_settings(snapshot)
            prepared = prepare_task_run(
                catalog,
                settings,
                session.lesson_contexts.get(course_id, {}),
            )
            self.settings.save_course(session.identity, catalog.identity, settings)
            return prepared
        except NoRunnableLessons:
            self._append_log(session, "warning", "任务", "没有可运行的已选课时")
        except MissingLessonContexts as error:
            self._append_log(
                session,
                "error",
                "任务",
                f"{len(error.missing)} 个小课缺少运行信息，请刷新课程后重试；任务未启动",
            )
        except InsufficientStudyTime as error:
            self._append_log(
                session,
                "warning",
                "计划",
                f"总时长不足：本次可分配 {error.available_minutes} 分钟，"
                f"已选 {error.lesson_count} 个小课至少需要 {error.lesson_count} 分钟，任务未启动",
            )
        except (KeyError, ValueError):
            self._append_log(session, "error", "任务", "当前任务配置无效")
        return None

    def _task_callbacks(self, session: _AccountSession) -> TaskRunCallbacks:
        def task_started(task: PreparedTask) -> None:
            self._task_started.emit(session.stable_id, task.stable_id, task.display_name)

        def task_finished(result: TaskResult[PreparedTask]) -> None:
            self._task_result.emit(
                session.stable_id,
                result.task.stable_id,
                result.task.target.lesson.name,
                result.started,
                result.outcome,
            )

        def batch_started(batch_number: int, tasks: tuple[PreparedTask, ...]) -> None:
            self._task_batch_started.emit(
                session.stable_id,
                batch_number,
                tuple(task.display_name for task in tasks),
            )

        def batch_finished(
            batch_number: int,
            results: tuple[TaskResult[PreparedTask], ...],
        ) -> None:
            self._task_batch_finished.emit(session.stable_id, batch_number, results)

        return TaskRunCallbacks(
            on_started=task_started,
            on_finished=task_finished,
            on_batch_started=batch_started,
            on_batch_finished=batch_finished,
            on_halted=lambda: self._task_log.emit(
                session.stable_id,
                "warning",
                "任务",
                "小课请求未被确认，已停止后续任务",
            ),
        )

    def _activate_task_session(
        self,
        session: _AccountSession,
        prepared: PreparedTaskRun,
        handle: ExecutionHandle[PreparedTask],
    ) -> None:
        session.active_handle = handle
        session.active_tasks.clear()
        session.reported_task_ids.clear()
        session.batch_number = 0
        session.state = prepared.mode.value
        now = time.monotonic()
        session.countdown_started_at = now if prepared.estimated_seconds else None
        session.countdown_deadline = (
            now + prepared.estimated_seconds if prepared.estimated_seconds else None
        )
        session.runtime = RuntimeView(
            state=prepared.mode.value,
            completed=0,
            planned=len(prepared.tasks),
            platform_seconds=prepared.platform_seconds,
            estimated_seconds=prepared.estimated_seconds,
            remaining_seconds=prepared.estimated_seconds,
            active=0,
            concurrency=prepared.concurrency,
        )
        if prepared.estimated_seconds:
            self._ensure_countdown_timer()
        self._publish_account(session)
        if self._current_account_id == session.stable_id:
            self.window.set_runtime(session.runtime)
            self.window.set_active_tasks([], prepared.concurrency)

    def stop_current_task(self) -> None:
        session = self.current_session
        if session is not None and session.active_handle is not None:
            session.active_handle.stop()
            self._append_log(session, "warning", "任务", "正在停止未完成的请求")

    def save_preset(self, course_id: str, name: str, snapshot: CoursePageSnapshot) -> None:
        session = self.current_session
        if session is None:
            return
        catalog = session.catalogs.get(course_id)
        if catalog is None:
            return
        settings = self._snapshot_to_settings(snapshot)
        existing = next(
            (
                preset
                for preset in self.settings.list_presets(catalog.identity)
                if preset.name.casefold() == name.strip().casefold()
            ),
            None,
        )
        preset_id = existing.preset_id if existing is not None else uuid4().hex
        preset = capture_preset(
            preset_id=preset_id,
            name=name.strip(),
            catalog=catalog,
            parameters=settings.parameters,
            selection=settings.selection,
        )
        self.settings.save_preset(preset)
        self._render_presets(catalog.identity, preset_id)

    def apply_saved_preset(self, course_id: str, preset_id: str) -> None:
        session = self.current_session
        if session is None:
            return
        catalog = session.catalogs.get(course_id)
        if catalog is None:
            return
        preset = next(
            (
                item
                for item in self.settings.list_presets(catalog.identity)
                if item.preset_id == preset_id
            ),
            None,
        )
        if preset is None:
            return
        applied = apply_preset(preset, catalog)
        settings = CourseSettings(applied.parameters, applied.selection)
        self.settings.save_course(session.identity, catalog.identity, settings)
        self._apply_course_settings(settings)
        if applied.skipped_units or applied.skipped_lessons:
            self._append_log(
                session,
                "warning",
                "配置",
                f"跳过 {len(applied.skipped_units)} 个单元和 {len(applied.skipped_lessons)} 个课时",
            )
        if self.window._preset_dialog is not None:
            self.window._preset_dialog.accept()

    def rename_preset(self, course_id: str, preset_id: str, name: str) -> None:
        session = self.current_session
        if session is None or course_id not in session.catalogs:
            return
        course = session.catalogs[course_id].identity
        self.settings.rename_preset(course, preset_id, name)
        self._render_presets(course, preset_id)

    def delete_preset(self, course_id: str, preset_id: str) -> None:
        session = self.current_session
        if session is None or course_id not in session.catalogs:
            return
        course = session.catalogs[course_id].identity
        self.settings.delete_preset(course, preset_id)
        self._render_presets(course)

    def request_restore_defaults(self, _account_id: str, course_id: str) -> None:
        answer = QMessageBox.question(
            self.window,
            "恢复默认",
            "确定恢复当前课程的默认配置吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.restore_defaults(course_id)

    def restore_defaults(self, course_id: str) -> None:
        session = self.current_session
        if session is None:
            return
        catalog = session.catalogs.get(course_id)
        if catalog is None:
            return
        selected_units = frozenset(
            unit.identity.stable_id for unit in catalog.units if unit.runnable_lessons
        )
        selections = tuple(
            UnitLessonSelection(
                unit.identity.stable_id,
                frozenset(lesson.identity.stable_id for lesson in unit.runnable_lessons),
            )
            for unit in catalog.units
        )
        settings = CourseSettings(
            TaskParameters(TaskMode.HOMEWORK, 100, 1, 5, 5),
            CourseSelection(selected_units, selections),
        )
        self.settings.save_course(session.identity, catalog.identity, settings)
        self._apply_course_settings(settings)

    def clear_current_log(self) -> None:
        session = self.current_session
        if session is not None:
            session.logs.clear()
            self.window.set_log_entries([])

    @property
    def current_session(self) -> _AccountSession | None:
        if self._current_account_id is None:
            return None
        return self._accounts.get(self._current_account_id)

    def _submit(
        self,
        operation: Callable[[], object],
        *,
        task_result: bool = False,
        failure: Callable[[], object],
    ) -> None:
        future = self._executor.submit(operation)
        self._futures.add(future)

        def finished(completed: Future[object]) -> None:
            try:
                result = completed.result()
            except Exception:
                result = failure()
            self._futures.discard(completed)
            if self._shutting_down:
                return
            if task_result:
                self._task_finished.emit(result)
            else:
                self._background_finished.emit(result)

        future.add_done_callback(finished)

    def _on_background_finished(self, result: object) -> None:
        if isinstance(result, _LoginResult):
            self._finish_login(result)
        elif isinstance(result, _CourseResult):
            self._finish_course(result)

    def _finish_login(self, result: _LoginResult) -> None:
        session = self._accounts.get(result.account_id)
        if session is None or session.operation_generation != result.generation:
            return
        session.busy = False
        if result.outcome.kind is not OutcomeKind.ACCEPTED:
            session.authenticated = False
            session.state = "error" if result.outcome.kind is OutcomeKind.REJECTED else "unknown"
            self._append_log(session, "error", "登录", self._outcome_text(result.outcome))
            self._publish_account(session)
            return
        session.authenticated = True
        session.state = "signed_in"
        session.courses = {item.identity.stable_id: item for item in result.courses}
        selected = self.settings.load_selected_course(session.identity)
        if selected not in session.courses:
            selected = next(iter(session.courses), None)
        session.selected_course_id = selected
        self._append_log(session, "info", "课程", f"已读取 {len(result.courses)} 门课程")
        self._publish_account(session)
        if self._current_account_id == session.stable_id:
            self._render_session(session)
            if selected is not None:
                self.select_course(selected)

    def _finish_course(self, result: _CourseResult) -> None:
        session = self._accounts.get(result.account_id)
        if session is None or session.operation_generation != result.generation:
            return
        session.busy = False
        if result.outcome.kind is not OutcomeKind.ACCEPTED or result.catalog is None:
            self._append_log(session, "error", "课程", self._outcome_text(result.outcome))
            session.state = "unknown"
            self._publish_account(session)
            if (
                session.selected_course_id
                and session.selected_course_id != result.course_id
                and session.selected_course_id not in session.catalogs
            ):
                self.select_course(session.selected_course_id)
            return
        session.contexts[result.course_id] = result.context  # type: ignore[assignment]
        session.catalogs[result.course_id] = result.catalog
        session.lesson_contexts[result.course_id] = dict(result.lesson_contexts)
        if result.issue_count:
            self._append_log(
                session,
                "warning",
                "课程",
                f"读取完成，{result.issue_count} 项内容不可用",
            )
        else:
            self._append_log(session, "info", "课程", "课程内容读取完成")
        if (
            self._current_account_id == session.stable_id
            and session.selected_course_id == result.course_id
        ):
            self._render_course(session, result.course_id)
        elif session.selected_course_id and session.selected_course_id not in session.catalogs:
            self.select_course(session.selected_course_id)

    def _on_task_started(self, account_id: str, task_id: str, message: str) -> None:
        session = self._accounts.get(account_id)
        if session is None or session.active_handle is None:
            return
        session.active_tasks[task_id] = message
        runtime = session.runtime
        session.runtime = RuntimeView(
            state=runtime.state,
            completed=runtime.completed,
            planned=runtime.planned,
            accepted=runtime.accepted,
            rejected=runtime.rejected,
            unknown=runtime.unknown,
            cancelled=runtime.cancelled,
            active=len(session.active_tasks),
            concurrency=runtime.concurrency,
            elapsed_seconds=runtime.elapsed_seconds,
            estimated_seconds=runtime.estimated_seconds,
            remaining_seconds=runtime.remaining_seconds,
            platform_seconds=runtime.platform_seconds,
        )
        self._publish_account(session)
        if self._current_account_id == account_id:
            self.window.set_runtime(session.runtime)
            self.window.set_active_tasks(list(session.active_tasks.values()), runtime.concurrency)

    def _on_task_batch_started(self, account_id: str, batch_number: int, messages: object) -> None:
        session = self._accounts.get(account_id)
        if session is None or not isinstance(messages, tuple):
            return
        session.batch_number = batch_number
        labels = [str(item) for item in messages]
        if not labels:
            return
        self._append_log(
            session,
            "info",
            "批次",
            f"第 {batch_number} 批开始（{len(labels)} 个小课）",
        )
        # Keep one lesson per row. A single long batch message becomes an
        # unreadable wrapped block in the narrow log column.
        for label in labels:
            self._append_log(session, "info", f"第 {batch_number} 批", label)

    def _on_task_batch_finished(self, account_id: str, batch_number: int, results: object) -> None:
        session = self._accounts.get(account_id)
        if session is None or not isinstance(results, tuple) or not results:
            return
        started_results = [
            result for result in results if isinstance(result, TaskResult) and result.started
        ]
        if not started_results:
            return
        accepted = rejected = unknown = cancelled = 0
        reason = ""
        for result in started_results:
            kind = result.outcome.kind
            accepted += int(kind is OutcomeKind.ACCEPTED)
            rejected += int(kind is OutcomeKind.REJECTED)
            unknown += int(kind is OutcomeKind.UNKNOWN)
            cancelled += int(kind is OutcomeKind.CANCELLED)
            if not reason and kind is not OutcomeKind.ACCEPTED:
                reason = self._outcome_text(result.outcome)
        summary = (
            f"第 {batch_number} 批结束：{len(started_results)} 个小课已结束；"
            f"接口接受 {accepted}，拒绝 {rejected}，未确认 {unknown}，取消 {cancelled}"
        )
        if reason:
            summary += f"；原因：{reason}"
        self._append_log(session, "info" if not reason else "warning", "批次", summary)

    def _on_task_result(
        self,
        account_id: str,
        task_id: str,
        lesson_name: str,
        started: bool,
        outcome: object,
    ) -> None:
        session = self._accounts.get(account_id)
        if (
            session is None
            or session.active_handle is None
            or not isinstance(outcome, RequestOutcome)
        ):
            return
        session.active_tasks.pop(task_id, None)
        if not started:
            return
        session.reported_task_ids.add(task_id)
        runtime = session.runtime
        accepted = runtime.accepted + int(outcome.kind is OutcomeKind.ACCEPTED)
        rejected = runtime.rejected + int(outcome.kind is OutcomeKind.REJECTED)
        unknown = runtime.unknown + int(outcome.kind is OutcomeKind.UNKNOWN)
        cancelled = runtime.cancelled + int(outcome.kind is OutcomeKind.CANCELLED)
        processed = accepted + rejected + unknown + cancelled
        session.runtime = RuntimeView(
            state=runtime.state,
            completed=max(runtime.completed, processed),
            planned=runtime.planned,
            accepted=accepted,
            rejected=rejected,
            unknown=unknown,
            cancelled=cancelled,
            active=len(session.active_tasks),
            concurrency=runtime.concurrency,
            elapsed_seconds=runtime.elapsed_seconds,
            estimated_seconds=runtime.estimated_seconds,
            remaining_seconds=runtime.remaining_seconds,
            platform_seconds=runtime.platform_seconds,
        )
        self._publish_account(session)
        if self._current_account_id == account_id:
            self.window.set_runtime(session.runtime)
            self.window.set_active_tasks(list(session.active_tasks.values()), runtime.concurrency)

    def _on_task_log(self, account_id: str, severity: str, scope: str, message: str) -> None:
        """Render worker task messages on the Qt thread as soon as they occur."""
        session = self._accounts.get(account_id)
        if session is None:
            return
        self._append_log(session, severity, scope, message)

    @staticmethod
    def _elapsed_seconds(session: _AccountSession, now: float | None = None) -> int:
        if session.countdown_started_at is None:
            return session.runtime.elapsed_seconds
        current = time.monotonic() if now is None else now
        return max(0, int(current - session.countdown_started_at))

    @staticmethod
    def _remaining_seconds(session: _AccountSession, now: float | None = None) -> int:
        if session.countdown_deadline is None:
            return 0
        current = time.monotonic() if now is None else now
        return max(0, int(math.ceil(session.countdown_deadline - current)))

    @staticmethod
    def _clear_countdown(session: _AccountSession) -> None:
        session.countdown_started_at = None
        session.countdown_deadline = None

    def _stop_countdown_timer_if_idle(self) -> None:
        if not any(
            session.active_handle is not None and session.countdown_started_at is not None
            for session in self._accounts.values()
        ):
            if self._countdown_timer is not None:
                self._countdown_timer.stop()

    def _ensure_countdown_timer(self) -> None:
        if self._countdown_timer is None:
            self._countdown_timer = QTimer(self)
            self._countdown_timer.setInterval(1000)
            self._countdown_timer.timeout.connect(self._update_countdowns)
        self._countdown_timer.start()

    def _update_countdowns(self) -> None:
        """Refresh per-account elapsed/remaining values without touching workers."""
        now = time.monotonic()
        for session in self._accounts.values():
            if session.active_handle is None or session.countdown_started_at is None:
                continue
            runtime = session.runtime
            updated = RuntimeView(
                state=runtime.state,
                completed=runtime.completed,
                planned=runtime.planned,
                accepted=runtime.accepted,
                rejected=runtime.rejected,
                unknown=runtime.unknown,
                cancelled=runtime.cancelled,
                active=len(session.active_tasks),
                concurrency=runtime.concurrency,
                elapsed_seconds=self._elapsed_seconds(session, now),
                platform_seconds=runtime.platform_seconds,
                estimated_seconds=runtime.estimated_seconds,
                remaining_seconds=self._remaining_seconds(session, now),
            )
            if updated == runtime:
                continue
            session.runtime = updated
            self._publish_account(session)
            if self._current_account_id == session.stable_id:
                self.window.set_runtime(updated)

    def _on_task_finished(self, payload: object) -> None:
        if isinstance(payload, _TaskFailed):
            session = self._accounts.get(payload.account_id)
            if session is None:
                return
            elapsed_seconds = self._elapsed_seconds(session)
            session.active_handle = None
            session.active_tasks.clear()
            session.reported_task_ids.clear()
            self._clear_countdown(session)
            self._stop_countdown_timer_if_idle()
            session.state = "unknown"
            session.runtime = RuntimeView(
                state="unknown",
                completed=session.runtime.completed,
                planned=session.runtime.planned,
                active=0,
                concurrency=session.runtime.concurrency,
                elapsed_seconds=elapsed_seconds,
                platform_seconds=session.runtime.platform_seconds,
                estimated_seconds=session.runtime.estimated_seconds,
            )
            self._append_log(session, "error", "任务", "后台任务异常结束")
            self._publish_account(session)
            if self._current_account_id == session.stable_id:
                self.window.set_runtime(session.runtime)
                self.window.set_active_tasks([])
            return
        if not isinstance(payload, _TaskFinished):
            return
        session = self._accounts.get(payload.account_id)
        if session is None:
            return
        accepted = rejected = unknown = cancelled = 0
        for result in payload.report.results:
            if not result.started:
                continue
            kind = result.outcome.kind
            accepted += int(kind is OutcomeKind.ACCEPTED)
            rejected += int(kind is OutcomeKind.REJECTED)
            unknown += int(kind is OutcomeKind.UNKNOWN)
            cancelled += int(kind is OutcomeKind.CANCELLED)
        planned = len(payload.report.results)
        processed = accepted + rejected + unknown + cancelled
        all_cancelled = all(
            result.outcome.kind is OutcomeKind.CANCELLED for result in payload.report.results
        )
        if processed == planned and accepted == planned:
            state = "accepted"
        elif all_cancelled:
            state = "stopped"
        elif processed == planned and rejected == planned:
            state = "error"
        else:
            state = "unknown"
        session.state = state
        session.active_handle = None
        session.active_tasks.clear()
        session.reported_task_ids.clear()
        elapsed_seconds = self._elapsed_seconds(session)
        estimated_seconds = session.runtime.estimated_seconds
        self._clear_countdown(session)
        self._stop_countdown_timer_if_idle()
        session.runtime = RuntimeView(
            state=state,
            completed=processed,
            planned=planned,
            accepted=accepted,
            rejected=rejected,
            unknown=unknown,
            cancelled=cancelled,
            active=0,
            concurrency=session.runtime.concurrency,
            elapsed_seconds=elapsed_seconds,
            platform_seconds=session.runtime.platform_seconds,
            estimated_seconds=estimated_seconds,
            remaining_seconds=0,
        )
        if state == "accepted":
            # Keep the successful endpoint result deliberately short. The
            # batch summary already contains the count, while this final line
            # is the clear account-level outcome.
            final_message = "接口接受"
            final_severity = "info"
        elif state == "stopped":
            final_message = f"任务已停止：已处理 {processed}/{planned} 个小课请求"
            final_severity = "warning"
        else:
            final_message = (
                f"任务结束：已处理 {processed}/{planned} 个小课请求；"
                f"接口接受 {accepted}，拒绝 {rejected}，未确认 {unknown}"
            )
            final_severity = "warning"
        self._append_log(session, final_severity, "任务", final_message)
        self._publish_account(session)
        if self._current_account_id == session.stable_id:
            self.window.set_runtime(session.runtime)
            self.window.set_active_tasks([])

    @staticmethod
    def _outcome_text(outcome: RequestOutcome) -> str:
        detail_messages = {
            "authorization redirect was malformed": "登录授权地址格式异常",
            "authorization redirect was incomplete": "登录授权参数读取失败",
            "credentials were rejected": "账号或密码错误",
            "credential response was malformed": "登录响应格式异常",
            "credential response was not recognized": "登录响应无法识别",
            "transport request failed": "网络请求失败",
            "transport returned malformed status": "网络响应状态异常",
            "course list was malformed": "课程列表响应无法识别",
            "course item was malformed": "课程数据格式异常",
            "course identity was missing": "课程数据缺少名称或编号",
            "course context was not recognized": "课程授权信息读取失败",
            "SCO state was malformed": "平台小课状态响应无法识别",
            "SCO state was incomplete": "平台小课状态字段不完整",
            "timed wait failed": "本地计时等待异常",
            "write response was not recognized": "平台保存响应无法识别",
            "write result was malformed": "平台保存结果格式异常",
        }
        if outcome.detail in detail_messages:
            return detail_messages[outcome.detail]
        if outcome.detail.startswith("server returned HTTP "):
            status = outcome.detail.removeprefix("server returned HTTP ")
            return f"服务器拒绝请求（HTTP {status}）"
        return {
            OutcomeKind.ACCEPTED: "接口接受",
            OutcomeKind.REJECTED: "请求被拒绝",
            OutcomeKind.UNKNOWN: "结果无法确认",
            OutcomeKind.CANCELLED: "已取消",
        }[outcome.kind]

    def _render_session(self, session: _AccountSession) -> None:
        self._rendering = True
        try:
            courses = [
                CourseView(item.identity.stable_id, item.identity.name)
                for item in session.courses.values()
            ]
            self.window.set_account_context(
                self._account_view(session), courses, session.selected_course_id
            )
            self.window.set_runtime(session.runtime)
            self.window.set_active_tasks(
                list(session.active_tasks.values()), session.runtime.concurrency
            )
            self.window.set_log_entries(session.logs)
            if session.selected_course_id in session.catalogs:
                self._render_course(session, session.selected_course_id)
            else:
                self.window.set_course_units([])
                self.window.set_presets([])
        finally:
            self._rendering = False

    def _render_course(self, session: _AccountSession, course_id: str) -> None:
        catalog = session.catalogs[course_id]
        settings = self.settings.load_course(session.identity, catalog.identity)
        if settings is None:
            settings = self._default_course_settings(catalog)
        units = [
            self._unit_view(unit, settings.selection.lessons_for(unit.identity.stable_id))
            for unit in catalog.units
        ]
        self._rendering = True
        try:
            self.window.set_course_units(units, settings.selection.selected_unit_ids)
            self.window.workspace.apply_snapshot(self._settings_to_snapshot(settings))
            self._render_presets(catalog.identity)
        finally:
            self._rendering = False

    def _apply_course_settings(self, settings: CourseSettings) -> None:
        self._rendering = True
        try:
            self.window.workspace.apply_snapshot(self._settings_to_snapshot(settings))
        finally:
            self._rendering = False

    def _render_presets(self, course: CourseIdentity, selected_id: str | None = None) -> None:
        presets = [
            PresetView(item.preset_id, item.name) for item in self.settings.list_presets(course)
        ]
        self.window.set_presets(presets, selected_id)

    @staticmethod
    def _unit_view(unit: UnitDefinition, selected_lessons: frozenset[str]) -> UnitView:
        return UnitView(
            unit.identity.stable_id,
            unit.identity.number or "",
            unit.identity.name,
            tuple(
                LessonView(
                    lesson.identity.stable_id,
                    lesson.identity.name,
                    lesson.runnable,
                )
                for lesson in unit.lessons
            ),
            selected_lessons,
            unit.load_state is UnitLoadState.FAILED,
        )

    @staticmethod
    def _default_course_settings(catalog: CourseCatalog) -> CourseSettings:
        selected_units = frozenset(
            unit.identity.stable_id for unit in catalog.units if unit.runnable_lessons
        )
        lessons = tuple(
            UnitLessonSelection(
                unit.identity.stable_id,
                frozenset(lesson.identity.stable_id for lesson in unit.runnable_lessons),
            )
            for unit in catalog.units
        )
        return CourseSettings(
            TaskParameters(TaskMode.HOMEWORK, 100, 1, 5, 5),
            CourseSelection(selected_units, lessons),
        )

    @staticmethod
    def _snapshot_to_settings(snapshot: CoursePageSnapshot) -> CourseSettings:
        parameters = TaskParameters(
            TaskMode(snapshot.mode),
            snapshot.accuracy,
            snapshot.total_hours,
            snapshot.random_minutes,
            snapshot.concurrency,
        )
        selections = tuple(
            UnitLessonSelection(unit_id, frozenset(lesson_ids))
            for unit_id, lesson_ids in snapshot.selected_lessons.items()
        )
        return CourseSettings(
            parameters,
            CourseSelection(frozenset(snapshot.selected_unit_ids), selections),
        )

    @staticmethod
    def _settings_to_snapshot(settings: CourseSettings) -> CoursePageSnapshot:
        return CoursePageSnapshot(
            settings.parameters.mode.value,
            settings.selection.selected_unit_ids,
            {item.unit_id: item.lesson_ids for item in settings.selection.lesson_selections},
            settings.parameters.accuracy_percent,
            settings.parameters.total_hours,
            settings.parameters.random_minutes,
            settings.parameters.concurrency,
        )

    def _lesson_selection_changed(self, _unit_id: str, _selected_ids: object) -> None:
        self._persist_visible_workspace()

    def _workspace_changed(self, _course_id: str, _snapshot: CoursePageSnapshot) -> None:
        self._persist_visible_workspace()

    def _persist_visible_workspace(self) -> None:
        if self._rendering:
            return
        session = self.current_session
        if session is None or session.selected_course_id is None:
            return
        catalog = session.catalogs.get(session.selected_course_id)
        if catalog is None:
            return
        settings = self._snapshot_to_settings(self.window.workspace.snapshot())
        self.settings.save_course(session.identity, catalog.identity, settings)

    def _save_interface_scale(self, scale: int) -> None:
        current = self.settings.load_workspace()
        self.settings.save_workspace(WorkspaceSettings(scale, current.last_account_file))

    def _refresh_account_list(self, selected_id: str | None = None) -> None:
        views = [self._account_view(session) for session in self._accounts.values()]
        self.window.set_accounts(views, selected_id)

    def _publish_account(self, session: _AccountSession) -> None:
        self.window.accounts.update_account(self._account_view(session))

    @staticmethod
    def _account_view(session: _AccountSession) -> AccountView:
        planned = session.runtime.planned
        progress = round(100 * session.runtime.completed / planned) if planned else 0
        return AccountView(
            session.stable_id,
            session.identity.username,
            session.identity.nickname or "",
            session.state,
            progress,
            session.runtime.estimated_seconds,
            session.runtime.remaining_seconds,
        )

    def _render_empty_workspace(self) -> None:
        self.window.workspace.set_account_name("")
        self.window.workspace.set_courses([])
        self.window.set_course_units([])
        self.window.set_runtime(RuntimeView())
        self.window.set_active_tasks([])
        self.window.set_log_entries([])
        self.window.set_presets([])

    def _append_current_log(self, severity: str, scope: str, message: str) -> None:
        session = self.current_session
        if session is not None:
            self._append_log(session, severity, scope, message)

    def _append_log(
        self,
        session: _AccountSession,
        severity: str,
        scope: str,
        message: str,
        *,
        transient: bool = False,
    ) -> None:
        entry = LogEntry(datetime.now().strftime("%H:%M:%S"), severity, scope, message, transient)
        session.logs.append(entry)
        if self._current_account_id == session.stable_id:
            self.window.append_log_entry(entry)

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        if self._countdown_timer is not None:
            self._countdown_timer.stop()
            self._countdown_timer.deleteLater()
            self._countdown_timer = None
        for session in self._accounts.values():
            session.operation_generation += 1
            if session.active_handle is not None:
                session.active_handle.stop()
        for session in self._accounts.values():
            if session.active_handle is not None:
                try:
                    session.active_handle.join(timeout=35)
                except TimeoutError:
                    pass
        if self._owns_executor:
            self._executor.shutdown(wait=True, cancel_futures=True)
        elif self._futures:
            for future in tuple(self._futures):
                future.cancel()
            wait(tuple(self._futures), timeout=35)
        for session in self._accounts.values():
            session.client.close()

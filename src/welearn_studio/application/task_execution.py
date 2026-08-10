"""Prepare and execute immutable course task runs without Qt dependencies."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

from welearn_studio.adapters.models import LessonContext, WorkflowResult
from welearn_studio.domain.models import (
    CourseCatalog,
    CourseSettings,
    LessonTarget,
    TaskMode,
)
from welearn_studio.domain.outcomes import OutcomeKind, RequestOutcome
from welearn_studio.services.execution import (
    CancellationToken,
    CooperativeTaskRunner,
    ExecutionHandle,
    TaskResult,
)
from welearn_studio.services.planning import IntegerRandom, create_time_study_plan

ContextKey = tuple[str, str]


class TaskRemote(Protocol):
    """Remote operations required by a prepared task run."""

    def submit_homework(
        self,
        context: LessonContext,
        accuracy_percent: int,
        cancellation: CancellationToken | None = None,
    ) -> WorkflowResult: ...

    def run_timed_study(
        self,
        context: LessonContext,
        duration_seconds: int,
        cancellation: CancellationToken | None = None,
    ) -> WorkflowResult: ...


class NoRunnableLessons(ValueError):
    """Raised when the current course selection contains no runnable lesson."""


class MissingLessonContexts(ValueError):
    """Raised when selected lessons do not have complete remote context."""

    def __init__(self, missing: tuple[LessonTarget, ...]) -> None:
        self.missing = missing
        super().__init__(f"{len(missing)} selected lessons are missing remote context")


@dataclass(frozen=True, slots=True)
class PreparedTask:
    target: LessonTarget
    context: LessonContext
    duration_minutes: int | None = None

    def __post_init__(self) -> None:
        if self.duration_minutes is not None and self.duration_minutes < 1:
            raise ValueError("timed tasks must run for at least one whole minute")

    @property
    def stable_id(self) -> str:
        return f"{self.target.unit.stable_id}:{self.target.lesson.stable_id}"

    @property
    def display_name(self) -> str:
        name = f"{self.target.unit.name} > {self.target.lesson.name}"
        if self.duration_minutes is not None:
            return f"{name} · {self.duration_minutes} 分钟"
        return name


@dataclass(frozen=True, slots=True)
class PreparedTaskRun:
    mode: TaskMode
    tasks: tuple[PreparedTask, ...]
    concurrency: int
    accuracy_percent: int
    platform_seconds: int = 0
    estimated_seconds: int = 0
    discarded_remainder_seconds: int = 0

    def __post_init__(self) -> None:
        if not self.tasks:
            raise ValueError("prepared runs require at least one task")
        if not 1 <= self.concurrency <= 100:
            raise ValueError("concurrency must be between 1 and 100")


TaskStartedCallback = Callable[[PreparedTask], None]
TaskFinishedCallback = Callable[[TaskResult[PreparedTask]], None]
BatchStartedCallback = Callable[[int, tuple[PreparedTask, ...]], None]
BatchFinishedCallback = Callable[[int, tuple[TaskResult[PreparedTask], ...]], None]


@dataclass(frozen=True, slots=True)
class TaskRunCallbacks:
    on_started: TaskStartedCallback | None = None
    on_finished: TaskFinishedCallback | None = None
    on_batch_started: BatchStartedCallback | None = None
    on_batch_finished: BatchFinishedCallback | None = None
    on_halted: Callable[[], None] | None = None


def _selected_targets(
    catalog: CourseCatalog,
    settings: CourseSettings,
) -> tuple[LessonTarget, ...]:
    explicit_lessons = {
        entry.unit_id: entry.lesson_ids for entry in settings.selection.lesson_selections
    }
    targets: list[LessonTarget] = []
    for unit in catalog.units:
        unit_id = unit.identity.stable_id
        if unit_id not in settings.selection.selected_unit_ids:
            continue
        default_ids = frozenset(lesson.identity.stable_id for lesson in unit.runnable_lessons)
        selected_ids = explicit_lessons.get(unit_id, default_ids)
        targets.extend(
            LessonTarget(unit.identity, lesson.identity)
            for lesson in unit.runnable_lessons
            if lesson.identity.stable_id in selected_ids
        )
    return tuple(targets)


def _context_for(
    target: LessonTarget,
    contexts: Mapping[ContextKey, LessonContext],
) -> LessonContext:
    return contexts[(target.unit.stable_id, target.lesson.stable_id)]


def prepare_task_run(
    catalog: CourseCatalog,
    settings: CourseSettings,
    contexts: Mapping[ContextKey, LessonContext],
    *,
    rng: IntegerRandom | None = None,
) -> PreparedTaskRun:
    """Validate the selection and build an immutable task run."""
    targets = _selected_targets(catalog, settings)
    if not targets:
        raise NoRunnableLessons("no runnable lessons are selected")
    missing = tuple(
        target
        for target in targets
        if (target.unit.stable_id, target.lesson.stable_id) not in contexts
    )
    if missing:
        raise MissingLessonContexts(missing)

    parameters = settings.parameters
    if parameters.mode is TaskMode.HOMEWORK:
        tasks = tuple(PreparedTask(target, _context_for(target, contexts)) for target in targets)
        return PreparedTaskRun(
            parameters.mode,
            tasks,
            parameters.concurrency,
            parameters.accuracy_percent,
        )

    plan = create_time_study_plan(
        targets,
        total_hours=parameters.total_hours,
        random_minutes=parameters.random_minutes,
        concurrency=parameters.concurrency,
        rng=rng,
    )
    tasks = tuple(
        PreparedTask(item.target, _context_for(item.target, contexts), item.duration_minutes)
        for item in plan.lessons
    )
    return PreparedTaskRun(
        parameters.mode,
        tasks,
        parameters.concurrency,
        parameters.accuracy_percent,
        platform_seconds=plan.actual_platform_seconds,
        estimated_seconds=plan.estimated_wall_minutes * 60,
        discarded_remainder_seconds=plan.discarded_remainder_seconds,
    )


class _FailureGate:
    def __init__(self, callback: Callable[[], None] | None) -> None:
        self._callback = callback
        self._lock = threading.Lock()
        self._announced = False

    def inspect(
        self,
        outcome: RequestOutcome,
        token: CancellationToken,
    ) -> RequestOutcome:
        if outcome.kind not in {OutcomeKind.REJECTED, OutcomeKind.UNKNOWN}:
            return outcome
        token.cancel()
        with self._lock:
            if self._announced:
                return outcome
            self._announced = True
        if self._callback is not None:
            try:
                self._callback()
            except Exception:
                pass
        return outcome


def _perform_task(
    remote: TaskRemote,
    run: PreparedTaskRun,
    task: PreparedTask,
    token: CancellationToken,
) -> RequestOutcome:
    if run.mode is TaskMode.TIME_STUDY:
        assert task.duration_minutes is not None
        return remote.run_timed_study(
            task.context,
            task.duration_minutes * 60,
            token,
        ).outcome
    return remote.submit_homework(task.context, run.accuracy_percent, token).outcome


def start_task_run(
    run: PreparedTaskRun,
    remote: TaskRemote,
    callbacks: TaskRunCallbacks | None = None,
    *,
    defer_start: bool = False,
) -> ExecutionHandle[PreparedTask]:
    """Start a prepared run with fixed batches and cooperative cancellation."""
    observers = callbacks or TaskRunCallbacks()
    failure_gate = _FailureGate(observers.on_halted)
    batch_number = 0

    def operation(task: PreparedTask, token: CancellationToken) -> RequestOutcome:
        return failure_gate.inspect(_perform_task(remote, run, task, token), token)

    def batch_started(tasks: tuple[PreparedTask, ...]) -> None:
        nonlocal batch_number
        batch_number += 1
        if observers.on_batch_started is not None:
            observers.on_batch_started(batch_number, tasks)

    def batch_finished(results: tuple[TaskResult[PreparedTask], ...]) -> None:
        if observers.on_batch_finished is not None:
            observers.on_batch_finished(batch_number, results)

    runner = CooperativeTaskRunner(run.concurrency)
    return runner.start(
        run.tasks,
        operation,
        on_started=observers.on_started,
        on_finished=observers.on_finished,
        on_batch_started=batch_started,
        on_batch_finished=batch_finished,
        defer_start=defer_start,
    )

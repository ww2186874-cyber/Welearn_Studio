"""Cooperative task execution without forced worker termination."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Generic, Sequence, TypeVar

from welearn_studio.domain.outcomes import RequestOutcome

TaskT = TypeVar("TaskT")


class CancellationRequested(Exception):
    """Raised at a cooperative cancellation checkpoint."""


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for cancellation, returning true when cancellation occurred."""
        return self._event.wait(timeout)

    def checkpoint(self) -> None:
        if self.is_cancelled:
            raise CancellationRequested()


@dataclass(frozen=True, slots=True)
class ExecutionProgress:
    completed: int
    planned: int

    @property
    def ratio(self) -> float:
        return self.completed / self.planned if self.planned else 0.0


@dataclass(frozen=True, slots=True)
class TaskResult(Generic[TaskT]):
    task: TaskT
    started: bool
    outcome: RequestOutcome


@dataclass(frozen=True, slots=True)
class ExecutionReport(Generic[TaskT]):
    results: tuple[TaskResult[TaskT], ...]

    @property
    def progress(self) -> ExecutionProgress:
        return ExecutionProgress(len(self.results), len(self.results))


Operation = Callable[[TaskT, CancellationToken], RequestOutcome]
ProgressCallback = Callable[[ExecutionProgress], None]
BatchStartedCallback = Callable[[tuple[TaskT, ...]], None]
BatchFinishedCallback = Callable[[tuple[TaskResult[TaskT], ...]], None]


@dataclass(frozen=True, slots=True)
class _RunnerCallbacks(Generic[TaskT]):
    on_progress: ProgressCallback | None = None
    on_started: Callable[[TaskT], None] | None = None
    on_finished: Callable[[TaskResult[TaskT]], None] | None = None
    on_batch_started: BatchStartedCallback[TaskT] | None = None
    on_batch_finished: BatchFinishedCallback[TaskT] | None = None


@dataclass(slots=True)
class _ExecutionState(Generic[TaskT]):
    tasks: tuple[TaskT, ...]
    results: list[TaskResult[TaskT] | None]
    completed: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def create(cls, tasks: tuple[TaskT, ...]) -> _ExecutionState[TaskT]:
        return cls(tasks, [None] * len(tasks))

    def record(
        self,
        index: int,
        result: TaskResult[TaskT],
        callback: ProgressCallback | None,
    ) -> None:
        with self.lock:
            self.results[index] = result
            self.completed += 1
            if callback is None:
                return
            try:
                callback(ExecutionProgress(self.completed, len(self.tasks)))
            except Exception:
                # Observers do not own task execution.
                pass

    def report(self) -> ExecutionReport[TaskT]:
        return ExecutionReport(tuple(result for result in self.results if result is not None))


class ExecutionHandle(Generic[TaskT]):
    def __init__(self, token: CancellationToken) -> None:
        self._token = token
        self._done = threading.Event()
        self._start_gate = threading.Event()
        self._report: ExecutionReport[TaskT] | None = None

    @property
    def token(self) -> CancellationToken:
        return self._token

    @property
    def is_done(self) -> bool:
        return self._done.is_set()

    def stop(self) -> None:
        self._token.cancel()

    def activate(self) -> None:
        """Release a deferred execution after its owner has published state."""
        self._start_gate.set()

    def join(self, timeout: float | None = None) -> ExecutionReport[TaskT]:
        if not self._done.wait(timeout):
            raise TimeoutError("execution did not finish before the timeout")
        assert self._report is not None
        return self._report

    def _finish(self, report: ExecutionReport[TaskT]) -> None:
        self._report = report
        self._done.set()


class CooperativeTaskRunner:
    """Run bounded work while requiring active operations to honor a token."""

    def __init__(self, concurrency: int) -> None:
        if not 1 <= concurrency <= 100:
            raise ValueError("concurrency must be between 1 and 100")
        self.concurrency = concurrency

    def start(
        self,
        tasks: Sequence[TaskT],
        operation: Operation[TaskT],
        *,
        on_progress: ProgressCallback | None = None,
        on_started: Callable[[TaskT], None] | None = None,
        on_finished: Callable[[TaskResult[TaskT]], None] | None = None,
        on_batch_started: BatchStartedCallback[TaskT] | None = None,
        on_batch_finished: BatchFinishedCallback[TaskT] | None = None,
        token: CancellationToken | None = None,
        defer_start: bool = False,
    ) -> ExecutionHandle[TaskT]:
        task_tuple = tuple(tasks)
        cancellation = token or CancellationToken()
        handle: ExecutionHandle[TaskT] = ExecutionHandle(cancellation)
        callbacks = _RunnerCallbacks(
            on_progress,
            on_started,
            on_finished,
            on_batch_started,
            on_batch_finished,
        )
        coordinator = threading.Thread(
            target=self._coordinate,
            args=(task_tuple, operation, callbacks, handle),
            name="welearn-task-coordinator",
            daemon=False,
        )
        coordinator.start()
        if not defer_start:
            handle.activate()
        return handle

    def run(
        self,
        tasks: Sequence[TaskT],
        operation: Operation[TaskT],
        *,
        on_progress: ProgressCallback | None = None,
        on_started: Callable[[TaskT], None] | None = None,
        on_finished: Callable[[TaskResult[TaskT]], None] | None = None,
        on_batch_started: BatchStartedCallback[TaskT] | None = None,
        on_batch_finished: BatchFinishedCallback[TaskT] | None = None,
        token: CancellationToken | None = None,
        defer_start: bool = False,
    ) -> ExecutionReport[TaskT]:
        handle = self.start(
            tasks,
            operation,
            on_progress=on_progress,
            on_started=on_started,
            on_finished=on_finished,
            on_batch_started=on_batch_started,
            on_batch_finished=on_batch_finished,
            token=token,
            defer_start=defer_start,
        )
        if defer_start:
            handle.activate()
        return handle.join()

    def _coordinate(
        self,
        tasks: tuple[TaskT, ...],
        operation: Operation[TaskT],
        callbacks: _RunnerCallbacks[TaskT],
        handle: ExecutionHandle[TaskT],
    ) -> None:
        # The controller can defer the first callback until its session state
        # points at the new handle. This avoids losing the first batch in the
        # narrow window between starting a worker and publishing the UI state.
        handle._start_gate.wait()
        token = handle.token
        state = _ExecutionState.create(tasks)

        # Deliberately execute fixed waves. A task that finishes early does not
        # cause the next lesson to start until the whole current wave is done.
        # This keeps the UI and the platform request pattern understandable.
        for batch_start in range(0, len(tasks), self.concurrency):
            if token.is_cancelled:
                break
            batch = tuple(range(batch_start, min(batch_start + self.concurrency, len(tasks))))
            self._run_batch(batch, operation, token, callbacks, state)
            if token.is_cancelled:
                break

        self._complete_unstarted(callbacks, state)
        handle._finish(state.report())

    @staticmethod
    def _run_batch(
        indices: tuple[int, ...],
        operation: Operation[TaskT],
        token: CancellationToken,
        callbacks: _RunnerCallbacks[TaskT],
        state: _ExecutionState[TaskT],
    ) -> None:
        if callbacks.on_batch_started is not None:
            try:
                callbacks.on_batch_started(tuple(state.tasks[index] for index in indices))
            except Exception:
                pass

        workers = [
            threading.Thread(
                target=CooperativeTaskRunner._execute_one,
                args=(index, operation, token, callbacks, state),
                name=f"welearn-task-{index}",
                daemon=False,
            )
            for index in indices
        ]
        for thread in workers:
            thread.start()
        for thread in workers:
            thread.join()

        if callbacks.on_batch_finished is not None:
            results = tuple(state.results[index] for index in indices)
            try:
                callbacks.on_batch_finished(
                    tuple(result for result in results if result is not None)
                )
            except Exception:
                pass

    @staticmethod
    def _execute_one(
        index: int,
        operation: Operation[TaskT],
        token: CancellationToken,
        callbacks: _RunnerCallbacks[TaskT],
        state: _ExecutionState[TaskT],
    ) -> None:
        started = not token.is_cancelled
        if not started:
            outcome = RequestOutcome.cancelled("cancelled before start")
        else:
            if callbacks.on_started is not None:
                try:
                    callbacks.on_started(state.tasks[index])
                except Exception:
                    pass
            outcome = CooperativeTaskRunner._invoke_operation(state.tasks[index], operation, token)

        result = TaskResult(state.tasks[index], started, outcome)
        if callbacks.on_finished is not None:
            try:
                callbacks.on_finished(result)
            except Exception:
                pass
        state.record(index, result, callbacks.on_progress)

    @staticmethod
    def _invoke_operation(
        task: TaskT,
        operation: Operation[TaskT],
        token: CancellationToken,
    ) -> RequestOutcome:
        try:
            outcome = operation(task, token)
            if not isinstance(outcome, RequestOutcome):
                raise TypeError("operations must return RequestOutcome")
            return outcome
        except CancellationRequested:
            return RequestOutcome.cancelled("cancelled")
        except Exception as exc:
            return RequestOutcome.unknown(str(exc) or type(exc).__name__)

    @staticmethod
    def _complete_unstarted(
        callbacks: _RunnerCallbacks[TaskT],
        state: _ExecutionState[TaskT],
    ) -> None:
        for index, result in enumerate(state.results):
            if result is None:
                result = TaskResult(
                    state.tasks[index],
                    False,
                    RequestOutcome.cancelled("cancelled before start"),
                )
                if callbacks.on_finished is not None:
                    try:
                        callbacks.on_finished(result)
                    except Exception:
                        pass
                state.record(index, result, callbacks.on_progress)

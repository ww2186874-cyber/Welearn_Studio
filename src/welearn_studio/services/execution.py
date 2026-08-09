"""Cooperative task execution without forced worker termination."""

from __future__ import annotations

import threading
from dataclasses import dataclass
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
        coordinator = threading.Thread(
            target=self._coordinate,
            args=(
                task_tuple,
                operation,
                on_progress,
                on_started,
                on_finished,
                on_batch_started,
                on_batch_finished,
                handle,
            ),
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
        on_progress: ProgressCallback | None,
        on_started: Callable[[TaskT], None] | None,
        on_finished: Callable[[TaskResult[TaskT]], None] | None,
        on_batch_started: BatchStartedCallback[TaskT] | None,
        on_batch_finished: BatchFinishedCallback[TaskT] | None,
        handle: ExecutionHandle[TaskT],
    ) -> None:
        # The controller can defer the first callback until its session state
        # points at the new handle. This avoids losing the first batch in the
        # narrow window between starting a worker and publishing the UI state.
        handle._start_gate.wait()
        token = handle.token
        result_slots: list[TaskResult[TaskT] | None] = [None] * len(tasks)
        completed = 0
        lock = threading.Lock()

        def publish_progress(value: int) -> None:
            if on_progress is None:
                return
            try:
                on_progress(ExecutionProgress(value, len(tasks)))
            except Exception:
                # UI observers do not own task execution.
                return

        def run_batch(batch_indices: tuple[int, ...]) -> None:
            nonlocal completed
            if on_batch_started is not None:
                try:
                    on_batch_started(tuple(tasks[index] for index in batch_indices))
                except Exception:
                    pass

            batch_results: dict[int, TaskResult[TaskT]] = {}
            batch_lock = threading.Lock()

            def worker(index: int) -> None:
                nonlocal completed
                started = False
                if token.is_cancelled:
                    outcome = RequestOutcome.cancelled("cancelled before start")
                else:
                    started = True
                    if on_started is not None:
                        try:
                            on_started(tasks[index])
                        except Exception:
                            pass
                    try:
                        outcome = operation(tasks[index], token)
                        if not isinstance(outcome, RequestOutcome):
                            raise TypeError("operations must return RequestOutcome")
                    except CancellationRequested:
                        outcome = RequestOutcome.cancelled("cancelled")
                    except Exception as exc:
                        outcome = RequestOutcome.unknown(str(exc) or type(exc).__name__)

                result = TaskResult(tasks[index], started, outcome)
                if on_finished is not None:
                    try:
                        on_finished(result)
                    except Exception:
                        pass
                with batch_lock:
                    batch_results[index] = result
                with lock:
                    result_slots[index] = result
                    completed += 1
                    # Publish while holding the completion lock so observers see
                    # monotonically increasing values even when workers finish
                    # at nearly the same time.
                    publish_progress(completed)

            workers = [
                threading.Thread(
                    target=worker, args=(index,), name=f"welearn-task-{index}", daemon=False
                )
                for index in batch_indices
            ]
            for thread in workers:
                thread.start()
            for thread in workers:
                thread.join()

            if on_batch_finished is not None:
                ordered_results = tuple(
                    batch_results[index] for index in batch_indices if index in batch_results
                )
                try:
                    on_batch_finished(ordered_results)
                except Exception:
                    pass

        # Deliberately execute fixed waves. A task that finishes early does not
        # cause the next lesson to start until the whole current wave is done.
        # This keeps the UI and the platform request pattern understandable.
        for batch_start in range(0, len(tasks), self.concurrency):
            if token.is_cancelled:
                break
            batch = tuple(range(batch_start, min(batch_start + self.concurrency, len(tasks))))
            run_batch(batch)
            if token.is_cancelled:
                break

        for index, result in enumerate(result_slots):
            if result is None:
                result = TaskResult(
                    tasks[index], False, RequestOutcome.cancelled("cancelled before start")
                )
                result_slots[index] = result
                if on_finished is not None:
                    try:
                        on_finished(result)
                    except Exception:
                        pass
                completed += 1
                publish_progress(completed)

        report = ExecutionReport(tuple(result for result in result_slots if result is not None))
        handle._finish(report)

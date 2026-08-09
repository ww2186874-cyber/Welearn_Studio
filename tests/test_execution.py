import threading
import time
import unittest

from welearn_studio.domain.outcomes import OutcomeKind, RequestOutcome
from welearn_studio.services.execution import CooperativeTaskRunner


class ExecutionTests(unittest.TestCase):
    def test_results_keep_plan_order_and_exceptions_become_unknown(self) -> None:
        progress: list[tuple[int, int]] = []

        def operation(task: int, _token: object) -> RequestOutcome:
            if task == 2:
                raise RuntimeError("synthetic failure")
            if task == 1:
                return RequestOutcome.rejected("synthetic rejection")
            return RequestOutcome.accepted()

        report = CooperativeTaskRunner(2).run(
            [0, 1, 2],
            operation,
            on_progress=lambda value: progress.append((value.completed, value.planned)),
        )

        self.assertEqual([result.task for result in report.results], [0, 1, 2])
        self.assertEqual(
            [result.outcome.kind for result in report.results],
            [OutcomeKind.ACCEPTED, OutcomeKind.REJECTED, OutcomeKind.UNKNOWN],
        )
        self.assertIn("synthetic failure", report.results[2].outcome.detail)
        self.assertEqual(sorted(progress), [(1, 3), (2, 3), (3, 3)])

    def test_stop_interrupts_cooperative_wait_and_prevents_unstarted_work(self) -> None:
        entered = threading.Event()

        def operation(_task: int, token: object) -> RequestOutcome:
            entered.set()
            if token.wait(5):  # type: ignore[attr-defined]
                return RequestOutcome.cancelled("wait interrupted")
            return RequestOutcome.accepted()

        handle = CooperativeTaskRunner(1).start([0, 1, 2], operation)
        self.assertTrue(entered.wait(1))
        handle.stop()
        report = handle.join(1)

        self.assertTrue(handle.is_done)
        self.assertTrue(report.results[0].started)
        self.assertFalse(report.results[1].started)
        self.assertFalse(report.results[2].started)
        self.assertTrue(
            all(result.outcome.kind is OutcomeKind.CANCELLED for result in report.results)
        )
        self.assertEqual(report.progress.ratio, 1.0)

    def test_runner_honors_concurrency_limit(self) -> None:
        lock = threading.Lock()
        active = 0
        maximum = 0

        def operation(_task: int, _token: object) -> RequestOutcome:
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return RequestOutcome.accepted()

        CooperativeTaskRunner(3).run(list(range(9)), operation)
        self.assertEqual(maximum, 3)

    def test_lifecycle_callbacks_describe_only_active_tasks(self) -> None:
        lock = threading.Lock()
        active: set[int] = set()
        maximum = 0

        def started(task: int) -> None:
            nonlocal maximum
            with lock:
                active.add(task)
                maximum = max(maximum, len(active))

        def operation(_task: int, _token: object) -> RequestOutcome:
            time.sleep(0.02)
            return RequestOutcome.accepted()

        def finished(result: object) -> None:
            with lock:
                active.remove(result.task)  # type: ignore[attr-defined]

        CooperativeTaskRunner(3).run(
            list(range(9)),
            operation,
            on_started=started,
            on_finished=finished,
        )

        self.assertEqual(maximum, 3)
        self.assertFalse(active)

    def test_progress_callbacks_are_monotonic_under_concurrency(self) -> None:
        observed: list[int] = []

        def observe(progress: object) -> None:
            completed = progress.completed  # type: ignore[attr-defined]
            if completed == 1:
                time.sleep(0.03)
            observed.append(completed)

        CooperativeTaskRunner(2).run(
            [0, 1],
            lambda _task, _token: RequestOutcome.accepted(),
            on_progress=observe,
        )

        self.assertEqual(observed, [1, 2])

    def test_tasks_are_started_in_fixed_concurrency_batches(self) -> None:
        batches: list[tuple[int, ...]] = []

        CooperativeTaskRunner(2).run(
            list(range(5)),
            lambda _task, _token: RequestOutcome.accepted(),
            on_batch_started=lambda items: batches.append(tuple(items)),
        )

        self.assertEqual(batches, [(0, 1), (2, 3), (4,)])


if __name__ == "__main__":
    unittest.main()

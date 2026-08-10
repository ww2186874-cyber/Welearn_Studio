import unittest

from welearn_studio.adapters import LessonContext, WorkflowResult
from welearn_studio.application.task_execution import (
    MissingLessonContexts,
    NoRunnableLessons,
    TaskRunCallbacks,
    prepare_task_run,
    start_task_run,
)
from welearn_studio.domain import (
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
)


class StubRandom:
    def __init__(self, value: int) -> None:
        self.value = value
        self.calls: list[tuple[int, int]] = []

    def randint(self, start: int, end: int) -> int:
        self.calls.append((start, end))
        return self.value


class FakeTaskRemote:
    def __init__(self, *, fail_sco_id: str | None = None) -> None:
        self.fail_sco_id = fail_sco_id
        self.timed_calls: list[tuple[str, int]] = []
        self.homework_calls: list[tuple[str, int]] = []

    def run_timed_study(self, context, duration_seconds, _cancellation=None):
        self.timed_calls.append((context.sco_id, duration_seconds))
        return WorkflowResult(self._outcome(context.sco_id), ())

    def submit_homework(self, context, accuracy_percent, _cancellation=None):
        self.homework_calls.append((context.sco_id, accuracy_percent))
        return WorkflowResult(self._outcome(context.sco_id), ())

    def _outcome(self, sco_id: str) -> RequestOutcome:
        if sco_id == self.fail_sco_id:
            return RequestOutcome.unknown("synthetic failure")
        return RequestOutcome.accepted()


def course_data(count: int, parameters: TaskParameters):
    unit_id = "unit-1"
    lessons = tuple(
        LessonDefinition(LessonIdentity(f"lesson-{index}", f"小课 {index}"))
        for index in range(count)
    )
    catalog = CourseCatalog(
        CourseIdentity("course-1", "测试课程"),
        (UnitDefinition(UnitIdentity(unit_id, "第一单元"), lessons),),
    )
    lesson_ids = frozenset(lesson.identity.stable_id for lesson in lessons)
    settings = CourseSettings(
        parameters,
        CourseSelection(
            frozenset({unit_id}),
            (UnitLessonSelection(unit_id, lesson_ids),),
        ),
    )
    contexts = {
        (unit_id, lesson.identity.stable_id): LessonContext(
            "course-1",
            "learner-1",
            "class-1",
            lesson.identity.stable_id,
        )
        for lesson in lessons
    }
    return catalog, settings, contexts


class TaskPreparationTests(unittest.TestCase):
    def test_eighteen_lessons_discard_six_minutes_from_one_hour(self) -> None:
        rng = StubRandom(0)
        catalog, settings, contexts = course_data(
            18,
            TaskParameters(TaskMode.TIME_STUDY, 100, 1, 0, 33),
        )

        run = prepare_task_run(catalog, settings, contexts, rng=rng)

        self.assertEqual(rng.calls, [(0, 0)])
        self.assertEqual([task.duration_minutes for task in run.tasks], [3] * 18)
        self.assertEqual(run.platform_seconds, 54 * 60)
        self.assertEqual(run.discarded_remainder_seconds, 6 * 60)
        self.assertEqual(run.estimated_seconds, 3 * 60)
        self.assertEqual(run.concurrency, 33)

    def test_missing_remote_context_is_reported_before_start(self) -> None:
        catalog, settings, contexts = course_data(3, TaskParameters())
        contexts.pop(("unit-1", "lesson-1"))

        with self.assertRaises(MissingLessonContexts) as raised:
            prepare_task_run(catalog, settings, contexts)

        self.assertEqual(len(raised.exception.missing), 1)
        self.assertEqual(raised.exception.missing[0].lesson.stable_id, "lesson-1")

    def test_empty_selection_is_rejected_before_start(self) -> None:
        catalog, settings, contexts = course_data(2, TaskParameters())
        empty = CourseSettings(settings.parameters, CourseSelection())

        with self.assertRaises(NoRunnableLessons):
            prepare_task_run(catalog, empty, contexts)


class TaskRunTests(unittest.TestCase):
    def test_homework_runs_in_numbered_fixed_batches(self) -> None:
        catalog, settings, contexts = course_data(
            5,
            TaskParameters(TaskMode.HOMEWORK, 80, 1, 0, 2),
        )
        run = prepare_task_run(catalog, settings, contexts)
        remote = FakeTaskRemote()
        started_batches: list[tuple[int, tuple[str, ...]]] = []
        finished_batches: list[tuple[int, int]] = []

        report = start_task_run(
            run,
            remote,
            TaskRunCallbacks(
                on_batch_started=lambda number, tasks: started_batches.append(
                    (number, tuple(task.target.lesson.stable_id for task in tasks))
                ),
                on_batch_finished=lambda number, results: finished_batches.append(
                    (number, len(results))
                ),
            ),
        ).join(2)

        self.assertEqual(
            started_batches,
            [
                (1, ("lesson-0", "lesson-1")),
                (2, ("lesson-2", "lesson-3")),
                (3, ("lesson-4",)),
            ],
        )
        self.assertEqual(finished_batches, [(1, 2), (2, 2), (3, 1)])
        self.assertEqual(len(report.results), 5)
        self.assertTrue(
            all(result.outcome.kind is OutcomeKind.ACCEPTED for result in report.results)
        )
        self.assertEqual(remote.homework_calls, [(f"lesson-{index}", 80) for index in range(5)])

    def test_unknown_result_stops_before_the_next_batch(self) -> None:
        catalog, settings, contexts = course_data(
            5,
            TaskParameters(TaskMode.HOMEWORK, 100, 1, 0, 2),
        )
        run = prepare_task_run(catalog, settings, contexts)
        remote = FakeTaskRemote(fail_sco_id="lesson-0")
        halted: list[bool] = []

        report = start_task_run(
            run,
            remote,
            TaskRunCallbacks(on_halted=lambda: halted.append(True)),
        ).join(2)

        self.assertEqual(halted, [True])
        self.assertLessEqual(len(remote.homework_calls), 2)
        self.assertFalse(any(result.started for result in report.results[2:]))


if __name__ == "__main__":
    unittest.main()

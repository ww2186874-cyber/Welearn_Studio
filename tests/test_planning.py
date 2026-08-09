import unittest
from dataclasses import FrozenInstanceError

from welearn_studio.domain.models import LessonIdentity, LessonTarget, UnitIdentity
from welearn_studio.services.planning import (
    InsufficientStudyTime,
    build_parallel_schedule,
    create_time_study_plan,
    estimate_parallel_makespan,
)


class StubRandom:
    def __init__(self, value: int) -> None:
        self.value = value
        self.calls: list[tuple[int, int]] = []

    def randint(self, start: int, end: int) -> int:
        self.calls.append((start, end))
        if not start <= self.value <= end:
            raise AssertionError("stub value is outside requested bounds")
        return self.value


def targets(count: int) -> list[LessonTarget]:
    unit = UnitIdentity("unit-1", "Synthetic unit")
    return [
        LessonTarget(unit, LessonIdentity(f"lesson-{index}", f"Lesson {index}"))
        for index in range(count)
    ]


class PlanningTests(unittest.TestCase):
    def test_total_jitter_is_drawn_once_before_allocation(self) -> None:
        rng = StubRandom(5)
        plan = create_time_study_plan(
            targets(4), total_hours=1, random_minutes=7, concurrency=2, rng=rng
        )

        self.assertEqual(rng.calls, [(-7, 7)])
        self.assertEqual(plan.requested_total_minutes, 60)
        self.assertEqual(plan.jittered_total_minutes, 65)
        self.assertEqual([item.duration_minutes for item in plan.lessons], [16] * 4)
        self.assertEqual(plan.discarded_remainder_minutes, 1)
        self.assertEqual(plan.actual_platform_minutes, 64)
        self.assertEqual(plan.actual_platform_seconds, 64 * 60)
        self.assertEqual(plan.discarded_remainder_seconds, 60)
        self.assertEqual(plan.estimated_wall_minutes, 32)

    def test_zero_variation_still_uses_one_deterministic_draw(self) -> None:
        rng = StubRandom(0)
        create_time_study_plan(targets(2), total_hours=1, random_minutes=0, concurrency=1, rng=rng)
        self.assertEqual(rng.calls, [(0, 0)])

    def test_insufficient_total_is_rejected_instead_of_inflating_the_plan(self) -> None:
        rng = StubRandom(-100)
        with self.assertRaises(InsufficientStudyTime) as raised:
            create_time_study_plan(
                targets(70), total_hours=1, random_minutes=100, concurrency=10, rng=rng
            )

        self.assertEqual(raised.exception.available_minutes, 0)
        self.assertEqual(raised.exception.lesson_count, 70)

    def test_parallel_schedule_is_exact_and_deterministic(self) -> None:
        schedule = build_parallel_schedule([8, 7, 6, 5], 2)

        self.assertEqual(
            [(item.worker_index, item.start_minute, item.end_minute) for item in schedule],
            [(0, 0, 8), (1, 0, 7), (1, 7, 13), (0, 8, 13)],
        )
        self.assertEqual(estimate_parallel_makespan([8, 7, 6, 5], 2), 13)

    def test_plan_is_immutable_and_requires_work(self) -> None:
        plan = create_time_study_plan(
            targets(1), total_hours=1, random_minutes=0, concurrency=1, rng=StubRandom(0)
        )
        with self.assertRaises(FrozenInstanceError):
            plan.concurrency = 2  # type: ignore[misc]
        with self.assertRaises(ValueError):
            create_time_study_plan(
                [], total_hours=1, random_minutes=0, concurrency=1, rng=StubRandom(0)
            )


if __name__ == "__main__":
    unittest.main()

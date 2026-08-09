"""Immutable time-study planning and parallel estimates."""

from __future__ import annotations

import heapq
import random
from dataclasses import dataclass
from typing import Protocol, Sequence

from welearn_studio.domain.models import LessonTarget


class IntegerRandom(Protocol):
    def randint(self, start: int, end: int) -> int: ...


class InsufficientStudyTime(ValueError):
    """Raised when whole-minute timing cannot cover every selected lesson."""

    def __init__(self, available_minutes: int, lesson_count: int) -> None:
        self.available_minutes = available_minutes
        self.lesson_count = lesson_count
        super().__init__(
            f"{available_minutes} minutes is insufficient for "
            f"{lesson_count} selected lessons; at least {lesson_count} minutes are required"
        )


@dataclass(frozen=True, slots=True)
class PlannedLesson:
    target: LessonTarget
    duration_minutes: int

    def __post_init__(self) -> None:
        if self.duration_minutes < 1:
            raise ValueError("planned lessons must be at least one minute")


@dataclass(frozen=True, slots=True)
class ParallelAssignment:
    task_index: int
    worker_index: int
    start_minute: int
    end_minute: int


@dataclass(frozen=True, slots=True)
class TimeStudyPlan:
    lessons: tuple[PlannedLesson, ...]
    concurrency: int
    requested_total_minutes: int
    jitter_minutes: int
    jittered_total_minutes: int
    allocatable_total_minutes: int
    discarded_remainder_minutes: int
    schedule: tuple[ParallelAssignment, ...]
    estimated_wall_minutes: int

    @property
    def actual_platform_minutes(self) -> int:
        """Return the duration that will actually be sent to the platform.

        Allocation intentionally stays at whole-minute precision.  This is
        therefore the sum of the per-lesson values after integer division,
        rather than the requested total before its discarded remainder.
        """
        return sum(item.duration_minutes for item in self.lessons)

    @property
    def actual_platform_seconds(self) -> int:
        return self.actual_platform_minutes * 60

    @property
    def discarded_remainder_seconds(self) -> int:
        return self.discarded_remainder_minutes * 60


def build_parallel_schedule(
    durations_minutes: Sequence[int], concurrency: int
) -> tuple[ParallelAssignment, ...]:
    """Schedule in input order onto the next available worker."""
    if concurrency < 1:
        raise ValueError("concurrency must be at least one")
    if any(duration < 1 for duration in durations_minutes):
        raise ValueError("durations must be positive whole minutes")
    if not durations_minutes:
        return ()

    worker_count = min(concurrency, len(durations_minutes))
    workers = [(0, worker_index) for worker_index in range(worker_count)]
    heapq.heapify(workers)
    assignments: list[ParallelAssignment] = []
    for task_index, duration in enumerate(durations_minutes):
        start, worker_index = heapq.heappop(workers)
        end = start + duration
        assignments.append(ParallelAssignment(task_index, worker_index, start, end))
        heapq.heappush(workers, (end, worker_index))
    return tuple(assignments)


def estimate_parallel_makespan(durations_minutes: Sequence[int], concurrency: int) -> int:
    schedule = build_parallel_schedule(durations_minutes, concurrency)
    return max((assignment.end_minute for assignment in schedule), default=0)


def create_time_study_plan(
    selected_lessons: Sequence[LessonTarget],
    *,
    total_hours: int,
    random_minutes: int,
    concurrency: int,
    rng: IntegerRandom | None = None,
) -> TimeStudyPlan:
    if not selected_lessons:
        raise ValueError("at least one selected lesson is required")
    if not 1 <= total_hours <= 72:
        raise ValueError("total_hours must be between 1 and 72")
    if random_minutes < 0:
        raise ValueError("random_minutes must be non-negative")
    if not 1 <= concurrency <= 100:
        raise ValueError("concurrency must be between 1 and 100")

    source = rng if rng is not None else random.SystemRandom()
    jitter = source.randint(-random_minutes, random_minutes)
    requested_total = total_hours * 60
    jittered_total = max(0, requested_total + jitter)
    lesson_count = len(selected_lessons)
    if jittered_total < lesson_count:
        raise InsufficientStudyTime(jittered_total, lesson_count)
    allocatable_total = jittered_total
    per_lesson, remainder = divmod(allocatable_total, lesson_count)

    lessons = tuple(PlannedLesson(target, per_lesson) for target in selected_lessons)
    durations = tuple(item.duration_minutes for item in lessons)
    schedule = build_parallel_schedule(durations, concurrency)
    estimate = max((assignment.end_minute for assignment in schedule), default=0)
    return TimeStudyPlan(
        lessons=lessons,
        concurrency=concurrency,
        requested_total_minutes=requested_total,
        jitter_minutes=jitter,
        jittered_total_minutes=jittered_total,
        allocatable_total_minutes=allocatable_total,
        discarded_remainder_minutes=remainder,
        schedule=schedule,
        estimated_wall_minutes=estimate,
    )

"""Small immutable view models used at the UI boundary."""

from __future__ import annotations

from dataclasses import dataclass, field

ACCOUNT_STATES = (
    "pending",
    "signed_in",
    "homework",
    "time_study",
    "accepted",
    "unknown",
    "stopped",
    "error",
)


@dataclass(frozen=True, slots=True)
class AccountView:
    stable_id: str
    username: str
    nickname: str = ""
    state: str = "pending"
    progress: int = 0
    # Runtime timing is kept on the account row as well as the active workspace.
    # A zero estimate means that no reliable countdown is currently available.
    estimated_seconds: int = 0
    remaining_seconds: int = 0

    @property
    def display_name(self) -> str:
        return self.nickname.strip() or self.username


@dataclass(frozen=True, slots=True)
class CourseView:
    stable_id: str
    name: str


@dataclass(frozen=True, slots=True)
class LessonView:
    stable_id: str
    name: str
    runnable: bool = True


@dataclass(frozen=True, slots=True)
class UnitView:
    stable_id: str
    number: str
    name: str
    lessons: tuple[LessonView, ...] = ()
    selected_lesson_ids: frozenset[str] | None = None
    load_failed: bool = False

    @property
    def runnable_lessons(self) -> tuple[LessonView, ...]:
        return tuple(lesson for lesson in self.lessons if lesson.runnable)

    @property
    def runnable_count(self) -> int:
        return len(self.runnable_lessons)

    @property
    def effective_lesson_ids(self) -> frozenset[str]:
        if self.selected_lesson_ids is None:
            return frozenset(lesson.stable_id for lesson in self.runnable_lessons)
        return self.selected_lesson_ids


@dataclass(frozen=True, slots=True)
class PresetView:
    stable_id: str
    name: str


@dataclass(frozen=True, slots=True)
class RuntimeView:
    state: str = "stopped"
    completed: int = 0
    planned: int = 0
    accepted: int = 0
    rejected: int = 0
    unknown: int = 0
    cancelled: int = 0
    # Number of lesson requests currently occupying the worker pool. This is
    # deliberately separate from completed/planned: concurrency is a limit,
    # while completed/planned is the whole course task progress.
    active: int = 0
    concurrency: int = 0
    # Durations are kept in seconds so the presentation can show an exact
    # countdown without changing the task runner's progress contract.
    elapsed_seconds: int = 0
    estimated_seconds: int = 0
    remaining_seconds: int = 0
    # Effective platform time after whole-minute allocation and discarded
    # division remainder.  This differs from wall-clock estimate when
    # concurrency is greater than one.
    platform_seconds: int = 0


def format_duration(seconds: int) -> str:
    """Return a non-negative duration in a stable HH:MM:SS form."""
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, whole_seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}"


@dataclass(frozen=True, slots=True)
class LogEntry:
    timestamp: str
    severity: str
    scope: str
    message: str
    transient: bool = False


@dataclass(frozen=True, slots=True)
class CoursePageSnapshot:
    mode: str
    selected_unit_ids: frozenset[str]
    selected_lessons: dict[str, frozenset[str]] = field(default_factory=dict)
    accuracy: int = 85
    total_hours: int = 1
    random_minutes: int = 0
    concurrency: int = 1

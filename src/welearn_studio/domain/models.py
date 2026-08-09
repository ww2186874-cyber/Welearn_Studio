"""Core identities and immutable workspace state."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


def _required(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must not be empty")
    return cleaned


class RuntimeState(str, Enum):
    PENDING = "pending"
    SIGNED_IN = "signed_in"
    RUNNING_HOMEWORK = "running_homework"
    RUNNING_TIME_STUDY = "running_time_study"
    REQUEST_ACCEPTED = "request_accepted"
    PARTIALLY_UNKNOWN = "partially_unknown"
    STOPPED = "stopped"
    ERROR = "error"


class TaskMode(str, Enum):
    HOMEWORK = "homework"
    TIME_STUDY = "time_study"


class UnitLoadState(str, Enum):
    AVAILABLE = "available"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AccountIdentity:
    username: str
    nickname: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "username", _required(self.username, "username"))
        if self.nickname is not None:
            nickname = self.nickname.strip()
            object.__setattr__(self, "nickname", nickname or None)


@dataclass(frozen=True, slots=True)
class CourseIdentity:
    stable_id: str
    name: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "stable_id", _required(self.stable_id, "course stable_id"))
        object.__setattr__(self, "name", _required(self.name, "course name"))


@dataclass(frozen=True, slots=True)
class UnitIdentity:
    stable_id: str
    name: str
    number: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "stable_id", _required(self.stable_id, "unit stable_id"))
        object.__setattr__(self, "name", _required(self.name, "unit name"))
        if self.number is not None:
            number = self.number.strip()
            object.__setattr__(self, "number", number or None)


@dataclass(frozen=True, slots=True)
class LessonIdentity:
    stable_id: str
    name: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "stable_id", _required(self.stable_id, "lesson stable_id"))
        object.__setattr__(self, "name", _required(self.name, "lesson name"))


@dataclass(frozen=True, slots=True)
class LessonDefinition:
    identity: LessonIdentity
    runnable: bool = True


@dataclass(frozen=True, slots=True)
class UnitDefinition:
    identity: UnitIdentity
    lessons: tuple[LessonDefinition, ...] = ()
    load_state: UnitLoadState = UnitLoadState.AVAILABLE

    def __post_init__(self) -> None:
        object.__setattr__(self, "lessons", tuple(self.lessons))
        lesson_ids = [lesson.identity.stable_id for lesson in self.lessons]
        if len(lesson_ids) != len(set(lesson_ids)):
            raise ValueError("lesson stable IDs must be unique within a unit")

    @property
    def runnable_lessons(self) -> tuple[LessonDefinition, ...]:
        return tuple(lesson for lesson in self.lessons if lesson.runnable)


@dataclass(frozen=True, slots=True)
class CourseCatalog:
    identity: CourseIdentity
    units: tuple[UnitDefinition, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "units", tuple(self.units))
        unit_ids = [unit.identity.stable_id for unit in self.units]
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError("unit stable IDs must be unique within a course")


@dataclass(frozen=True, slots=True)
class LessonTarget:
    unit: UnitIdentity
    lesson: LessonIdentity


@dataclass(frozen=True, slots=True)
class AccountRuntime:
    identity: AccountIdentity
    state: RuntimeState = RuntimeState.PENDING
    completed_work: int = 0
    planned_work: int = 0
    message: str = ""

    def __post_init__(self) -> None:
        if self.completed_work < 0 or self.planned_work < 0:
            raise ValueError("work counts must be non-negative")
        if self.completed_work > self.planned_work:
            raise ValueError("completed work cannot exceed planned work")

    @property
    def progress(self) -> float:
        if self.planned_work == 0:
            return 0.0
        return self.completed_work / self.planned_work


@dataclass(frozen=True, slots=True)
class TaskParameters:
    mode: TaskMode = TaskMode.HOMEWORK
    accuracy_percent: int = 100
    total_hours: int = 1
    random_minutes: int = 0
    concurrency: int = 1

    def __post_init__(self) -> None:
        if not 0 <= self.accuracy_percent <= 100:
            raise ValueError("accuracy_percent must be between 0 and 100")
        if not 1 <= self.total_hours <= 72:
            raise ValueError("total_hours must be between 1 and 72")
        if self.random_minutes < 0:
            raise ValueError("random_minutes must be non-negative")
        if not 1 <= self.concurrency <= 100:
            raise ValueError("concurrency must be between 1 and 100")


@dataclass(frozen=True, slots=True)
class UnitLessonSelection:
    unit_id: str
    lesson_ids: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        object.__setattr__(self, "unit_id", _required(self.unit_id, "unit_id"))
        cleaned = frozenset(_required(value, "lesson_id") for value in self.lesson_ids)
        object.__setattr__(self, "lesson_ids", cleaned)


@dataclass(frozen=True, slots=True)
class CourseSelection:
    selected_unit_ids: frozenset[str] = field(default_factory=frozenset)
    lesson_selections: tuple[UnitLessonSelection, ...] = ()

    def __post_init__(self) -> None:
        selected = frozenset(
            _required(value, "selected unit ID") for value in self.selected_unit_ids
        )
        object.__setattr__(self, "selected_unit_ids", selected)
        object.__setattr__(self, "lesson_selections", tuple(self.lesson_selections))
        unit_ids = [entry.unit_id for entry in self.lesson_selections]
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError("lesson selections must contain each unit at most once")

    def lessons_for(self, unit_id: str) -> frozenset[str]:
        return next(
            (entry.lesson_ids for entry in self.lesson_selections if entry.unit_id == unit_id),
            frozenset(),
        )


@dataclass(frozen=True, slots=True)
class CourseSettings:
    parameters: TaskParameters = field(default_factory=TaskParameters)
    selection: CourseSelection = field(default_factory=CourseSelection)


@dataclass(frozen=True, slots=True)
class WorkspaceSettings:
    interface_scale_percent: int = 100
    last_account_file: str | None = None

    def __post_init__(self) -> None:
        if not 80 <= self.interface_scale_percent <= 200:
            raise ValueError("interface_scale_percent must be between 80 and 200")
        if self.last_account_file is not None:
            path = self.last_account_file.strip()
            object.__setattr__(self, "last_account_file", path or None)

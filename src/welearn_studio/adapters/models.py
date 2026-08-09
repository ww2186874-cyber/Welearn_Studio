"""Normalized values exposed by the remote adapter boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar

from welearn_studio.domain.models import CourseIdentity
from welearn_studio.domain.outcomes import OutcomeKind, RequestOutcome

ValueT = TypeVar("ValueT")
FormScalar = str | int | float


@dataclass(frozen=True, slots=True)
class ReadResult(Generic[ValueT]):
    """A normalized read value paired with its protocol outcome."""

    outcome: RequestOutcome
    value: ValueT | None = None
    issues: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        has_value = self.value is not None
        is_accepted = self.outcome.kind is OutcomeKind.ACCEPTED
        if has_value != is_accepted:
            raise ValueError("accepted reads require a value and other reads must not have one")


@dataclass(frozen=True, slots=True)
class CourseSummary:
    identity: CourseIdentity
    progress_percent: int | float | None = None


@dataclass(frozen=True, slots=True)
class CourseContext:
    learner_id: str
    class_id: str


@dataclass(frozen=True, slots=True)
class UnitSummary:
    index: int
    name: str
    visibility_marker: str | None = None


class Visibility(str, Enum):
    VISIBLE = "visible"
    HIDDEN = "hidden"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class LessonSummary:
    sco_id: str
    location: str | None
    visibility: Visibility
    completion_label: str | None = None

    @property
    def runnable(self) -> bool:
        return self.visibility is not Visibility.HIDDEN


@dataclass(frozen=True, slots=True)
class LessonContext:
    course_id: str
    learner_id: str
    class_id: str
    sco_id: str

    def __post_init__(self) -> None:
        for field_name in ("course_id", "learner_id", "class_id", "sco_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be empty")
            object.__setattr__(self, field_name, value.strip())


@dataclass(frozen=True, slots=True)
class ScoState:
    session_time: FormScalar
    total_time: FormScalar
    progress_measure: FormScalar
    score_scaled: FormScalar
    completion_status: FormScalar


@dataclass(frozen=True, slots=True)
class StepResult:
    name: str
    outcome: RequestOutcome


@dataclass(frozen=True, slots=True)
class WorkflowResult:
    outcome: RequestOutcome
    steps: tuple[StepResult, ...]
    completed_intervals: int = 0

    def __post_init__(self) -> None:
        if self.completed_intervals < 0:
            raise ValueError("completed_intervals must be non-negative")

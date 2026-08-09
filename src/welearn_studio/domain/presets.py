"""Immutable preset snapshots."""

from __future__ import annotations

from dataclasses import dataclass

from .models import CourseIdentity, TaskParameters


@dataclass(frozen=True, slots=True)
class ItemReference:
    stable_id: str
    exact_name: str

    def __post_init__(self) -> None:
        if not self.stable_id.strip() or not self.exact_name.strip():
            raise ValueError("preset references require a stable ID and exact name")


@dataclass(frozen=True, slots=True)
class PresetUnitSnapshot:
    unit: ItemReference
    selected: bool
    selected_lessons: tuple[ItemReference, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "selected_lessons", tuple(self.selected_lessons))


@dataclass(frozen=True, slots=True)
class PresetSnapshot:
    preset_id: str
    name: str
    course: CourseIdentity
    parameters: TaskParameters
    units: tuple[PresetUnitSnapshot, ...]

    def __post_init__(self) -> None:
        if not self.preset_id.strip():
            raise ValueError("preset_id must not be empty")
        if not self.name.strip():
            raise ValueError("preset name must not be empty")
        object.__setattr__(self, "units", tuple(self.units))

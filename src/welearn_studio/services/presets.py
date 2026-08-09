"""Capture and safely apply complete, course-bound presets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TypeVar

from welearn_studio.domain.models import (
    CourseCatalog,
    CourseIdentity,
    CourseSelection,
    LessonDefinition,
    TaskParameters,
    UnitDefinition,
    UnitLessonSelection,
)
from welearn_studio.domain.presets import ItemReference, PresetSnapshot, PresetUnitSnapshot


class CourseMismatchError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PresetApplication:
    parameters: TaskParameters
    selection: CourseSelection
    skipped_units: tuple[ItemReference, ...] = ()
    skipped_lessons: tuple[ItemReference, ...] = ()


def capture_preset(
    *,
    preset_id: str,
    name: str,
    catalog: CourseCatalog,
    parameters: TaskParameters,
    selection: CourseSelection,
) -> PresetSnapshot:
    units: list[PresetUnitSnapshot] = []
    for unit in catalog.units:
        lesson_ids = selection.lessons_for(unit.identity.stable_id)
        selected_lessons = tuple(
            ItemReference(lesson.identity.stable_id, lesson.identity.name)
            for lesson in unit.lessons
            if lesson.identity.stable_id in lesson_ids
        )
        units.append(
            PresetUnitSnapshot(
                unit=ItemReference(unit.identity.stable_id, unit.identity.name),
                selected=unit.identity.stable_id in selection.selected_unit_ids,
                selected_lessons=selected_lessons,
            )
        )
    return PresetSnapshot(preset_id, name, catalog.identity, parameters, tuple(units))


def apply_preset(snapshot: PresetSnapshot, catalog: CourseCatalog) -> PresetApplication:
    if not _same_course(snapshot.course, catalog.identity):
        raise CourseMismatchError("preset belongs to a different course")

    selected_unit_ids: set[str] = set()
    lesson_selections: list[UnitLessonSelection] = []
    skipped_units: list[ItemReference] = []
    skipped_lessons: list[ItemReference] = []
    available_units = list(catalog.units)
    used_unit_ids: set[str] = set()

    for saved_unit in snapshot.units:
        unit = _match_reference(
            saved_unit.unit,
            available_units,
            stable_id=lambda value: value.identity.stable_id,
            exact_name=lambda value: value.identity.name,
            excluded=used_unit_ids,
        )
        if unit is None:
            skipped_units.append(saved_unit.unit)
            skipped_lessons.extend(saved_unit.selected_lessons)
            continue
        used_unit_ids.add(unit.identity.stable_id)
        if saved_unit.selected:
            selected_unit_ids.add(unit.identity.stable_id)

        chosen_lessons: set[str] = set()
        used_lesson_ids: set[str] = set()
        runnable_lessons = list(unit.runnable_lessons)
        for saved_lesson in saved_unit.selected_lessons:
            lesson = _match_reference(
                saved_lesson,
                runnable_lessons,
                stable_id=lambda value: value.identity.stable_id,
                exact_name=lambda value: value.identity.name,
                excluded=used_lesson_ids,
            )
            if lesson is None:
                skipped_lessons.append(saved_lesson)
                continue
            used_lesson_ids.add(lesson.identity.stable_id)
            chosen_lessons.add(lesson.identity.stable_id)
        lesson_selections.append(
            UnitLessonSelection(unit.identity.stable_id, frozenset(chosen_lessons))
        )

    selection = CourseSelection(frozenset(selected_unit_ids), tuple(lesson_selections))
    return PresetApplication(
        parameters=snapshot.parameters,
        selection=selection,
        skipped_units=tuple(skipped_units),
        skipped_lessons=tuple(skipped_lessons),
    )


def _same_course(saved: CourseIdentity, current: CourseIdentity) -> bool:
    return saved.stable_id == current.stable_id or saved.name == current.name


MatchT = TypeVar("MatchT", UnitDefinition, LessonDefinition)


def _match_reference(
    reference: ItemReference,
    candidates: list[MatchT],
    *,
    stable_id: Callable[[MatchT], str],
    exact_name: Callable[[MatchT], str],
    excluded: set[str],
) -> MatchT | None:
    id_matches = [
        candidate
        for candidate in candidates
        if stable_id(candidate) not in excluded and stable_id(candidate) == reference.stable_id
    ]
    if len(id_matches) == 1:
        return id_matches[0]
    name_matches = [
        candidate
        for candidate in candidates
        if stable_id(candidate) not in excluded and exact_name(candidate) == reference.exact_name
    ]
    return name_matches[0] if len(name_matches) == 1 else None

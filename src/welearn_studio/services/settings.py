"""Atomic JSON storage for non-secret workspace state and presets."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

from welearn_studio.domain.models import (
    AccountIdentity,
    CourseIdentity,
    CourseSelection,
    CourseSettings,
    TaskMode,
    TaskParameters,
    UnitLessonSelection,
    WorkspaceSettings,
)
from welearn_studio.domain.presets import ItemReference, PresetSnapshot, PresetUnitSnapshot


class SettingsError(RuntimeError):
    pass


def hashed_key(namespace: str, stable_value: str) -> str:
    normalized = stable_value.strip()
    if not normalized:
        raise ValueError("stable key values must not be empty")
    payload = f"welearn-studio:{namespace}:{normalized}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class JsonSettingsStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._data = self._read()

    def load_workspace(self) -> WorkspaceSettings:
        with self._lock:
            raw = self._data.get("workspace", {})
            try:
                return WorkspaceSettings(
                    interface_scale_percent=int(raw.get("interface_scale_percent", 100)),
                    last_account_file=raw.get("last_account_file"),
                )
            except (TypeError, ValueError) as exc:
                raise SettingsError("workspace settings are invalid") from exc

    def save_workspace(self, settings: WorkspaceSettings) -> None:
        with self._lock:
            self._data["workspace"] = {
                "interface_scale_percent": settings.interface_scale_percent,
                "last_account_file": settings.last_account_file,
            }
            self._write()

    def load_selected_course(self, account: AccountIdentity) -> str | None:
        with self._lock:
            account_data = self._account_data(account, create=False)
            value = account_data.get("selected_course_id") if account_data else None
            return value if isinstance(value, str) and value else None

    def save_selected_course(self, account: AccountIdentity, course_id: str | None) -> None:
        if course_id is not None and not course_id.strip():
            raise ValueError("course_id must not be empty")
        with self._lock:
            account_data = self._account_data(account, create=True)
            account_data["selected_course_id"] = course_id
            self._write()

    def load_course(
        self, account: AccountIdentity, course: CourseIdentity
    ) -> CourseSettings | None:
        with self._lock:
            account_data = self._account_data(account, create=False)
            if not account_data:
                return None
            courses = account_data.get("courses", {})
            if not isinstance(courses, dict):
                raise SettingsError("course settings are invalid")
            raw = courses.get(self._course_key(course))
            if raw is None:
                return None
            try:
                return _course_settings_from_dict(raw)
            except (KeyError, TypeError, ValueError) as exc:
                raise SettingsError("course settings are invalid") from exc

    def save_course(
        self, account: AccountIdentity, course: CourseIdentity, settings: CourseSettings
    ) -> None:
        with self._lock:
            account_data = self._account_data(account, create=True)
            courses = account_data.setdefault("courses", {})
            courses[self._course_key(course)] = _course_settings_to_dict(settings)
            self._write()

    def list_presets(self, course: CourseIdentity) -> tuple[PresetSnapshot, ...]:
        with self._lock:
            raw_presets = self._data.get("presets", {}).get(self._course_key(course), {})
            if not isinstance(raw_presets, dict):
                raise SettingsError("preset settings are invalid")
            snapshots: list[PresetSnapshot] = []
            try:
                for raw in raw_presets.values():
                    snapshots.append(_preset_from_dict(raw))
            except (KeyError, TypeError, ValueError) as exc:
                raise SettingsError("preset settings are invalid") from exc
            return tuple(
                sorted(snapshots, key=lambda value: (value.name.casefold(), value.preset_id))
            )

    def save_preset(self, snapshot: PresetSnapshot) -> None:
        with self._lock:
            course_presets = self._data.setdefault("presets", {}).setdefault(
                self._course_key(snapshot.course), {}
            )
            course_presets[snapshot.preset_id] = _preset_to_dict(snapshot)
            self._write()

    def rename_preset(self, course: CourseIdentity, preset_id: str, new_name: str) -> None:
        if not new_name.strip():
            raise ValueError("preset name must not be empty")
        with self._lock:
            course_presets = self._data.get("presets", {}).get(self._course_key(course), {})
            if preset_id not in course_presets:
                raise KeyError(preset_id)
            snapshot = _preset_from_dict(course_presets[preset_id])
            course_presets[preset_id] = _preset_to_dict(replace(snapshot, name=new_name.strip()))
            self._write()

    def delete_preset(self, course: CourseIdentity, preset_id: str) -> bool:
        with self._lock:
            course_presets = self._data.get("presets", {}).get(self._course_key(course), {})
            if preset_id not in course_presets:
                return False
            del course_presets[preset_id]
            self._write()
            return True

    def _account_data(self, account: AccountIdentity, *, create: bool) -> dict[str, Any]:
        accounts = self._data.setdefault("accounts", {})
        key = hashed_key("account", account.username.casefold())
        if create:
            return accounts.setdefault(key, {"selected_course_id": None, "courses": {}})
        value = accounts.get(key)
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _course_key(course: CourseIdentity) -> str:
        return hashed_key("course", course.stable_id)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "workspace": {}, "accounts": {}, "presets": {}}
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                data = json.load(stream)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SettingsError(f"cannot read settings: {exc}") from exc
        if not isinstance(data, dict) or data.get("version") != 1:
            raise SettingsError("unsupported or invalid settings document")
        for key in ("workspace", "accounts", "presets"):
            if key not in data or not isinstance(data[key], dict):
                raise SettingsError(f"settings field {key!r} must be an object")
        return data

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(self._data, stream, ensure_ascii=True, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except Exception:
            try:
                temporary.unlink(missing_ok=True)
            finally:
                raise


def _parameters_to_dict(parameters: TaskParameters) -> dict[str, Any]:
    return {
        "mode": parameters.mode.value,
        "accuracy_percent": parameters.accuracy_percent,
        "total_hours": parameters.total_hours,
        "random_minutes": parameters.random_minutes,
        "concurrency": parameters.concurrency,
    }


def _parameters_from_dict(raw: dict[str, Any]) -> TaskParameters:
    return TaskParameters(
        mode=TaskMode(raw["mode"]),
        accuracy_percent=int(raw["accuracy_percent"]),
        total_hours=int(raw["total_hours"]),
        random_minutes=int(raw["random_minutes"]),
        concurrency=int(raw["concurrency"]),
    )


def _course_settings_to_dict(settings: CourseSettings) -> dict[str, Any]:
    lesson_selections = sorted(
        settings.selection.lesson_selections, key=lambda value: value.unit_id
    )
    return {
        "parameters": _parameters_to_dict(settings.parameters),
        "selected_unit_ids": sorted(settings.selection.selected_unit_ids),
        "lesson_selections": [
            {"unit_id": item.unit_id, "lesson_ids": sorted(item.lesson_ids)}
            for item in lesson_selections
        ],
    }


def _course_settings_from_dict(raw: dict[str, Any]) -> CourseSettings:
    selection = CourseSelection(
        selected_unit_ids=frozenset(str(value) for value in raw["selected_unit_ids"]),
        lesson_selections=tuple(
            UnitLessonSelection(
                str(value["unit_id"]), frozenset(str(item) for item in value["lesson_ids"])
            )
            for value in raw["lesson_selections"]
        ),
    )
    return CourseSettings(_parameters_from_dict(raw["parameters"]), selection)


def _reference_to_dict(reference: ItemReference) -> dict[str, str]:
    return {"stable_id": reference.stable_id, "exact_name": reference.exact_name}


def _reference_from_dict(raw: dict[str, Any]) -> ItemReference:
    return ItemReference(str(raw["stable_id"]), str(raw["exact_name"]))


def _preset_to_dict(snapshot: PresetSnapshot) -> dict[str, Any]:
    return {
        "preset_id": snapshot.preset_id,
        "name": snapshot.name,
        "course": {"stable_id": snapshot.course.stable_id, "name": snapshot.course.name},
        "parameters": _parameters_to_dict(snapshot.parameters),
        "units": [
            {
                "unit": _reference_to_dict(unit.unit),
                "selected": unit.selected,
                "selected_lessons": [
                    _reference_to_dict(lesson) for lesson in unit.selected_lessons
                ],
            }
            for unit in snapshot.units
        ],
    }


def _preset_from_dict(raw: dict[str, Any]) -> PresetSnapshot:
    course_raw = raw["course"]
    return PresetSnapshot(
        preset_id=str(raw["preset_id"]),
        name=str(raw["name"]),
        course=CourseIdentity(str(course_raw["stable_id"]), str(course_raw["name"])),
        parameters=_parameters_from_dict(raw["parameters"]),
        units=tuple(
            PresetUnitSnapshot(
                unit=_reference_from_dict(unit["unit"]),
                selected=bool(unit["selected"]),
                selected_lessons=tuple(
                    _reference_from_dict(lesson) for lesson in unit["selected_lessons"]
                ),
            )
            for unit in raw["units"]
        ),
    )

"""Explicit terminal outcomes for remote requests."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class OutcomeKind(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class RequestOutcome:
    kind: OutcomeKind
    detail: str = ""

    @property
    def endpoint_accepted(self) -> bool:
        return self.kind is OutcomeKind.ACCEPTED

    @classmethod
    def accepted(cls, detail: str = "") -> RequestOutcome:
        return cls(OutcomeKind.ACCEPTED, detail)

    @classmethod
    def rejected(cls, detail: str = "") -> RequestOutcome:
        return cls(OutcomeKind.REJECTED, detail)

    @classmethod
    def unknown(cls, detail: str = "") -> RequestOutcome:
        return cls(OutcomeKind.UNKNOWN, detail)

    @classmethod
    def cancelled(cls, detail: str = "") -> RequestOutcome:
        return cls(OutcomeKind.CANCELLED, detail)

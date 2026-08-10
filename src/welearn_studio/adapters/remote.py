"""Concrete clean-room client for the documented remote protocol."""

from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from numbers import Real
from typing import Any, Callable, Mapping, Protocol, TypeVar
from urllib.parse import parse_qs, urlencode, urlsplit

from welearn_studio.domain.models import CourseIdentity
from welearn_studio.domain.outcomes import OutcomeKind, RequestOutcome

from .models import (
    CourseContext,
    CourseSummary,
    FormScalar,
    LessonContext,
    LessonSummary,
    ReadResult,
    ScoState,
    StepResult,
    UnitSummary,
    Visibility,
    WorkflowResult,
)
from .password_wire import encode_password_for_wire
from .transport import HttpResponse, HttpTransport, RequestsSessionTransport

APPLICATION_ORIGIN = "https://welearn.sflep.com"
IDENTITY_ORIGIN = "https://sso.sflep.com"
PRELOGIN_PATH = "/user/prelogin.aspx"
SCO_PATH = "/Ajax/SCO.aspx"
LOGIN_RETURN_URL = "http://welearn.sflep.com/user/loginredirect.aspx"

ValueT = TypeVar("ValueT")


class Cancellation(Protocol):
    @property
    def is_cancelled(self) -> bool: ...

    def wait(self, timeout: float | None = None) -> bool: ...


Waiter = Callable[[Cancellation | None, float], bool]


@dataclass(frozen=True, slots=True)
class _HttpAttempt:
    response: HttpResponse | None
    outcome: RequestOutcome | None


class _JsonScriptCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.documents: list[str] = []
        self._parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script" or self._parts is not None:
            return
        attributes = {name.lower(): value for name, value in attrs}
        media_type = (attributes.get("type") or "").split(";", 1)[0].strip().lower()
        if media_type == "application/json":
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._parts is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._parts is not None:
            self.documents.append("".join(self._parts))
            self._parts = None


def _is_cancelled(cancellation: Cancellation | None) -> bool:
    return cancellation is not None and cancellation.is_cancelled


def _nonempty_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _identifier_text(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    return _nonempty_text(value)


def _form_scalar(value: object) -> FormScalar | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    return value


def _nonnegative_seconds(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 and value.is_integer() else None
    if isinstance(value, str):
        cleaned = value.strip()
        if re.fullmatch(r"[0-9]+", cleaned):
            return int(cleaned)
    return None


def _walk_mappings(value: object):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_mappings(child)


def _extract_course_context(html: str) -> CourseContext | None:
    collector = _JsonScriptCollector()
    try:
        collector.feed(html)
        collector.close()
    except Exception:
        return None

    candidates: set[tuple[str, str]] = set()
    for source in collector.documents:
        try:
            document = json.loads(source)
        except (TypeError, ValueError):
            continue
        for mapping in _walk_mappings(document):
            learner_id = _identifier_text(mapping.get("uid"))
            class_id = _identifier_text(mapping.get("classid"))
            if learner_id is None or class_id is None:
                continue
            if re.fullmatch(r"[0-9]+", learner_id) and re.fullmatch(r"[A-Za-z0-9_]+", class_id):
                candidates.add((learner_id, class_id))

    # The current course page also emits the same values inside a regular
    # JavaScript object instead of an application/json script. Only accept a
    # unique pair so unrelated or ambiguous page text cannot be combined.
    learner_ids = {
        quoted or bare
        for quoted, bare in re.findall(r"""["']uid["']\s*:\s*(?:["']([0-9]+)["']|([0-9]+))""", html)
    }
    class_ids = {
        double_quoted or single_quoted
        for double_quoted, single_quoted in re.findall(
            r"""["']classid["']\s*:\s*(?:"([A-Za-z0-9_]+)"|'([A-Za-z0-9_]+)')""",
            html,
        )
    }
    if len(learner_ids) == 1 and len(class_ids) == 1:
        candidates.add((next(iter(learner_ids)), next(iter(class_ids))))

    if len(candidates) != 1:
        return None
    learner_id, class_id = candidates.pop()
    return CourseContext(learner_id=learner_id, class_id=class_id)


def _authorization_callback(response_url: str) -> str | None:
    """Extract the callback issued by the identity service.

    The live service finishes its anonymous redirect chain at ``transfer.html``
    and puts the callback URL in a nested ``returnUrl`` query parameter.  Some
    older responses exposed the callback directly, so both shapes are accepted.
    """
    pending = [response_url]
    visited: set[str] = set()
    while pending and len(visited) < 4:
        candidate = pending.pop(0)
        if candidate in visited:
            continue
        visited.add(candidate)
        parsed = urlsplit(candidate)
        query = parse_qs(parsed.query, keep_blank_values=True)
        challenge = query.get("code_challenge", [])
        state = query.get("state", [])
        if len(challenge) == 1 and challenge[0] and len(state) == 1 and state[0]:
            if not parsed.path:
                return None
            return f"{parsed.path}?{parsed.query}"
        for name, values in query.items():
            if name.casefold() != "returnurl":
                continue
            pending.extend(value for value in values if value)
    return None


class WeLearnRemoteClient:
    """One authenticated, cookie-preserving remote client for one account."""

    def __init__(
        self,
        transport: HttpTransport | None = None,
        *,
        timeout: float | tuple[float, float] = (10.0, 30.0),
        now_milliseconds: Callable[[], int] | None = None,
        waiter: Waiter | None = None,
    ) -> None:
        self._transport = transport or RequestsSessionTransport()
        self._timeout = timeout
        self._now_milliseconds = now_milliseconds
        self._waiter = waiter or self._default_waiter

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> WeLearnRemoteClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _default_waiter(cancellation: Cancellation | None, seconds: float) -> bool:
        if cancellation is not None:
            return cancellation.wait(seconds)
        time.sleep(seconds)
        return False

    def _request(
        self,
        method: str,
        url: str,
        *,
        cancellation: Cancellation | None,
        params: Mapping[str, object] | None = None,
        data: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
        allow_redirects: bool = False,
    ) -> _HttpAttempt:
        if _is_cancelled(cancellation):
            return _HttpAttempt(None, RequestOutcome.cancelled("cancelled before request"))
        try:
            response = self._transport.request(
                method,
                url,
                params=params,
                data=data,
                headers=headers,
                allow_redirects=allow_redirects,
                timeout=self._timeout,
            )
        except Exception:
            return _HttpAttempt(None, RequestOutcome.unknown("transport request failed"))

        status = getattr(response, "status_code", None)
        if isinstance(status, bool) or not isinstance(status, int):
            return _HttpAttempt(None, RequestOutcome.unknown("transport returned malformed status"))
        if not 200 <= status < 300:
            return _HttpAttempt(None, RequestOutcome.rejected(f"server returned HTTP {status}"))
        return _HttpAttempt(response, None)

    @staticmethod
    def _json_object(response: HttpResponse) -> dict[str, Any] | None:
        try:
            value = response.json()
        except Exception:
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _failed_read(outcome: RequestOutcome) -> ReadResult[Any]:
        return ReadResult(outcome)

    @staticmethod
    def _ret_outcome(response: HttpResponse) -> RequestOutcome:
        envelope = WeLearnRemoteClient._json_object(response)
        if envelope is None or "ret" not in envelope:
            return RequestOutcome.unknown("write response was not recognized")
        value = envelope["ret"]
        if isinstance(value, bool) or value is None:
            return RequestOutcome.unknown("write result was malformed")
        if isinstance(value, Real):
            if value == 0:
                return RequestOutcome.accepted("write accepted")
            return RequestOutcome.rejected("write rejected")
        if isinstance(value, str):
            if value == "0":
                return RequestOutcome.accepted("write accepted")
            if value:
                return RequestOutcome.rejected("write rejected")
        return RequestOutcome.unknown("write result was malformed")

    def login(
        self,
        account: str,
        password: str,
        cancellation: Cancellation | None = None,
    ) -> RequestOutcome:
        prelogin_params = {
            "loginret": LOGIN_RETURN_URL,
        }
        initial = self._request(
            "GET",
            f"{APPLICATION_ORIGIN}{PRELOGIN_PATH}",
            params=prelogin_params,
            allow_redirects=True,
            cancellation=cancellation,
        )
        if initial.outcome is not None:
            return initial.outcome
        assert initial.response is not None

        final_url = getattr(initial.response, "url", "")
        if not isinstance(final_url, str):
            return RequestOutcome.unknown("authorization redirect was malformed")
        callback_path = _authorization_callback(final_url)
        if callback_path is None:
            return RequestOutcome.unknown("authorization redirect was incomplete")

        if _is_cancelled(cancellation):
            return RequestOutcome.cancelled("cancelled before credential submission")

        wire_value = encode_password_for_wire(
            password,
            now_milliseconds=self._now_milliseconds,
        )
        credentials = self._request(
            "POST",
            f"{IDENTITY_ORIGIN}/idsvr/account/login",
            data={
                "account": account,
                "pwd": wire_value.encoded,
                "ts": str(wire_value.timestamp),
                "rturl": callback_path,
            },
            cancellation=cancellation,
        )
        if credentials.outcome is not None:
            return credentials.outcome
        assert credentials.response is not None
        envelope = self._json_object(credentials.response)
        if envelope is None:
            return RequestOutcome.unknown("credential response was malformed")
        code = envelope.get("code")
        if isinstance(code, bool) or not isinstance(code, int):
            return RequestOutcome.unknown("credential response was not recognized")
        if code == 1:
            return RequestOutcome.rejected("credentials were rejected")
        if code != 0:
            return RequestOutcome.unknown("credential response was not recognized")

        finalization = self._request(
            "GET",
            f"{APPLICATION_ORIGIN}{PRELOGIN_PATH}",
            params=prelogin_params,
            allow_redirects=True,
            cancellation=cancellation,
        )
        if finalization.outcome is not None:
            return finalization.outcome
        return RequestOutcome.accepted("login accepted")

    def list_courses(
        self, cancellation: Cancellation | None = None
    ) -> ReadResult[tuple[CourseSummary, ...]]:
        attempt = self._request(
            "GET",
            f"{APPLICATION_ORIGIN}/ajax/authCourse.aspx",
            params={"action": "gmc"},
            headers={"Referer": f"{APPLICATION_ORIGIN}/2019/student/index.aspx"},
            cancellation=cancellation,
        )
        if attempt.outcome is not None:
            return self._failed_read(attempt.outcome)
        assert attempt.response is not None
        envelope = self._json_object(attempt.response)
        items = envelope.get("clist") if envelope is not None else None
        if not isinstance(items, list):
            return self._failed_read(RequestOutcome.unknown("course list was malformed"))

        courses: list[CourseSummary] = []
        for item in items:
            if not isinstance(item, dict):
                return self._failed_read(RequestOutcome.unknown("course item was malformed"))
            course_id = _identifier_text(item.get("cid"))
            name = _nonempty_text(item.get("name"))
            if course_id is None or name is None:
                return self._failed_read(RequestOutcome.unknown("course identity was missing"))
            progress = item.get("per")
            if isinstance(progress, bool) or not isinstance(progress, (int, float)):
                progress = None
            courses.append(CourseSummary(CourseIdentity(course_id, name), progress))

        return ReadResult(RequestOutcome.accepted("course list read"), tuple(courses))

    def bootstrap_course(
        self,
        course_id: str,
        cancellation: Cancellation | None = None,
    ) -> ReadResult[CourseContext]:
        attempt = self._request(
            "GET",
            f"{APPLICATION_ORIGIN}/student/course_info.aspx",
            params={"cid": course_id},
            cancellation=cancellation,
        )
        if attempt.outcome is not None:
            return self._failed_read(attempt.outcome)
        assert attempt.response is not None
        context = _extract_course_context(attempt.response.text)
        if context is None:
            return self._failed_read(RequestOutcome.unknown("course context was not recognized"))
        return ReadResult(RequestOutcome.accepted("course context read"), context)

    def list_units(
        self,
        course_id: str,
        learner_id: str,
        cancellation: Cancellation | None = None,
    ) -> ReadResult[tuple[UnitSummary, ...]]:
        attempt = self._request(
            "GET",
            f"{APPLICATION_ORIGIN}/ajax/StudyStat.aspx",
            params={"action": "courseunits", "cid": course_id, "uid": learner_id},
            headers={"Referer": f"{APPLICATION_ORIGIN}/2019/student/course_info.aspx"},
            cancellation=cancellation,
        )
        if attempt.outcome is not None:
            return self._failed_read(attempt.outcome)
        assert attempt.response is not None
        envelope = self._json_object(attempt.response)
        items = envelope.get("info") if envelope is not None else None
        if not isinstance(items, list):
            return self._failed_read(RequestOutcome.unknown("unit list was malformed"))

        units: list[UnitSummary] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                return self._failed_read(RequestOutcome.unknown("unit item was malformed"))
            name = _nonempty_text(item.get("name"))
            if name is None:
                return self._failed_read(RequestOutcome.unknown("unit identity was missing"))
            marker = item.get("visible")
            units.append(UnitSummary(index, name, marker if isinstance(marker, str) else None))

        return ReadResult(RequestOutcome.accepted("unit list read"), tuple(units))

    def list_lessons(
        self,
        course_id: str,
        learner_id: str,
        class_id: str,
        unit_index: int,
        cancellation: Cancellation | None = None,
    ) -> ReadResult[tuple[LessonSummary, ...]]:
        if isinstance(unit_index, bool) or not isinstance(unit_index, int) or unit_index < 0:
            raise ValueError("unit_index must be a non-negative integer")
        attempt = self._request(
            "GET",
            f"{APPLICATION_ORIGIN}/ajax/StudyStat.aspx",
            params={
                "action": "scoLeaves",
                "cid": course_id,
                "uid": learner_id,
                "unitidx": unit_index,
                "classid": class_id,
            },
            headers={
                "Referer": (
                    f"{APPLICATION_ORIGIN}/2019/student/course_info.aspx?"
                    f"{urlencode({'cid': course_id})}"
                )
            },
            cancellation=cancellation,
        )
        if attempt.outcome is not None:
            return self._failed_read(attempt.outcome)
        assert attempt.response is not None
        envelope = self._json_object(attempt.response)
        items = envelope.get("info") if envelope is not None else None
        if not isinstance(items, list):
            return self._failed_read(RequestOutcome.unknown("lesson list was malformed"))

        lessons: list[LessonSummary] = []
        issues: list[str] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                issues.append(f"lesson item {index + 1} was skipped because it was malformed")
                continue
            sco_id = _identifier_text(item.get("id"))
            if sco_id is None:
                issues.append(f"lesson item {index + 1} was skipped because its ID was missing")
                continue
            location = _nonempty_text(item.get("location"))
            marker = item.get("isvisible")
            if marker == "false":
                visibility = Visibility.HIDDEN
            elif marker == "true":
                visibility = Visibility.VISIBLE
            else:
                visibility = Visibility.UNKNOWN
            completion = item.get("iscomplete")
            lessons.append(
                LessonSummary(
                    sco_id=sco_id,
                    location=location,
                    visibility=visibility,
                    completion_label=completion if isinstance(completion, str) else None,
                )
            )

        return ReadResult(
            RequestOutcome.accepted("lesson list read"), tuple(lessons), tuple(issues)
        )

    @staticmethod
    def _sco_referer(context: LessonContext) -> str:
        query = urlencode(
            {
                "cid": context.course_id,
                "classid": context.class_id,
                "sco": context.sco_id,
            }
        )
        return f"{APPLICATION_ORIGIN}/Student/StudyCourse.aspx?{query}"

    def _sco_request(
        self,
        context: LessonContext,
        data: Mapping[str, object],
        cancellation: Cancellation | None,
    ) -> _HttpAttempt:
        payload: dict[str, object] = {
            "nocache": format(random.random(), ".17g"),
            **data,
        }
        return self._request(
            "POST",
            f"{APPLICATION_ORIGIN}{SCO_PATH}",
            data=payload,
            headers={"Referer": self._sco_referer(context)},
            cancellation=cancellation,
        )

    @staticmethod
    def _start_fields(context: LessonContext) -> dict[str, object]:
        return {
            "action": "startsco160928",
            "cid": context.course_id,
            "scoid": context.sco_id,
            "uid": context.learner_id,
            "classid": context.class_id,
            "tid": "-1",
        }

    def submit_homework(
        self,
        context: LessonContext,
        accuracy_percent: int,
        cancellation: Cancellation | None = None,
    ) -> WorkflowResult:
        if (
            isinstance(accuracy_percent, bool)
            or not isinstance(accuracy_percent, int)
            or not 0 <= accuracy_percent <= 100
        ):
            raise ValueError("accuracy_percent must be an integer from 0 through 100")

        steps: list[StepResult] = []
        start = self._sco_request(context, self._start_fields(context), cancellation)
        start_outcome = start.outcome
        if start_outcome is None:
            assert start.response is not None
            start_outcome = self._ret_outcome(start.response)
        steps.append(StepResult("start", start_outcome))
        if start_outcome.kind is not OutcomeKind.ACCEPTED:
            return WorkflowResult(start_outcome, tuple(steps))

        state = {
            "cmi": {
                "completion_status": "completed",
                "interactions": [],
                "launch_data": "",
                "progress_measure": "1",
                "score": {"scaled": str(accuracy_percent), "raw": "100"},
                "session_time": "0",
                "success_status": "unknown",
                "total_time": "0",
                "mode": "normal",
            },
            "adl": {"data": []},
            "cci": {"data": []},
            "retry_count": "0",
            "submit_time": "",
        }
        state_attempt = self._sco_request(
            context,
            {
                "action": "setscoinfo",
                "cid": context.course_id,
                "scoid": context.sco_id,
                "uid": context.learner_id,
                "data": json.dumps(state, ensure_ascii=True, separators=(",", ":"))
                + "[INTERACTIONINFO]",
                "isend": "False",
            },
            cancellation,
        )
        state_outcome = state_attempt.outcome
        if state_outcome is None:
            assert state_attempt.response is not None
            state_outcome = self._ret_outcome(state_attempt.response)
        steps.append(StepResult("set_state", state_outcome))
        if state_outcome.kind is OutcomeKind.CANCELLED:
            return WorkflowResult(state_outcome, tuple(steps))

        save_attempt = self._sco_request(
            context,
            {
                "action": "savescoinfo160928",
                "cid": context.course_id,
                "scoid": context.sco_id,
                "uid": context.learner_id,
                "progress": "100",
                "crate": str(accuracy_percent),
                "status": "unknown",
                "cstatus": "completed",
                "trycount": "0",
            },
            cancellation,
        )
        save_outcome = save_attempt.outcome
        if save_outcome is None:
            assert save_attempt.response is not None
            save_outcome = self._ret_outcome(save_attempt.response)
        steps.append(StepResult("save", save_outcome))

        update_outcomes = (state_outcome, save_outcome)
        if all(item.kind is OutcomeKind.ACCEPTED for item in update_outcomes):
            overall = RequestOutcome.accepted("homework workflow accepted")
        elif any(item.kind is OutcomeKind.CANCELLED for item in update_outcomes):
            overall = RequestOutcome.cancelled("homework workflow cancelled")
        elif any(item.kind is OutcomeKind.ACCEPTED for item in update_outcomes):
            overall = RequestOutcome.unknown("homework workflow was only partially accepted")
        elif any(item.kind is OutcomeKind.UNKNOWN for item in update_outcomes):
            overall = RequestOutcome.unknown("homework workflow result was unknown")
        else:
            overall = RequestOutcome.rejected("homework workflow was rejected")
        return WorkflowResult(overall, tuple(steps))

    def _read_sco_state(
        self,
        context: LessonContext,
        cancellation: Cancellation | None,
    ) -> ReadResult[ScoState]:
        data = {
            "action": "getscoinfo_v7",
            "uid": context.learner_id,
            "cid": context.course_id,
            "scoid": context.sco_id,
        }
        attempt = self._sco_request(context, data, cancellation)
        if attempt.outcome is not None:
            return self._failed_read(attempt.outcome)
        assert attempt.response is not None
        envelope = self._json_object(attempt.response)
        comment = envelope.get("comment") if envelope is not None else None
        if isinstance(comment, str):
            try:
                comment = json.loads(comment)
            except (TypeError, ValueError):
                comment = None
        if not isinstance(comment, dict):
            return self._failed_read(RequestOutcome.unknown("SCO state was malformed"))
        cmi = comment.get("cmi")
        if not isinstance(cmi, dict):
            return self._failed_read(RequestOutcome.unknown("SCO state was malformed"))

        session_time = _form_scalar(cmi.get("session_time"))
        total_time = _form_scalar(cmi.get("total_time"))
        progress = _form_scalar(cmi.get("progress_measure", "0"))
        completion = _form_scalar(cmi.get("completion_status"))
        score = cmi.get("score")
        scaled = _form_scalar(score.get("scaled")) if isinstance(score, dict) else None
        if any(value is None for value in (session_time, total_time, progress, completion, scaled)):
            return self._failed_read(RequestOutcome.unknown("SCO state was incomplete"))
        return ReadResult(
            RequestOutcome.accepted("SCO state read"),
            ScoState(session_time, total_time, progress, scaled, completion),
        )

    def _heartbeat(
        self,
        context: LessonContext,
        session_time: int,
        total_time: int,
        cancellation: Cancellation | None,
    ) -> RequestOutcome:
        attempt = self._sco_request(
            context,
            {
                "action": "keepsco_with_getticket_with_updatecmitime",
                "uid": context.learner_id,
                "cid": context.course_id,
                "scoid": context.sco_id,
                "session_time": str(session_time),
                "total_time": str(total_time),
                "endcaltime": "false",
                "timelimitsec": "3600",
            },
            cancellation,
        )
        if attempt.outcome is not None:
            return attempt.outcome
        return RequestOutcome.accepted("heartbeat accepted")

    def _wait(
        self,
        cancellation: Cancellation | None,
        seconds: float,
    ) -> RequestOutcome | None:
        if _is_cancelled(cancellation):
            return RequestOutcome.cancelled("cancelled during timed wait")
        try:
            interrupted = self._waiter(cancellation, seconds)
        except Exception:
            return RequestOutcome.unknown("timed wait failed")
        if interrupted or _is_cancelled(cancellation):
            return RequestOutcome.cancelled("cancelled during timed wait")
        return None

    def run_timed_study(
        self,
        context: LessonContext,
        duration_seconds: int,
        cancellation: Cancellation | None = None,
    ) -> WorkflowResult:
        if (
            isinstance(duration_seconds, bool)
            or not isinstance(duration_seconds, int)
            or duration_seconds < 0
        ):
            raise ValueError("duration_seconds must be a non-negative integer")

        steps: list[StepResult] = []
        start_attempt = self._sco_request(context, self._start_fields(context), cancellation)
        start_outcome = start_attempt.outcome
        if start_outcome is None:
            assert start_attempt.response is not None
            start_outcome = self._ret_outcome(start_attempt.response)
        steps.append(StepResult("start", start_outcome))
        if start_outcome.kind is not OutcomeKind.ACCEPTED:
            return WorkflowResult(start_outcome, tuple(steps))

        state_result = self._read_sco_state(context, cancellation)
        steps.append(StepResult("read_state", state_result.outcome))
        if state_result.outcome.kind is not OutcomeKind.ACCEPTED:
            return WorkflowResult(state_result.outcome, tuple(steps))
        assert state_result.value is not None
        state = state_result.value
        initial_session_time = _nonnegative_seconds(state.session_time)
        initial_total_time = _nonnegative_seconds(state.total_time)
        if initial_session_time is None or initial_total_time is None:
            outcome = RequestOutcome.unknown("SCO time state was not measured in seconds")
            steps.append(StepResult("validate_time_state", outcome))
            return WorkflowResult(outcome, tuple(steps))

        initial_heartbeat = self._heartbeat(
            context,
            initial_session_time,
            initial_total_time,
            cancellation,
        )
        steps.append(StepResult("heartbeat_initial", initial_heartbeat))
        if initial_heartbeat.kind is not OutcomeKind.ACCEPTED:
            return WorkflowResult(initial_heartbeat, tuple(steps))

        full_intervals, remainder = divmod(duration_seconds, 60)
        completed_intervals = 0
        for interval in range(1, full_intervals + 1):
            wait_outcome = self._wait(cancellation, 60)
            if wait_outcome is not None:
                return WorkflowResult(wait_outcome, tuple(steps), completed_intervals)
            completed_intervals += 1
            heartbeat = self._heartbeat(
                context,
                initial_session_time,
                initial_total_time,
                cancellation,
            )
            steps.append(StepResult(f"heartbeat_{interval}", heartbeat))
            if heartbeat.kind is not OutcomeKind.ACCEPTED:
                return WorkflowResult(heartbeat, tuple(steps), completed_intervals)

        if remainder:
            wait_outcome = self._wait(cancellation, remainder)
            if wait_outcome is not None:
                return WorkflowResult(wait_outcome, tuple(steps), completed_intervals)

        save_attempt = self._sco_request(
            context,
            {
                "action": "savescoinfo160928",
                "uid": context.learner_id,
                "cid": context.course_id,
                "scoid": context.sco_id,
                "progress": state.progress_measure,
                "crate": state.score_scaled,
                "status": "unknown",
                "cstatus": state.completion_status,
                "trycount": "0",
                "endcaltime": "false",
            },
            cancellation,
        )
        save_outcome = save_attempt.outcome
        if save_outcome is None:
            assert save_attempt.response is not None
            save_outcome = self._ret_outcome(save_attempt.response)
        steps.append(StepResult("save", save_outcome))
        return WorkflowResult(save_outcome, tuple(steps), completed_intervals)

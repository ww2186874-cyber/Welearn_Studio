from __future__ import annotations

import base64
import json
import threading
import time
import unittest
from collections import deque
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from welearn_studio import __version__
from welearn_studio.adapters import (
    LessonContext,
    RequestsSessionTransport,
    Visibility,
    WeLearnRemoteClient,
    encode_password_for_wire,
)
from welearn_studio.domain.outcomes import OutcomeKind

FIXTURES = Path(__file__).with_name("fixtures")
_MISSING = object()


def load_json(name: str) -> object:
    with (FIXTURES / name).open("r", encoding="utf-8") as stream:
        return json.load(stream)


class FakeResponse:
    def __init__(
        self,
        json_value: object = _MISSING,
        *,
        status_code: int = 200,
        text: str = "",
        url: str = "https://synthetic.invalid/response",
    ) -> None:
        self._json_value = json_value
        self.status_code = status_code
        self.text = text
        self.url = url

    def json(self) -> Any:
        if self._json_value is _MISSING:
            raise ValueError("synthetic non-JSON response")
        return self._json_value


class RecordingTransport:
    def __init__(
        self,
        responses: list[FakeResponse | Exception],
        *,
        after_request: Callable[[int], None] | None = None,
    ) -> None:
        self._responses = deque(responses)
        self.requests: list[dict[str, Any]] = []
        self.closed = False
        self._after_request = after_request

    def request(self, method: str, url: str, **kwargs: object) -> FakeResponse:
        self.requests.append({"method": method, "url": url, **kwargs})
        if not self._responses:
            raise AssertionError("unexpected network-shaped request")
        response = self._responses.popleft()
        if self._after_request is not None:
            self._after_request(len(self.requests))
        if isinstance(response, Exception):
            raise response
        return response

    def close(self) -> None:
        self.closed = True


class StubCancellation:
    def __init__(self, cancelled: bool = False) -> None:
        self.cancelled = cancelled
        self.wait_calls: list[float | None] = []

    @property
    def is_cancelled(self) -> bool:
        return self.cancelled

    def wait(self, timeout: float | None = None) -> bool:
        self.wait_calls.append(timeout)
        return self.cancelled


class RecordingWaiter:
    def __init__(self, results: list[bool] | None = None) -> None:
        self.calls: list[float] = []
        self._results = deque(results or [])

    def __call__(self, cancellation: object, seconds: float) -> bool:
        self.calls.append(seconds)
        return self._results.popleft() if self._results else False


def synthetic_authorization_redirect() -> str:
    callback_query = urlencode(
        {
            "client_id": "welearn_web",
            "redirect_uri": "https://welearn.sflep.com/signin-sflep",
            "response_type": "code",
            "scope": "openid profile email phone address",
            "code_challenge": "synthetic-challenge",
            "code_challenge_method": "S256",
            "state": "synthetic-state",
            "x-client-SKU": "ID_NET472",
            "x-client-ver": "6.32.1.0",
        }
    )
    callback = f"/connect/authorize/callback?{callback_query}"
    transfer_query = urlencode({"returnUrl": callback})
    return urlunsplit(("https", "sso.sflep.com", "/idsvr/transfer.html", transfer_query, ""))


def lesson_context() -> LessonContext:
    return LessonContext("course-alpha", "812004", "class_demo_7", "lesson-visible")


class PasswordWireTests(unittest.TestCase):
    def test_transform_obeys_timestamp_xor_and_utf8_hex_contract(self) -> None:
        password = "".join(chr(value) for value in (65, 0x4E2D, 33))
        original = sum(value * (10**index) for index, value in enumerate((5, 4, 3, 2, 1)))

        result = encode_password_for_wire(password, now_milliseconds=lambda: original)

        self.assertNotIn(result.encoded, repr(result))
        self.assertNotIn(str(result.timestamp), repr(result))
        accumulator = (original >> 16) & 0xFF
        for value in password.encode("utf-8"):
            accumulator ^= value
        self.assertEqual(result.timestamp // 100, original // 100)
        self.assertEqual(result.timestamp % 100, accumulator % 100)
        decoded = base64.b64decode(result.encoded).decode("utf-8")
        timestamp_text, password_hex = decoded.split("*", 1)
        self.assertEqual(timestamp_text, str(result.timestamp))
        self.assertEqual(password_hex, password.encode("utf-8").hex())


class TransportTests(unittest.TestCase):
    def test_requests_transport_delegates_without_creating_another_session(self) -> None:
        class FakeSession:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, dict[str, object]]] = []
                self.closed = False

            def request(self, method: str, url: str, **kwargs: object) -> FakeResponse:
                self.calls.append((method, url, kwargs))
                return FakeResponse({})

            def close(self) -> None:
                self.closed = True

        session = FakeSession()
        transport = RequestsSessionTransport(session)  # type: ignore[arg-type]
        response = transport.request("GET", "https://synthetic.invalid/read", timeout=3)
        transport.close()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(session.calls[0][0:2], ("GET", "https://synthetic.invalid/read"))
        self.assertFalse(session.calls[0][2]["allow_redirects"])
        self.assertEqual(
            session.calls[0][2]["headers"]["User-Agent"], f"WeLearnStudio/{__version__}"
        )
        self.assertTrue(session.closed)

    def test_request_headers_extend_the_application_identity(self) -> None:
        class FakeSession:
            def __init__(self) -> None:
                self.kwargs: dict[str, object] = {}

            def request(self, _method: str, _url: str, **kwargs: object) -> FakeResponse:
                self.kwargs = kwargs
                return FakeResponse({})

            def close(self) -> None:
                return None

        session = FakeSession()
        transport = RequestsSessionTransport(session)  # type: ignore[arg-type]

        transport.request(
            "GET",
            "https://synthetic.invalid/read",
            headers={"Referer": "https://synthetic.invalid/source"},
        )

        self.assertEqual(session.kwargs["headers"]["User-Agent"], f"WeLearnStudio/{__version__}")
        self.assertEqual(session.kwargs["headers"]["Referer"], "https://synthetic.invalid/source")

    def test_default_transports_create_distinct_cookie_sessions(self) -> None:
        sessions = [object(), object()]
        with patch(
            "welearn_studio.adapters.transport.requests.Session",
            side_effect=sessions,
        ) as constructor:
            first = RequestsSessionTransport()
            second = RequestsSessionTransport()
        self.assertEqual(constructor.call_count, 2)
        self.assertIsNot(first._session, second._session)

    def test_one_account_session_serializes_mutating_http_exchanges(self) -> None:
        class ConcurrentSession:
            def __init__(self) -> None:
                self.active = 0
                self.maximum = 0
                self.lock = threading.Lock()

            def request(self, _method: str, _url: str, **_kwargs: object) -> FakeResponse:
                with self.lock:
                    self.active += 1
                    self.maximum = max(self.maximum, self.active)
                time.sleep(0.02)
                with self.lock:
                    self.active -= 1
                return FakeResponse({})

            def close(self) -> None:
                return None

        session = ConcurrentSession()
        transport = RequestsSessionTransport(session)  # type: ignore[arg-type]
        threads = [
            threading.Thread(target=lambda: transport.request("GET", "https://synthetic.invalid"))
            for _ in range(3)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(session.maximum, 1)


class LoginTests(unittest.TestCase):
    def test_login_preserves_three_step_order_and_documented_fields(self) -> None:
        transport = RecordingTransport(
            [
                FakeResponse({}, url=synthetic_authorization_redirect()),
                FakeResponse({"code": 0}),
                FakeResponse({}),
            ]
        )
        client = WeLearnRemoteClient(transport, now_milliseconds=lambda: 123456)
        account = "".join(chr(value) for value in (100, 101, 109, 111))
        password = "".join(chr(value) for value in (116, 101, 115, 116))

        outcome = client.login(account, password)

        self.assertEqual(outcome.kind, OutcomeKind.ACCEPTED)
        self.assertEqual([call["method"] for call in transport.requests], ["GET", "POST", "GET"])
        identity_call = transport.requests[1]
        fields = identity_call["data"]
        self.assertIsInstance(fields, dict)
        assert isinstance(fields, dict)
        self.assertEqual(fields["account"], account)
        self.assertNotEqual(fields["pwd"], password)
        callback = urlsplit(str(fields["rturl"]))
        callback_query = parse_qs(callback.query)
        self.assertEqual(callback.path, "/connect/authorize/callback")
        self.assertEqual(callback_query["client_id"], ["welearn_web"])
        self.assertEqual(callback_query["code_challenge_method"], ["S256"])
        self.assertEqual(callback_query["state"], ["synthetic-state"])
        self.assertTrue(transport.requests[0]["allow_redirects"])
        self.assertFalse(transport.requests[1]["allow_redirects"])
        self.assertTrue(transport.requests[2]["allow_redirects"])
        self.assertEqual(
            transport.requests[0]["params"],
            {"loginret": "http://welearn.sflep.com/user/loginredirect.aspx"},
        )

    def test_direct_callback_shape_remains_supported(self) -> None:
        callback = "/connect/authorize/callback?code_challenge=value&state=state"
        transport = RecordingTransport(
            [
                FakeResponse({}, url=f"https://sso.sflep.com{callback}"),
                FakeResponse({"code": 0}),
                FakeResponse({}),
            ]
        )

        outcome = WeLearnRemoteClient(transport).login("synthetic-account", "password")

        self.assertEqual(outcome.kind, OutcomeKind.ACCEPTED)
        self.assertEqual(transport.requests[1]["data"]["rturl"], callback)

    def test_login_rejection_does_not_finalize(self) -> None:
        transport = RecordingTransport(
            [
                FakeResponse({}, url=synthetic_authorization_redirect()),
                FakeResponse({"code": 1}),
            ]
        )
        outcome = WeLearnRemoteClient(transport).login("synthetic-account", "")
        self.assertEqual(outcome.kind, OutcomeKind.REJECTED)
        self.assertEqual(len(transport.requests), 2)

    def test_string_login_code_remains_unknown(self) -> None:
        transport = RecordingTransport(
            [
                FakeResponse({}, url=synthetic_authorization_redirect()),
                FakeResponse({"code": "0"}),
            ]
        )
        outcome = WeLearnRemoteClient(transport).login("synthetic-account", "")
        self.assertEqual(outcome.kind, OutcomeKind.UNKNOWN)
        self.assertEqual(len(transport.requests), 2)

    def test_missing_authorization_state_is_unknown(self) -> None:
        redirect = urlunsplit(
            (
                "https",
                "synthetic.invalid",
                "/connect/authorize/callback",
                "code_challenge=value",
                "",
            )
        )
        transport = RecordingTransport([FakeResponse({}, url=redirect)])
        outcome = WeLearnRemoteClient(transport).login("synthetic-account", "")
        self.assertEqual(outcome.kind, OutcomeKind.UNKNOWN)
        self.assertEqual(len(transport.requests), 1)

    def test_pre_request_cancellation_makes_no_transport_call(self) -> None:
        transport = RecordingTransport([])
        outcome = WeLearnRemoteClient(transport).login(
            "synthetic-account", "", StubCancellation(cancelled=True)
        )
        self.assertEqual(outcome.kind, OutcomeKind.CANCELLED)
        self.assertEqual(transport.requests, [])

    def test_transport_exception_text_is_not_exposed(self) -> None:
        transport = RecordingTransport([RuntimeError("credential-or-cookie-shaped-value")])
        outcome = WeLearnRemoteClient(transport).login("synthetic-account", "")
        self.assertEqual(outcome.kind, OutcomeKind.UNKNOWN)
        self.assertNotIn("credential-or-cookie-shaped-value", outcome.detail)


class ReadWorkflowTests(unittest.TestCase):
    def test_course_list_is_normalized_and_empty_list_is_accepted(self) -> None:
        transport = RecordingTransport(
            [FakeResponse(load_json("course_list.json")), FakeResponse({"clist": []})]
        )
        client = WeLearnRemoteClient(transport)

        populated = client.list_courses()
        empty = client.list_courses()

        self.assertEqual(populated.outcome.kind, OutcomeKind.ACCEPTED)
        assert populated.value is not None
        self.assertEqual(
            [item.identity.stable_id for item in populated.value], ["course-alpha", "course-beta"]
        )
        self.assertEqual(populated.value[0].progress_percent, 24)
        self.assertEqual(empty.value, ())
        self.assertEqual(transport.requests[0]["params"], {"action": "gmc"})

    def test_numeric_course_identifier_is_normalized_to_text(self) -> None:
        transport = RecordingTransport(
            [FakeResponse({"clist": [{"cid": 73001234, "name": "Synthetic Course"}]})]
        )

        result = WeLearnRemoteClient(transport).list_courses()

        self.assertEqual(result.outcome.kind, OutcomeKind.ACCEPTED)
        assert result.value is not None
        self.assertEqual(result.value[0].identity.stable_id, "73001234")

    def test_invalid_course_identity_and_non_json_are_unknown(self) -> None:
        transport = RecordingTransport(
            [FakeResponse({"clist": [{"cid": "", "name": "Name"}]}), FakeResponse()]
        )
        client = WeLearnRemoteClient(transport)
        self.assertEqual(client.list_courses().outcome.kind, OutcomeKind.UNKNOWN)
        self.assertEqual(client.list_courses().outcome.kind, OutcomeKind.UNKNOWN)

    def test_non_2xx_read_is_rejected(self) -> None:
        transport = RecordingTransport([FakeResponse({}, status_code=302)])
        result = WeLearnRemoteClient(transport).list_courses()
        self.assertEqual(result.outcome.kind, OutcomeKind.REJECTED)
        self.assertFalse(transport.requests[0]["allow_redirects"])

    def test_course_bootstrap_accepts_unique_json_or_script_context(self) -> None:
        html = (FIXTURES / "course_bootstrap.html").read_text(encoding="utf-8")
        script_object = """
            <script>window.course = {"uid":812004,"classid":"class_demo_7"};</script>
        """
        ambiguous = """
            <script type="application/json">{"uid":"1","classid":"a"}</script>
            <script type="application/json">{"uid":"2","classid":"b"}</script>
        """
        incidental = "<script>var uid='1'; var classid='a';</script>"
        transport = RecordingTransport(
            [
                FakeResponse(text=html),
                FakeResponse(text=script_object),
                FakeResponse(text=ambiguous),
                FakeResponse(text=incidental),
            ]
        )
        client = WeLearnRemoteClient(transport)

        accepted = client.bootstrap_course("course-alpha")
        self.assertEqual(accepted.outcome.kind, OutcomeKind.ACCEPTED)
        assert accepted.value is not None
        self.assertEqual(
            (accepted.value.learner_id, accepted.value.class_id), ("812004", "class_demo_7")
        )
        scripted = client.bootstrap_course("course-alpha")
        self.assertEqual(scripted.outcome.kind, OutcomeKind.ACCEPTED)
        assert scripted.value is not None
        self.assertEqual(
            (scripted.value.learner_id, scripted.value.class_id), ("812004", "class_demo_7")
        )
        self.assertEqual(client.bootstrap_course("course-alpha").outcome.kind, OutcomeKind.UNKNOWN)
        self.assertEqual(client.bootstrap_course("course-alpha").outcome.kind, OutcomeKind.UNKNOWN)

    def test_unit_order_defines_indices_without_sorting(self) -> None:
        transport = RecordingTransport([FakeResponse(load_json("units.json"))])
        result = WeLearnRemoteClient(transport).list_units("course-alpha", "812004")
        self.assertEqual(result.outcome.kind, OutcomeKind.ACCEPTED)
        assert result.value is not None
        self.assertEqual(
            [(unit.index, unit.name) for unit in result.value],
            [(0, "Unit Two"), (1, "Unit One"), (2, "Unit Three")],
        )
        self.assertEqual(
            transport.requests[0]["params"],
            {"action": "courseunits", "cid": "course-alpha", "uid": "812004"},
        )

    def test_lessons_skip_invalid_items_and_keep_unknown_metadata_unknown(self) -> None:
        transport = RecordingTransport([FakeResponse(load_json("lessons.json"))])
        result = WeLearnRemoteClient(transport).list_lessons(
            "course-alpha", "812004", "class_demo_7", 0
        )

        self.assertEqual(result.outcome.kind, OutcomeKind.ACCEPTED)
        assert result.value is not None
        self.assertEqual(len(result.value), 4)
        self.assertEqual(
            [item.visibility for item in result.value],
            [Visibility.VISIBLE, Visibility.HIDDEN, Visibility.UNKNOWN, Visibility.UNKNOWN],
        )
        self.assertTrue(result.value[0].runnable)
        self.assertFalse(result.value[1].runnable)
        self.assertEqual(len(result.issues), 2)
        self.assertEqual(transport.requests[0]["params"]["unitidx"], 0)

    def test_numeric_lesson_identifier_is_normalized_to_text(self) -> None:
        transport = RecordingTransport(
            [
                FakeResponse(
                    {
                        "info": [
                            {"id": 83004567, "location": "Synthetic Lesson", "isvisible": "true"}
                        ]
                    }
                )
            ]
        )

        result = WeLearnRemoteClient(transport).list_lessons(
            "course-alpha", "812004", "class_demo_7", 0
        )

        self.assertEqual(result.outcome.kind, OutcomeKind.ACCEPTED)
        assert result.value is not None
        self.assertEqual(result.value[0].sco_id, "83004567")


class HomeworkWorkflowTests(unittest.TestCase):
    def test_successful_homework_uses_ordered_payloads(self) -> None:
        transport = RecordingTransport(
            [FakeResponse({"ret": 0}), FakeResponse({"ret": "0"}), FakeResponse({"ret": 0})]
        )
        result = WeLearnRemoteClient(transport).submit_homework(lesson_context(), 87)

        self.assertEqual(result.outcome.kind, OutcomeKind.ACCEPTED)
        actions = [call["data"]["action"] for call in transport.requests]
        self.assertEqual(actions, ["startsco160928", "setscoinfo", "savescoinfo160928"])
        encoded_state = transport.requests[1]["data"]["data"]
        self.assertTrue(encoded_state.endswith("[INTERACTIONINFO]"))
        state = json.loads(encoded_state.removesuffix("[INTERACTIONINFO]"))
        self.assertEqual(state["cmi"]["score"]["scaled"], "87")
        self.assertEqual(transport.requests[2]["data"]["crate"], "87")

    def test_unrecognized_start_response_is_unknown_and_stops(self) -> None:
        transport = RecordingTransport([FakeResponse({"legacy": "synthetic"})])
        result = WeLearnRemoteClient(transport).submit_homework(lesson_context(), 50)
        self.assertEqual(result.outcome.kind, OutcomeKind.UNKNOWN)
        self.assertEqual(len(transport.requests), 1)

    def test_partially_accepted_updates_are_unknown(self) -> None:
        transport = RecordingTransport(
            [FakeResponse({"ret": 0}), FakeResponse({"ret": 0}), FakeResponse({"ret": 8})]
        )
        result = WeLearnRemoteClient(transport).submit_homework(lesson_context(), 50)
        self.assertEqual(result.outcome.kind, OutcomeKind.UNKNOWN)
        self.assertEqual(
            [step.outcome.kind for step in result.steps],
            [OutcomeKind.ACCEPTED, OutcomeKind.ACCEPTED, OutcomeKind.REJECTED],
        )

    def test_cancellation_after_state_write_skips_save(self) -> None:
        cancellation = StubCancellation()

        def cancel_after_second(request_count: int) -> None:
            if request_count == 2:
                cancellation.cancelled = True

        transport = RecordingTransport(
            [FakeResponse({"ret": 0}), FakeResponse({"ret": 0})],
            after_request=cancel_after_second,
        )
        result = WeLearnRemoteClient(transport).submit_homework(lesson_context(), 50, cancellation)
        self.assertEqual(result.outcome.kind, OutcomeKind.CANCELLED)
        self.assertEqual(len(transport.requests), 2)

    def test_accuracy_validation_happens_before_transport(self) -> None:
        transport = RecordingTransport([])
        with self.assertRaises(ValueError):
            WeLearnRemoteClient(transport).submit_homework(lesson_context(), 101)
        self.assertEqual(transport.requests, [])


class TimedWorkflowTests(unittest.TestCase):
    def test_full_intervals_replay_initial_state_and_finish_with_save(self) -> None:
        waiter = RecordingWaiter()
        transport = RecordingTransport(
            [
                FakeResponse({"ret": 0}),
                FakeResponse(load_json("sco_state.json")),
                FakeResponse(text="synthetic-initial-ticket"),
                FakeResponse(text="synthetic-ticket-one"),
                FakeResponse(text="synthetic-ticket-two"),
                FakeResponse({"ret": 0}),
            ]
        )
        result = WeLearnRemoteClient(transport, waiter=waiter).run_timed_study(
            lesson_context(), 125
        )

        self.assertEqual(result.outcome.kind, OutcomeKind.ACCEPTED)
        self.assertEqual(result.completed_intervals, 2)
        self.assertEqual(waiter.calls, [60, 60, 5])
        actions = [call["data"]["action"] for call in transport.requests]
        self.assertEqual(
            actions,
            [
                "startsco160928",
                "getscoinfo_v7",
                "keepsco_with_getticket_with_updatecmitime",
                "keepsco_with_getticket_with_updatecmitime",
                "keepsco_with_getticket_with_updatecmitime",
                "savescoinfo160928",
            ],
        )
        heartbeats = [request["data"] for request in transport.requests[2:5]]
        for heartbeat in heartbeats:
            self.assertEqual(heartbeat["session_time"], "0")
            self.assertEqual(heartbeat["total_time"], "6")
            self.assertEqual(heartbeat["endcaltime"], "false")
            self.assertEqual(heartbeat["timelimitsec"], "3600")
            self.assertNotIn("classid", heartbeat)
            self.assertNotIn("tid", heartbeat)

        save = transport.requests[-1]["data"]
        self.assertEqual(save["progress"], "0.75")
        self.assertEqual(save["crate"], "0.55")
        self.assertEqual(save["cstatus"], "synthetic-status")
        self.assertEqual(save["endcaltime"], "false")
        for request in transport.requests:
            self.assertIn("nocache", request["data"])
            self.assertLess(float(request["data"]["nocache"]), 1.0)
        self.assertNotIn("ticket", result.outcome.detail)

    def test_seconds_below_interval_use_initial_heartbeat_then_save(self) -> None:
        waiter = RecordingWaiter()
        transport = RecordingTransport(
            [
                FakeResponse({"ret": 0}),
                FakeResponse(load_json("sco_state.json")),
                FakeResponse({"ret": 0}),
                FakeResponse({"ret": 0}),
            ]
        )
        result = WeLearnRemoteClient(transport, waiter=waiter).run_timed_study(lesson_context(), 59)
        self.assertEqual(result.outcome.kind, OutcomeKind.ACCEPTED)
        self.assertEqual(waiter.calls, [59])
        self.assertEqual(
            [request["data"]["action"] for request in transport.requests],
            [
                "startsco160928",
                "getscoinfo_v7",
                "keepsco_with_getticket_with_updatecmitime",
                "savescoinfo160928",
            ],
        )

    def test_rejected_initial_heartbeat_stops_before_wait_and_save(self) -> None:
        waiter = RecordingWaiter()
        transport = RecordingTransport(
            [
                FakeResponse({"ret": 0}),
                FakeResponse(load_json("sco_state.json")),
                FakeResponse(status_code=409),
            ]
        )

        result = WeLearnRemoteClient(transport, waiter=waiter).run_timed_study(
            lesson_context(), 120
        )

        self.assertEqual(result.outcome.kind, OutcomeKind.REJECTED)
        self.assertEqual(result.completed_intervals, 0)
        self.assertEqual(waiter.calls, [])
        self.assertEqual(len(transport.requests), 3)

    def test_rejected_periodic_heartbeat_stops_before_save(self) -> None:
        waiter = RecordingWaiter()
        transport = RecordingTransport(
            [
                FakeResponse({"ret": 0}),
                FakeResponse(load_json("sco_state.json")),
                FakeResponse(text="synthetic-initial-ticket"),
                FakeResponse(status_code=409),
            ]
        )
        result = WeLearnRemoteClient(transport, waiter=waiter).run_timed_study(
            lesson_context(), 120
        )
        self.assertEqual(result.outcome.kind, OutcomeKind.REJECTED)
        self.assertEqual(result.completed_intervals, 1)
        self.assertEqual(waiter.calls, [60])
        self.assertEqual(len(transport.requests), 4)

    def test_cancellation_during_wait_skips_heartbeat_and_save(self) -> None:
        waiter = RecordingWaiter([True])
        cancellation = StubCancellation()
        transport = RecordingTransport(
            [
                FakeResponse({"ret": 0}),
                FakeResponse(load_json("sco_state.json")),
                FakeResponse(text="synthetic-initial-ticket"),
            ]
        )
        result = WeLearnRemoteClient(transport, waiter=waiter).run_timed_study(
            lesson_context(), 60, cancellation
        )
        self.assertEqual(result.outcome.kind, OutcomeKind.CANCELLED)
        self.assertEqual(result.completed_intervals, 0)
        self.assertEqual(len(transport.requests), 3)

    def test_malformed_state_and_missing_save_ret_remain_unknown(self) -> None:
        malformed_transport = RecordingTransport(
            [FakeResponse({"ret": 0}), FakeResponse({"comment": {"cmi": {}}})]
        )
        malformed = WeLearnRemoteClient(malformed_transport).run_timed_study(lesson_context(), 0)
        self.assertEqual(malformed.outcome.kind, OutcomeKind.UNKNOWN)

        no_ret_transport = RecordingTransport(
            [
                FakeResponse({"ret": 0}),
                FakeResponse(load_json("sco_state.json")),
                FakeResponse(text="synthetic-initial-ticket"),
                FakeResponse({}),
            ]
        )
        no_ret = WeLearnRemoteClient(no_ret_transport, waiter=RecordingWaiter()).run_timed_study(
            lesson_context(), 0
        )
        self.assertEqual(no_ret.outcome.kind, OutcomeKind.UNKNOWN)

    def test_comment_json_string_is_decoded_and_missing_progress_defaults_in_request(self) -> None:
        comment = {
            "cmi": {
                "session_time": "0",
                "total_time": "6",
                "completion_status": "status",
                "score": {"scaled": "score"},
            }
        }
        transport = RecordingTransport(
            [
                FakeResponse({"ret": 0}),
                FakeResponse({"comment": json.dumps(comment)}),
                FakeResponse({"ret": 0}),
                FakeResponse({"ret": 0}),
            ]
        )
        result = WeLearnRemoteClient(transport, waiter=RecordingWaiter()).run_timed_study(
            lesson_context(), 0
        )
        self.assertEqual(result.outcome.kind, OutcomeKind.ACCEPTED)
        self.assertEqual(transport.requests[-1]["data"]["progress"], "0")

    def test_non_numeric_time_state_is_not_replayed_as_elapsed_seconds(self) -> None:
        invalid_state = {
            "comment": {
                "cmi": {
                    "session_time": "opaque-session",
                    "total_time": "6",
                    "progress_measure": "0",
                    "completion_status": "incomplete",
                    "score": {"scaled": "0"},
                }
            }
        }
        transport = RecordingTransport([FakeResponse({"ret": 0}), FakeResponse(invalid_state)])

        result = WeLearnRemoteClient(transport, waiter=RecordingWaiter()).run_timed_study(
            lesson_context(), 60
        )

        self.assertEqual(result.outcome.kind, OutcomeKind.UNKNOWN)
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(result.steps[-1].name, "validate_time_state")

    def test_three_minutes_send_initial_plus_three_periodic_heartbeats(self) -> None:
        transport = RecordingTransport(
            [
                FakeResponse({"ret": 0}),
                FakeResponse(load_json("sco_state.json")),
                FakeResponse(text="synthetic-initial-ticket"),
                FakeResponse(text="synthetic-ticket-one"),
                FakeResponse(text="synthetic-ticket-two"),
                FakeResponse(text="synthetic-ticket-three"),
                FakeResponse({"ret": 0}),
            ]
        )
        waiter = RecordingWaiter()

        result = WeLearnRemoteClient(transport, waiter=waiter).run_timed_study(
            lesson_context(), 180
        )

        self.assertEqual(result.outcome.kind, OutcomeKind.ACCEPTED)
        self.assertEqual(result.completed_intervals, 3)
        self.assertEqual(waiter.calls, [60, 60, 60])
        self.assertEqual(
            [request["data"]["action"] for request in transport.requests],
            [
                "startsco160928",
                "getscoinfo_v7",
                "keepsco_with_getticket_with_updatecmitime",
                "keepsco_with_getticket_with_updatecmitime",
                "keepsco_with_getticket_with_updatecmitime",
                "keepsco_with_getticket_with_updatecmitime",
                "savescoinfo160928",
            ],
        )
        for request in transport.requests[2:6]:
            self.assertEqual(request["data"]["session_time"], "0")
            self.assertEqual(request["data"]["total_time"], "6")


if __name__ == "__main__":
    unittest.main()

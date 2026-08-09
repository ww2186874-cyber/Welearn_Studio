import unittest
from dataclasses import FrozenInstanceError

from welearn_studio.domain.models import (
    AccountIdentity,
    AccountRuntime,
    CourseIdentity,
    RuntimeState,
    TaskMode,
    TaskParameters,
)
from welearn_studio.domain.outcomes import OutcomeKind, RequestOutcome


class DomainTests(unittest.TestCase):
    def test_identities_and_runtime_are_immutable(self) -> None:
        account = AccountIdentity("  learner@example.test  ", "  Learner  ")
        runtime = AccountRuntime(account, RuntimeState.SIGNED_IN, 2, 4)

        self.assertEqual(account.username, "learner@example.test")
        self.assertEqual(account.nickname, "Learner")
        self.assertEqual(runtime.progress, 0.5)
        with self.assertRaises(FrozenInstanceError):
            runtime.state = RuntimeState.ERROR  # type: ignore[misc]

    def test_empty_stable_identity_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            CourseIdentity(" ", "Synthetic course")

    def test_task_parameter_ranges_are_enforced(self) -> None:
        valid = TaskParameters(TaskMode.TIME_STUDY, 0, 72, 500, 100)
        self.assertEqual(valid.accuracy_percent, 0)
        for kwargs in (
            {"accuracy_percent": 101},
            {"total_hours": 0},
            {"total_hours": 73},
            {"random_minutes": -1},
            {"concurrency": 0},
            {"concurrency": 101},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                TaskParameters(**kwargs)

    def test_all_request_outcomes_are_explicit(self) -> None:
        outcomes = (
            RequestOutcome.accepted("queued"),
            RequestOutcome.rejected("invalid"),
            RequestOutcome.unknown("timeout"),
            RequestOutcome.cancelled(),
        )

        self.assertEqual({value.kind for value in outcomes}, set(OutcomeKind))
        self.assertTrue(outcomes[0].endpoint_accepted)
        self.assertTrue(all(not value.endpoint_accepted for value in outcomes[1:]))


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch

from cyberclaw.core.tools import ApprovalStore


class MutableClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _request(store: ApprovalStore, *, fingerprint: str = "fingerprint"):
    return store.request(
        thread_id="thread-a",
        tool_name="write_file",
        canonical_arguments='{"path":"a.txt"}',
        invocation_fingerprint=fingerprint,
        reason="写入操作需要确认",
    )


class TestApprovalStore(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = MutableClock()
        self.store = ApprovalStore(ttl_seconds=10, clock=self.clock)

    def test_same_pending_invocation_reuses_request(self):
        first = _request(self.store)
        second = _request(self.store)

        self.assertEqual(first.request_id, second.request_id)

    def test_approved_grant_is_bound_and_consumed_once(self):
        request = _request(self.store)
        grant = self.store.approve(
            request.request_id,
            thread_id="thread-a",
        )

        self.assertIsNotNone(grant)
        consumed = self.store.consume(
            thread_id="thread-a",
            invocation_fingerprint="fingerprint",
        )
        consumed_again = self.store.consume(
            thread_id="thread-a",
            invocation_fingerprint="fingerprint",
        )

        self.assertEqual(consumed, grant)
        self.assertIsNone(consumed_again)

    def test_grant_cannot_cross_thread_or_changed_arguments(self):
        request = _request(self.store)
        self.store.approve(request.request_id, thread_id="thread-a")

        self.assertIsNone(self.store.consume(
            thread_id="thread-b",
            invocation_fingerprint="fingerprint",
        ))
        self.assertIsNone(self.store.consume(
            thread_id="thread-a",
            invocation_fingerprint="changed",
        ))
        self.assertIsNotNone(self.store.consume(
            thread_id="thread-a",
            invocation_fingerprint="fingerprint",
        ))

    def test_request_and_grant_expire(self):
        request = _request(self.store)
        self.clock.now += 11
        self.assertIsNone(self.store.approve(
            request.request_id,
            thread_id="thread-a",
        ))

        request = _request(self.store, fingerprint="second")
        self.store.approve(request.request_id, thread_id="thread-a")
        self.clock.now += 11
        self.assertIsNone(self.store.consume(
            thread_id="thread-a",
            invocation_fingerprint="second",
        ))

    def test_denial_removes_pending_request(self):
        request = _request(self.store)

        self.assertTrue(self.store.deny(
            request.request_id,
            thread_id="thread-a",
        ))
        self.assertFalse(self.store.deny(
            request.request_id,
            thread_id="thread-a",
        ))

    @patch("cyberclaw.core.tools.approval.secrets.token_urlsafe")
    def test_grant_uses_an_opaque_token(self, mock_token) -> None:
        mock_token.side_effect = ["request-id", "opaque-token", "grant-id"]
        request = _request(self.store)

        grant = self.store.approve(
            request.request_id,
            thread_id="thread-a",
        )

        self.assertEqual(grant.token, "opaque-token")
        self.assertNotIn("fingerprint", grant.token)


if __name__ == "__main__":
    unittest.main()

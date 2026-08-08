from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """One pending user decision bound to an exact normalized invocation."""

    request_id: str
    thread_id: str
    tool_name: str
    canonical_arguments: str
    invocation_fingerprint: str
    reason: str
    expires_at: float


@dataclass(frozen=True, slots=True)
class ApprovalGrant:
    """Short-lived, one-use capability created only after user approval."""

    grant_id: str
    token: str
    thread_id: str
    invocation_fingerprint: str
    expires_at: float


class ApprovalStore:
    """Thread-safe in-memory store for pending requests and one-use grants."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds 必须大于 0")
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._requests: dict[str, ApprovalRequest] = {}
        self._request_ids_by_scope: dict[tuple[str, str], str] = {}
        self._grants_by_token: dict[str, ApprovalGrant] = {}
        self._grant_tokens_by_scope: dict[tuple[str, str], str] = {}

    @property
    def ttl_seconds(self) -> float:
        return self._ttl_seconds

    def _cleanup(self, now: float) -> None:
        expired_requests = [
            request_id
            for request_id, request in self._requests.items()
            if request.expires_at <= now
        ]
        for request_id in expired_requests:
            request = self._requests.pop(request_id)
            self._request_ids_by_scope.pop(
                (request.thread_id, request.invocation_fingerprint),
                None,
            )
        for token, grant in list(self._grants_by_token.items()):
            if grant.expires_at <= now:
                self._grants_by_token.pop(token, None)
                self._grant_tokens_by_scope.pop(
                    (grant.thread_id, grant.invocation_fingerprint),
                    None,
                )

    def request(
        self,
        *,
        thread_id: str,
        tool_name: str,
        canonical_arguments: str,
        invocation_fingerprint: str,
        reason: str,
    ) -> ApprovalRequest:
        now = self._clock()
        scope = (thread_id, invocation_fingerprint)
        with self._lock:
            self._cleanup(now)
            existing_id = self._request_ids_by_scope.get(scope)
            if existing_id is not None:
                return self._requests[existing_id]

            request = ApprovalRequest(
                request_id=secrets.token_urlsafe(6),
                thread_id=thread_id,
                tool_name=tool_name,
                canonical_arguments=canonical_arguments,
                invocation_fingerprint=invocation_fingerprint,
                reason=reason,
                expires_at=now + self._ttl_seconds,
            )
            self._requests[request.request_id] = request
            self._request_ids_by_scope[scope] = request.request_id
            return request

    def approve(
        self,
        request_id: str,
        *,
        thread_id: str,
    ) -> ApprovalGrant | None:
        now = self._clock()
        with self._lock:
            self._cleanup(now)
            request = self._requests.get(request_id)
            if request is None or request.thread_id != thread_id:
                return None

            self._requests.pop(request_id)
            scope = (request.thread_id, request.invocation_fingerprint)
            self._request_ids_by_scope.pop(scope, None)
            token = secrets.token_urlsafe(24)
            grant = ApprovalGrant(
                grant_id=secrets.token_urlsafe(8),
                token=token,
                thread_id=request.thread_id,
                invocation_fingerprint=request.invocation_fingerprint,
                expires_at=now + self._ttl_seconds,
            )
            self._grants_by_token[token] = grant
            self._grant_tokens_by_scope[scope] = token
            return grant

    def deny(self, request_id: str, *, thread_id: str) -> bool:
        now = self._clock()
        with self._lock:
            self._cleanup(now)
            request = self._requests.get(request_id)
            if request is None or request.thread_id != thread_id:
                return False
            self._requests.pop(request_id)
            self._request_ids_by_scope.pop(
                (request.thread_id, request.invocation_fingerprint),
                None,
            )
            return True

    def consume(
        self,
        *,
        thread_id: str,
        invocation_fingerprint: str,
    ) -> ApprovalGrant | None:
        """Consume the exact matching grant; a successful grant is never reused."""

        now = self._clock()
        scope = (thread_id, invocation_fingerprint)
        with self._lock:
            self._cleanup(now)
            token = self._grant_tokens_by_scope.pop(scope, None)
            if token is None:
                return None
            return self._grants_by_token.pop(token, None)

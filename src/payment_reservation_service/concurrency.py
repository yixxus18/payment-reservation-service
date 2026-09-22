"""Fencing-token abstractions for externally coordinated workers."""

from threading import Lock
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from redis import Redis


class FencingTokenProvider(Protocol):
    """Issue monotonically increasing tokens for a named protected resource."""

    def next_token(self, resource: str) -> int:
        """Return the next token for ``resource``."""


class RedisFencingTokenProvider:
    """Redis-backed fencing-token source for externally coordinated workers."""

    def __init__(
        self,
        client: "Redis",
        key_prefix: str = "payment-reservation-service:fencing-token:",
    ) -> None:
        self._client = client
        self._key_prefix = key_prefix

    def next_token(self, resource: str) -> int:
        """Atomically issue the next token using Redis ``INCR``."""
        return int(self._client.incr(f"{self._key_prefix}{resource}"))


class FakeFencingTokenProvider:
    """Thread-safe in-memory fencing-token source for focused tests."""

    def __init__(self) -> None:
        self._tokens: dict[str, int] = {}
        self._lock = Lock()

    def next_token(self, resource: str) -> int:
        """Issue the next strictly increasing token for ``resource``."""
        with self._lock:
            token = self._tokens.get(resource, 0) + 1
            self._tokens[resource] = token
            return token

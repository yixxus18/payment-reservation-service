"""Typed outbox event contract and in-memory test repository."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    """An event durably queued for downstream delivery."""

    event_id: str
    event_type: str
    aggregate_id: str
    payload: Mapping[str, Any]
    occurred_at: datetime


class OutboxRepository(Protocol):
    """Storage operations required to persist outbox events idempotently."""

    def record(self, event: OutboxEvent) -> bool:
        """Store an event once, returning whether this call inserted it."""

    def get(self, event_id: str) -> OutboxEvent | None:
        """Return the event stored under ``event_id``."""


class FakeOutboxRepository:
    """Thread-safe in-memory outbox fake with event-id deduplication."""

    def __init__(self) -> None:
        self._events: dict[str, OutboxEvent] = {}
        self._lock = Lock()

    def record(self, event: OutboxEvent) -> bool:
        """Insert ``event`` exactly once by event id."""
        with self._lock:
            if event.event_id in self._events:
                return False
            self._events[event.event_id] = event
            return True

    def get(self, event_id: str) -> OutboxEvent | None:
        """Return an event by id."""
        with self._lock:
            return self._events.get(event_id)

    def events(self) -> tuple[OutboxEvent, ...]:
        """Return the recorded events in insertion order for assertions."""
        with self._lock:
            return tuple(self._events.values())

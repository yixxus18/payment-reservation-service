"""Reservation persistence abstraction and an in-memory test fake."""

from collections.abc import Callable
from datetime import datetime
from threading import Lock
from typing import Protocol

from payment_reservation_service.domain import (
    CreateReservationResult,
    Reservation,
    ReservationStatus,
    TransitionResult,
)


class RepositoryUnavailable(RuntimeError):
    """Raised when the application was not configured with persistence."""


class ReservationRepository(Protocol):
    """Storage operations required by reservation routes."""

    def create(self, reservation: Reservation) -> CreateReservationResult:
        """Persist a reservation, applying idempotency semantics."""

    def get(self, reservation_id: str) -> Reservation | None:
        """Return a reservation by identifier."""

    def transition(
        self,
        reservation_id: str,
        target_status: ReservationStatus,
        now: datetime,
        fencing_token: int | None = None,
    ) -> TransitionResult:
        """Conditionally move a reservation, rejecting non-increasing fencing tokens."""


class UnavailableReservationRepository:
    """Explicit non-persistent repository used when MongoDB is not configured."""

    def create(self, reservation: Reservation) -> CreateReservationResult:
        raise RepositoryUnavailable("MongoDB is not configured for this service")

    def get(self, reservation_id: str) -> Reservation | None:
        raise RepositoryUnavailable("MongoDB is not configured for this service")

    def transition(
        self,
        reservation_id: str,
        target_status: ReservationStatus,
        now: datetime,
        fencing_token: int | None = None,
    ) -> TransitionResult:
        raise RepositoryUnavailable("MongoDB is not configured for this service")


class FakeReservationRepository:
    """Thread-safe in-memory repository for focused tests and local wiring checks."""

    def __init__(self) -> None:
        self._by_id: dict[str, Reservation] = {}
        self._idempotency_ids: dict[str, str] = {}
        self._lock = Lock()

    def create(self, reservation: Reservation) -> CreateReservationResult:
        with self._lock:
            existing_id = self._idempotency_ids.get(reservation.idempotency_key)
            if existing_id is not None:
                existing = self._by_id[existing_id]
                return CreateReservationResult(
                    reservation=existing,
                    created=False,
                    fingerprint_matches=(
                        existing.request_fingerprint == reservation.request_fingerprint
                    ),
                )

            self._by_id[reservation.id] = reservation
            self._idempotency_ids[reservation.idempotency_key] = reservation.id
            return CreateReservationResult(
                reservation=reservation,
                created=True,
                fingerprint_matches=True,
            )

    def get(self, reservation_id: str) -> Reservation | None:
        with self._lock:
            return self._by_id.get(reservation_id)

    def transition(
        self,
        reservation_id: str,
        target_status: ReservationStatus,
        now: datetime,
        fencing_token: int | None = None,
    ) -> TransitionResult:
        with self._lock:
            current = self._by_id.get(reservation_id)
            if (
                current is None
                or current.status is not ReservationStatus.PENDING
                or current.expires_at <= now
                or (
                    fencing_token is not None
                    and current.last_fencing_token is not None
                    and fencing_token <= current.last_fencing_token
                )
            ):
                return TransitionResult(reservation=current, applied=False)

            updated = Reservation(
                id=current.id,
                payment_reference=current.payment_reference,
                amount=current.amount,
                currency=current.currency,
                status=target_status,
                expires_at=current.expires_at,
                created_at=current.created_at,
                updated_at=now,
                version=current.version + 1,
                idempotency_key=current.idempotency_key,
                request_fingerprint=current.request_fingerprint,
                last_fencing_token=(
                    fencing_token
                    if fencing_token is not None
                    else current.last_fencing_token
                ),
            )
            self._by_id[reservation_id] = updated
            return TransitionResult(reservation=updated, applied=True)


RepositoryFactory = Callable[[], ReservationRepository]

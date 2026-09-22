"""Typed reservation domain models."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class ReservationStatus(StrEnum):
    """States a reservation can occupy."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class Reservation:
    """An amount reserved for a payment reference."""

    id: str
    payment_reference: str
    amount: Decimal
    currency: str
    status: ReservationStatus
    expires_at: datetime
    created_at: datetime
    updated_at: datetime
    version: int
    idempotency_key: str
    request_fingerprint: str
    last_fencing_token: int | None = None


@dataclass(frozen=True, slots=True)
class CreateReservationResult:
    """The outcome of a create request under an idempotency key."""

    reservation: Reservation
    created: bool
    fingerprint_matches: bool


@dataclass(frozen=True, slots=True)
class TransitionResult:
    """The outcome of a conditional reservation state transition."""

    reservation: Reservation | None
    applied: bool

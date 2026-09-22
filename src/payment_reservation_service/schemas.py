"""HTTP request and response schemas for reservation operations."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field, field_serializer, field_validator

from payment_reservation_service.domain import Reservation, ReservationStatus


class ReservationCreateRequest(BaseModel):
    """Data required to reserve a payment amount."""

    payment_reference: Annotated[str, Field(min_length=1, max_length=128)]
    amount: Annotated[Decimal, Field(gt=Decimal("0"), max_digits=18, decimal_places=6)]
    currency: Annotated[str, Field(min_length=3, max_length=3)]
    expires_in_seconds: Annotated[int, Field(gt=0, le=86_400)] = 300

    @field_validator("amount", mode="before")
    @classmethod
    def reject_float_amounts(cls, value: object) -> object:
        """Prevent binary floating-point values from entering money handling."""
        if isinstance(value, float):
            raise ValueError("amount must be a decimal string or integer, not a float")
        return value

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        normalized = value.upper()
        if not normalized.isalpha():
            raise ValueError("currency must contain three letters")
        return normalized

    @field_serializer("amount")
    def serialize_amount(self, amount: Decimal) -> str:
        """Keep request fingerprint material decimal and JSON-safe."""
        return str(amount)


class ReservationResponse(BaseModel):
    """Public representation of a reservation."""

    id: str
    payment_reference: str
    amount: Decimal
    currency: str
    status: ReservationStatus
    expires_at: datetime
    created_at: datetime
    updated_at: datetime
    version: int

    @field_serializer("amount")
    def serialize_amount(self, amount: Decimal) -> str:
        """Return money as a string, never a JSON float."""
        return str(amount)

    @classmethod
    def from_domain(cls, reservation: Reservation) -> "ReservationResponse":
        """Convert a domain entity without exposing idempotency internals."""
        return cls(
            id=reservation.id,
            payment_reference=reservation.payment_reference,
            amount=reservation.amount,
            currency=reservation.currency,
            status=reservation.status,
            expires_at=reservation.expires_at,
            created_at=reservation.created_at,
            updated_at=reservation.updated_at,
            version=reservation.version,
        )


class ProblemDetails(BaseModel):
    """RFC 9457-style error response body."""

    type: str
    title: str
    status: int
    detail: str

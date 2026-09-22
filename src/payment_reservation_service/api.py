"""Reservation HTTP routes and dependency injection."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse

from payment_reservation_service.domain import Reservation, ReservationStatus
from payment_reservation_service.repository import (
    RepositoryUnavailable,
    ReservationRepository,
)
from payment_reservation_service.schemas import (
    ProblemDetails,
    ReservationCreateRequest,
    ReservationResponse,
)

router = APIRouter(prefix="/reservations", tags=["reservations"])


def get_reservation_repository(request: Request) -> ReservationRepository:
    """Get the explicitly configured repository from application state."""
    return request.app.state.reservation_repository


def problem_response(
    status: int,
    title: str,
    detail: str,
    problem_type: str,
) -> JSONResponse:
    """Produce a compact RFC 9457-style JSON problem response."""
    return JSONResponse(
        status_code=status,
        content=ProblemDetails(
            type=f"https://payment-reservation-service.dev/problems/{problem_type}",
            title=title,
            status=status,
            detail=detail,
        ).model_dump(),
        media_type="application/problem+json",
    )


def _fingerprint(request: ReservationCreateRequest) -> str:
    payload = request.model_dump(mode="json")
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256(serialized.encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(UTC)


@router.post(
    "",
    response_model=ReservationResponse,
    responses={
        201: {"model": ReservationResponse},
        400: {"model": ProblemDetails},
        409: {"model": ProblemDetails},
        503: {"model": ProblemDetails},
    },
)
def create_reservation(
    payload: ReservationCreateRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    repository: ReservationRepository = Depends(get_reservation_repository),
) -> JSONResponse:
    """Create one reservation or safely replay a matching request."""
    if idempotency_key is None or not idempotency_key.strip():
        return problem_response(
            400,
            "Idempotency-Key is required",
            "Provide a non-empty Idempotency-Key header for reservation creation.",
            "idempotency-key-required",
        )

    now = _utc_now()
    reservation = Reservation(
        id=str(uuid4()),
        payment_reference=payload.payment_reference,
        amount=payload.amount,
        currency=payload.currency,
        status=ReservationStatus.PENDING,
        expires_at=now + timedelta(seconds=payload.expires_in_seconds),
        created_at=now,
        updated_at=now,
        version=0,
        idempotency_key=idempotency_key,
        request_fingerprint=_fingerprint(payload),
    )
    try:
        outcome = repository.create(reservation)
    except RepositoryUnavailable as exc:
        return problem_response(503, "Persistence unavailable", str(exc), "persistence-unavailable")

    if not outcome.fingerprint_matches:
        return problem_response(
            409,
            "Idempotency key request mismatch",
            "The Idempotency-Key was already used with a different reservation request.",
            "idempotency-key-reused",
        )

    response = JSONResponse(
        status_code=201 if outcome.created else 200,
        content=ReservationResponse.from_domain(outcome.reservation).model_dump(mode="json"),
    )
    if outcome.created:
        response.headers["Location"] = f"/reservations/{outcome.reservation.id}"
    return response


@router.get(
    "/{reservation_id}",
    response_model=ReservationResponse,
    responses={404: {"model": ProblemDetails}, 503: {"model": ProblemDetails}},
)
def get_reservation(
    reservation_id: str,
    repository: ReservationRepository = Depends(get_reservation_repository),
) -> ReservationResponse | JSONResponse:
    """Fetch a reservation by identifier."""
    try:
        reservation = repository.get(reservation_id)
    except RepositoryUnavailable as exc:
        return problem_response(503, "Persistence unavailable", str(exc), "persistence-unavailable")
    if reservation is None:
        return problem_response(404, "Reservation not found", "No reservation matches this id.", "not-found")
    return ReservationResponse.from_domain(reservation)


@router.post(
    "/{reservation_id}/confirm",
    response_model=ReservationResponse,
    responses={404: {"model": ProblemDetails}, 409: {"model": ProblemDetails}, 503: {"model": ProblemDetails}},
)
def confirm_reservation(
    reservation_id: str,
    repository: ReservationRepository = Depends(get_reservation_repository),
) -> ReservationResponse | JSONResponse:
    """Confirm a pending, non-expired reservation."""
    return _transition_reservation(reservation_id, ReservationStatus.CONFIRMED, repository)


@router.post(
    "/{reservation_id}/cancel",
    response_model=ReservationResponse,
    responses={404: {"model": ProblemDetails}, 409: {"model": ProblemDetails}, 503: {"model": ProblemDetails}},
)
def cancel_reservation(
    reservation_id: str,
    repository: ReservationRepository = Depends(get_reservation_repository),
) -> ReservationResponse | JSONResponse:
    """Cancel a pending, non-expired reservation."""
    return _transition_reservation(reservation_id, ReservationStatus.CANCELLED, repository)


def _transition_reservation(
    reservation_id: str,
    target_status: ReservationStatus,
    repository: ReservationRepository,
) -> ReservationResponse | JSONResponse:
    try:
        outcome = repository.transition(reservation_id, target_status, _utc_now())
    except RepositoryUnavailable as exc:
        return problem_response(503, "Persistence unavailable", str(exc), "persistence-unavailable")

    if outcome.applied and outcome.reservation is not None:
        return ReservationResponse.from_domain(outcome.reservation)
    if outcome.reservation is None:
        return problem_response(404, "Reservation not found", "No reservation matches this id.", "not-found")
    if outcome.reservation.expires_at <= _utc_now():
        return problem_response(
            409,
            "Reservation expired",
            "Expired reservations cannot be transitioned.",
            "reservation-expired",
        )
    return problem_response(
        409,
        "Invalid reservation state",
        f"Only pending reservations can be {target_status.value}.",
        "invalid-reservation-state",
    )

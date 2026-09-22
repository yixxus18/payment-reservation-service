from datetime import UTC, datetime, timedelta
from decimal import Decimal

from payment_reservation_service.concurrency import RedisFencingTokenProvider
from payment_reservation_service.domain import Reservation, ReservationStatus
from payment_reservation_service.outbox import FakeOutboxRepository, OutboxEvent
from payment_reservation_service.repository import FakeReservationRepository


def _pending_reservation(*, last_fencing_token: int | None = None) -> Reservation:
    now = datetime.now(UTC)
    return Reservation(
        id="reservation-123",
        payment_reference="payment-123",
        amount=Decimal("10.50"),
        currency="USD",
        status=ReservationStatus.PENDING,
        expires_at=now + timedelta(minutes=5),
        created_at=now,
        updated_at=now,
        version=0,
        idempotency_key="reservation-123-key",
        request_fingerprint="fingerprint",
        last_fencing_token=last_fencing_token,
    )


class _FakeRedisClient:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.incremented_keys: list[str] = []

    def incr(self, key: str) -> int:
        self.incremented_keys.append(key)
        value = self.values.get(key, 0) + 1
        self.values[key] = value
        return value


def test_redis_fencing_token_provider_increments_a_namespaced_resource_key() -> None:
    client = _FakeRedisClient()
    provider = RedisFencingTokenProvider(client)

    assert provider.next_token("reservation-123") == 1
    assert provider.next_token("reservation-123") == 2
    assert provider.next_token("reservation-456") == 1
    assert client.incremented_keys == [
        "payment-reservation-service:fencing-token:reservation-123",
        "payment-reservation-service:fencing-token:reservation-123",
        "payment-reservation-service:fencing-token:reservation-456",
    ]


def test_fake_rejects_a_stale_fencing_token_without_transitioning() -> None:
    repository = FakeReservationRepository()
    reservation = _pending_reservation(last_fencing_token=2)
    repository.create(reservation)

    outcome = repository.transition(
        reservation.id,
        ReservationStatus.CONFIRMED,
        datetime.now(UTC),
        fencing_token=1,
    )

    assert outcome.applied is False
    assert outcome.reservation == reservation
    assert repository.get(reservation.id) == reservation


def test_fake_outbox_records_an_event_exactly_once_by_event_id() -> None:
    repository = FakeOutboxRepository()
    event = OutboxEvent(
        event_id="event-123",
        event_type="reservation.confirmed",
        aggregate_id="reservation-123",
        payload={"reservation_id": "reservation-123"},
        occurred_at=datetime.now(UTC),
    )
    replay = OutboxEvent(
        event_id=event.event_id,
        event_type="reservation.cancelled",
        aggregate_id=event.aggregate_id,
        payload={"reservation_id": event.aggregate_id},
        occurred_at=event.occurred_at + timedelta(seconds=1),
    )

    assert repository.record(event) is True
    assert repository.record(replay) is False
    assert repository.get(event.event_id) == event
    assert repository.events() == (event,)

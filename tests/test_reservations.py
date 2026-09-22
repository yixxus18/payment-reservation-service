from datetime import UTC, datetime, timedelta
from decimal import Decimal
import sys
from types import ModuleType

import pytest
from fastapi.testclient import TestClient

from payment_reservation_service.config import Settings
from payment_reservation_service.domain import Reservation, ReservationStatus
from payment_reservation_service.main import create_app
from payment_reservation_service.mongo import MongoReservationRepository
from payment_reservation_service.repository import (
    FakeReservationRepository,
    RepositoryUnavailable,
    ReservationRepository,
)


def _test_app(repository: ReservationRepository) -> TestClient:
    return TestClient(
        create_app(
            Settings(
                app_name="payment-reservation-service",
                environment="test",
                log_level="INFO",
            ),
            repository=repository,
        )
    )


def _payload(amount: str = "10.50") -> dict[str, object]:
    return {
        "payment_reference": "payment-123",
        "amount": amount,
        "currency": "usd",
        "expires_in_seconds": 300,
    }


def test_create_requires_idempotency_key() -> None:
    response = _test_app(FakeReservationRepository()).post("/reservations", json=_payload())

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["type"].endswith("/idempotency-key-required")


def test_create_replay_and_key_reuse_follow_idempotency_contract() -> None:
    client = _test_app(FakeReservationRepository())
    headers = {"Idempotency-Key": "create-payment-123"}

    created = client.post("/reservations", json=_payload(), headers=headers)
    replayed = client.post("/reservations", json=_payload(), headers=headers)
    conflict = client.post("/reservations", json=_payload("11.50"), headers=headers)

    assert created.status_code == 201
    assert created.headers["location"] == f"/reservations/{created.json()['id']}"
    assert created.json()["amount"] == "10.50"
    assert created.json()["currency"] == "USD"
    assert replayed.status_code == 200
    assert replayed.json()["id"] == created.json()["id"]
    assert conflict.status_code == 409
    assert conflict.json()["type"].endswith("/idempotency-key-reused")


def test_get_confirm_and_cancel_routes_use_injected_repository() -> None:
    client = _test_app(FakeReservationRepository())
    created = client.post(
        "/reservations",
        json=_payload(),
        headers={"Idempotency-Key": "transition-payment-123"},
    )
    reservation_id = created.json()["id"]

    fetched = client.get(f"/reservations/{reservation_id}")
    confirmed = client.post(f"/reservations/{reservation_id}/confirm")
    cancelled = client.post(f"/reservations/{reservation_id}/cancel")

    assert fetched.status_code == 200
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    assert confirmed.json()["version"] == 1
    assert cancelled.status_code == 409
    assert cancelled.json()["type"].endswith("/invalid-reservation-state")


def test_unconfigured_application_does_not_claim_persistence() -> None:
    client = TestClient(
        create_app(
            Settings(
                app_name="payment-reservation-service",
                environment="test",
                log_level="INFO",
            )
        )
    )

    response = client.post(
        "/reservations",
        json=_payload(),
        headers={"Idempotency-Key": "unconfigured"},
    )

    assert response.status_code == 503
    assert response.json()["type"].endswith("/persistence-unavailable")


def test_fake_rejects_transition_for_expired_reservation() -> None:
    repository = FakeReservationRepository()
    now = datetime.now(UTC)
    reservation = Reservation(
        id="expired-reservation",
        payment_reference="payment-expired",
        amount=Decimal("1.00"),
        currency="USD",
        status=ReservationStatus.PENDING,
        expires_at=now - timedelta(seconds=1),
        created_at=now - timedelta(minutes=1),
        updated_at=now - timedelta(minutes=1),
        version=3,
        idempotency_key="expired-key",
        request_fingerprint="fingerprint",
    )
    repository.create(reservation)

    outcome = repository.transition(
        reservation.id, ReservationStatus.CONFIRMED, datetime.now(UTC)
    )

    assert outcome.applied is False
    assert outcome.reservation == reservation


class _RecordingCollection:
    def __init__(self) -> None:
        self.indexes: list[tuple[object, dict[str, object]]] = []

    def create_index(self, fields: object, **options: object) -> None:
        self.indexes.append((fields, options))


def test_mongo_repository_creates_idempotency_and_expiry_indexes() -> None:
    collection = _RecordingCollection()

    MongoReservationRepository(collection).ensure_indexes()

    assert collection.indexes == [
        (
            [("idempotency_key", 1)],
            {"name": "reservation_idempotency_key_unique", "unique": True},
        ),
        ([("expires_at", 1)], {"name": "reservation_expiry"}),
    ]


def _reservation() -> Reservation:
    now = datetime.now(UTC)
    return Reservation(
        id="mongo-reservation",
        payment_reference="payment-mongo",
        amount=Decimal("10.50"),
        currency="USD",
        status=ReservationStatus.PENDING,
        expires_at=now + timedelta(minutes=5),
        created_at=now,
        updated_at=now,
        version=0,
        idempotency_key="mongo-key",
        request_fingerprint="mongo-fingerprint",
    )


class _PyMongoError(Exception):
    pass


class _DuplicateKeyError(_PyMongoError):
    pass


class _UnavailableCollection:
    def insert_one(self, document: object) -> None:
        raise _PyMongoError("MongoDB is unavailable")

    def find_one(self, query: object) -> None:
        raise _PyMongoError("MongoDB is unavailable")


class _DuplicateKeyCollection:
    def __init__(self, document: dict[str, object]) -> None:
        self.document = document

    def insert_one(self, document: object) -> None:
        raise _DuplicateKeyError("duplicate idempotency key")

    def find_one(self, query: object) -> dict[str, object]:
        return self.document


class _UpdateUnavailableCollection:
    def __init__(self, document: dict[str, object]) -> None:
        self.document = document

    def find_one(self, query: object) -> dict[str, object]:
        return self.document

    def find_one_and_update(self, *args: object, **kwargs: object) -> None:
        raise _PyMongoError("MongoDB is unavailable")


def _patch_pymongo_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        MongoReservationRepository,
        "_duplicate_key_error",
        staticmethod(lambda: _DuplicateKeyError),
    )
    monkeypatch.setattr(
        MongoReservationRepository,
        "_pymongo_error",
        staticmethod(lambda: _PyMongoError),
    )


def test_mongo_repository_uses_timezone_aware_decoding(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class _Client:
        def __getitem__(self, database_name: str) -> object:
            return {"reservations": object()}

    def mongo_client(*args: object, **kwargs: object) -> _Client:
        calls.append((args, kwargs))
        return _Client()

    pymongo_module = ModuleType("pymongo")
    pymongo_module.MongoClient = mongo_client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pymongo", pymongo_module)

    MongoReservationRepository.from_url("mongodb://example.test", "reservations")

    assert calls == [(("mongodb://example.test",), {"tz_aware": True})]


def test_mongo_repository_preserves_duplicate_key_idempotency_handling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pymongo_errors(monkeypatch)
    reservation = _reservation()
    repository = MongoReservationRepository(
        _DuplicateKeyCollection(
            {
                "_id": reservation.id,
                "payment_reference": reservation.payment_reference,
                "amount": str(reservation.amount),
                "currency": reservation.currency,
                "status": reservation.status.value,
                "expires_at": reservation.expires_at,
                "created_at": reservation.created_at,
                "updated_at": reservation.updated_at,
                "version": reservation.version,
                "idempotency_key": reservation.idempotency_key,
                "request_fingerprint": reservation.request_fingerprint,
                "last_fencing_token": reservation.last_fencing_token,
            }
        )
    )

    outcome = repository.create(reservation)

    assert outcome.created is False
    assert outcome.fingerprint_matches is True
    assert outcome.reservation == reservation


def test_mongo_repository_translates_operation_failures_to_repository_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pymongo_errors(monkeypatch)
    repository = MongoReservationRepository(_UnavailableCollection())

    with pytest.raises(RepositoryUnavailable, match="MongoDB operation failed"):
        repository.create(_reservation())
    with pytest.raises(RepositoryUnavailable, match="MongoDB operation failed"):
        repository.get("mongo-reservation")


def test_mongo_repository_translates_transition_update_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pymongo_errors(monkeypatch)
    monkeypatch.setattr(
        MongoReservationRepository,
        "_return_document_after",
        staticmethod(object),
    )
    reservation = _reservation()
    repository = MongoReservationRepository(
        _UpdateUnavailableCollection(
            {
                "_id": reservation.id,
                "payment_reference": reservation.payment_reference,
                "amount": str(reservation.amount),
                "currency": reservation.currency,
                "status": reservation.status.value,
                "expires_at": reservation.expires_at,
                "created_at": reservation.created_at,
                "updated_at": reservation.updated_at,
                "version": reservation.version,
                "idempotency_key": reservation.idempotency_key,
                "request_fingerprint": reservation.request_fingerprint,
                "last_fencing_token": reservation.last_fencing_token,
            }
        )
    )

    with pytest.raises(RepositoryUnavailable, match="MongoDB operation failed"):
        repository.transition(
            reservation.id,
            ReservationStatus.CONFIRMED,
            datetime.now(UTC),
        )


def test_create_route_openapi_declares_a_201_response() -> None:
    responses = _test_app(FakeReservationRepository()).app.openapi()["paths"][
        "/reservations"
    ]["post"]["responses"]

    assert "201" in responses
    assert "application/json" in responses["201"]["content"]


def test_create_route_returns_503_for_mongo_operation_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pymongo_errors(monkeypatch)
    client = _test_app(MongoReservationRepository(_UnavailableCollection()))

    response = client.post(
        "/reservations",
        json=_payload(),
        headers={"Idempotency-Key": "mongo-unavailable"},
    )

    assert response.status_code == 503
    assert response.json()["type"].endswith("/persistence-unavailable")

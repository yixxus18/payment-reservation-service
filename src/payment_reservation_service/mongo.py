"""MongoDB reservation repository and index setup."""

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any

from payment_reservation_service.domain import (
    CreateReservationResult,
    Reservation,
    ReservationStatus,
    TransitionResult,
)
from payment_reservation_service.repository import (
    RepositoryUnavailable,
    ReservationRepository,
)


class MongoReservationRepository(ReservationRepository):
    """MongoDB implementation of the reservation repository contract."""

    def __init__(self, collection: Any) -> None:
        self._collection = collection

    @classmethod
    def from_url(cls, mongo_url: str, database_name: str) -> "MongoReservationRepository":
        """Create a repository from explicit MongoDB configuration."""
        from pymongo import MongoClient

        client = MongoClient(mongo_url, tz_aware=True)
        return cls(client[database_name]["reservations"])

    def ensure_indexes(self) -> None:
        """Create the indexes used for idempotency and expiry lookups."""
        self._collection.create_index(
            [("idempotency_key", 1)],
            name="reservation_idempotency_key_unique",
            unique=True,
        )
        self._collection.create_index(
            [("expires_at", 1)],
            name="reservation_expiry",
        )

    def create(self, reservation: Reservation) -> CreateReservationResult:
        """Insert once, then compare the stored fingerprint on duplicate keys."""
        try:
            self._collection.insert_one(_to_document(reservation))
        except self._duplicate_key_error():
            try:
                existing = self._collection.find_one(
                    {"idempotency_key": reservation.idempotency_key}
                )
            except self._pymongo_error() as exc:
                raise _repository_unavailable(exc) from exc
            if existing is None:
                raise RuntimeError("idempotency record disappeared after duplicate key")
            stored = _from_document(existing)
            return CreateReservationResult(
                reservation=stored,
                created=False,
                fingerprint_matches=(
                    stored.request_fingerprint == reservation.request_fingerprint
                ),
            )
        except self._pymongo_error() as exc:
            raise _repository_unavailable(exc) from exc

        return CreateReservationResult(
            reservation=reservation,
            created=True,
            fingerprint_matches=True,
        )

    def get(self, reservation_id: str) -> Reservation | None:
        try:
            document = self._collection.find_one({"_id": reservation_id})
        except self._pymongo_error() as exc:
            raise _repository_unavailable(exc) from exc
        return _from_document(document) if document is not None else None

    def transition(
        self,
        reservation_id: str,
        target_status: ReservationStatus,
        now: datetime,
        fencing_token: int | None = None,
    ) -> TransitionResult:
        """Use an optimistic, single-document conditional update for transitions."""
        current = self.get(reservation_id)
        if current is None:
            return TransitionResult(reservation=None, applied=False)

        predicate: dict[str, Any] = {
            "_id": reservation_id,
            "status": ReservationStatus.PENDING.value,
            "expires_at": {"$gt": now},
            "version": current.version,
        }
        updates: dict[str, Any] = {
            "status": target_status.value,
            "updated_at": now,
        }
        if fencing_token is not None:
            predicate["$or"] = [
                {"last_fencing_token": {"$exists": False}},
                {"last_fencing_token": None},
                {"last_fencing_token": {"$lt": fencing_token}},
            ]
            updates["last_fencing_token"] = fencing_token

        try:
            document = self._collection.find_one_and_update(
                predicate,
                {
                    "$set": updates,
                    "$inc": {"version": 1},
                },
                return_document=self._return_document_after(),
            )
        except self._pymongo_error() as exc:
            raise _repository_unavailable(exc) from exc
        if document is not None:
            return TransitionResult(reservation=_from_document(document), applied=True)

        return TransitionResult(reservation=self.get(reservation_id), applied=False)

    @staticmethod
    def _duplicate_key_error() -> type[Exception]:
        from pymongo.errors import DuplicateKeyError

        return DuplicateKeyError

    @staticmethod
    def _pymongo_error() -> type[Exception]:
        from pymongo.errors import PyMongoError

        return PyMongoError

    @staticmethod
    def _return_document_after() -> Any:
        from pymongo import ReturnDocument

        return ReturnDocument.AFTER


def _repository_unavailable(error: Exception) -> RepositoryUnavailable:
    """Map driver failures to the persistence boundary exposed by the API."""
    return RepositoryUnavailable(f"MongoDB operation failed: {error}")


def _to_document(reservation: Reservation) -> dict[str, Any]:
    return {
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


def ensure_outbox_collection(database: Any) -> Any:
    """Return the outbox collection after creating its idempotency indexes."""
    collection = database["outbox"]
    collection.create_index(
        [("event_id", 1)],
        name="outbox_event_id_unique",
        unique=True,
    )
    collection.create_index(
        [("occurred_at", 1)],
        name="outbox_occurred_at",
    )
    return collection


def _from_document(document: Mapping[str, Any]) -> Reservation:
    return Reservation(
        id=str(document["_id"]),
        payment_reference=str(document["payment_reference"]),
        amount=Decimal(str(document["amount"])),
        currency=str(document["currency"]),
        status=ReservationStatus(str(document["status"])),
        expires_at=document["expires_at"],
        created_at=document["created_at"],
        updated_at=document["updated_at"],
        version=int(document["version"]),
        idempotency_key=str(document["idempotency_key"]),
        request_fingerprint=str(document["request_fingerprint"]),
        last_fencing_token=(
            int(document["last_fencing_token"])
            if document.get("last_fencing_token") is not None
            else None
        ),
    )

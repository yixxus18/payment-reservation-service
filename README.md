# Payment Reservation Service

FastAPI service for creating and transitioning payment reservations.

## Persistence configuration

The process starts without MongoDB configuration so the health endpoint remains
available for local development. Reservation endpoints return `503` with an
RFC 9457-style problem response until persistence is configured; the service
never treats in-memory process state as durable storage.

Configure MongoDB explicitly to enable reservation persistence and create the
required indexes at application startup:

```bash
export MONGODB_URI="mongodb://localhost:27017"
export MONGODB_DATABASE="payment_reservations"
```

The `reservations` collection receives a unique `idempotency_key` index and an
`expires_at` index. Tests inject `FakeReservationRepository`; it is not used by
the production application.

## Concurrency and outbox contracts

`concurrency.py` provides a fencing-token protocol, a Redis-backed adapter,
and an in-memory fake for unit tests. `RedisFencingTokenProvider` uses Redis
`INCR` against namespaced resource keys to atomically issue tokens. Reservation
transitions may receive an optional fencing token. When provided, MongoDB
accepts it only when it is greater than the stored `last_fencing_token`; the
same predicate is enforced atomically by the fake. The HTTP routes do not
require a token and retain their existing behavior.

`outbox.py` defines the typed outbox event and repository contracts, plus an
in-memory fake that records each `event_id` exactly once. Mongo callers can use
`ensure_outbox_collection(database)` to obtain the `outbox` collection with its
unique `event_id` and delivery-order indexes. The current API exposes these
contracts but does not yet run a background outbox publisher.

## Reservation API

`POST /reservations` requires `Idempotency-Key` and accepts decimal money as a
string (not a JSON float):

```json
{
  "payment_reference": "payment-123",
  "amount": "10.50",
  "currency": "USD",
  "expires_in_seconds": 300
}
```

A fresh create returns `201 Created` and a `Location` header. An identical
replay returns `200 OK` with the original reservation. Reusing the key with a
different request returns `409 Conflict`. Reservations can be fetched with
`GET /reservations/{id}` and transitioned with:

- `POST /reservations/{id}/confirm`
- `POST /reservations/{id}/cancel`

Only pending, non-expired reservations can be transitioned. MongoDB performs
these transitions with a single-document conditional update over status,
expiry, and version predicates.

## Run tests locally

Use Python 3.11 through 3.13 and install the development dependencies:

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

Run the API locally with:

```bash
uvicorn payment_reservation_service.main:app --reload
```

## Run with Docker Compose

Start the API, MongoDB, and Redis:

```bash
docker compose up --build
```

The API is available at `http://localhost:8000`, and its health endpoint is
`http://localhost:8000/health`. MongoDB remains the authoritative reservation
store. Redis is available for workers that use the fencing-token adapter; the
current HTTP API does not instantiate it or publish outbox events in the
background.

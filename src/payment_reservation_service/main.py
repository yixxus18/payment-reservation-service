"""FastAPI application entry point."""

from fastapi import FastAPI

from payment_reservation_service.api import router
from payment_reservation_service.config import Settings
from payment_reservation_service.mongo import MongoReservationRepository
from payment_reservation_service.repository import (
    ReservationRepository,
    UnavailableReservationRepository,
)


def _configured_repository(settings: Settings) -> ReservationRepository:
    """Build persistence only when a MongoDB URI was explicitly configured."""
    if not settings.mongodb_uri:
        return UnavailableReservationRepository()

    repository = MongoReservationRepository.from_url(
        settings.mongodb_uri, settings.mongodb_database
    )
    repository.ensure_indexes()
    return repository


def create_app(
    settings: Settings | None = None,
    repository: ReservationRepository | None = None,
) -> FastAPI:
    """Create the service application with an injected persistence implementation."""
    application_settings = settings or Settings.from_environment()
    app = FastAPI(title=application_settings.app_name)
    app.state.reservation_repository = repository or _configured_repository(
        application_settings
    )

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        """Report that the API process is ready to accept requests."""
        return {
            "status": "ok",
            "service": application_settings.app_name,
            "environment": application_settings.environment,
        }

    app.include_router(router)
    return app


app = create_app()

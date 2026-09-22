"""Application configuration."""

from dataclasses import dataclass
from os import getenv


@dataclass(frozen=True, slots=True)
class Settings:
    """Settings loaded from environment variables."""

    app_name: str
    environment: str
    log_level: str
    mongodb_uri: str | None = None
    mongodb_database: str = "payment_reservations"

    @classmethod
    def from_environment(cls) -> "Settings":
        """Load configuration with safe local-development defaults."""
        return cls(
            app_name=getenv("APP_NAME", "payment-reservation-service"),
            environment=getenv("APP_ENV", "development"),
            log_level=getenv("LOG_LEVEL", "INFO"),
            mongodb_uri=getenv("MONGODB_URI"),
            mongodb_database=getenv("MONGODB_DATABASE", "payment_reservations"),
        )

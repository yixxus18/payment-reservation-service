from fastapi.testclient import TestClient

from payment_reservation_service.config import Settings
from payment_reservation_service.main import create_app


def test_health_endpoint_reports_service_status() -> None:
    app = create_app(
        Settings(
            app_name="payment-reservation-service",
            environment="test",
            log_level="INFO",
        )
    )

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "payment-reservation-service",
        "environment": "test",
    }

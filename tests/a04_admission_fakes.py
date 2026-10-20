"""Explicit test double for already-authorized A04 business-flow unit tests.

The PostgreSQL quota lifecycle suite remains the source of truth for real
admission, ticket integrity, idempotency and commit behavior. This helper is
only for legacy inbound unit tests whose purpose is downstream delivery,
guardrail and escalation behavior; it prevents those tests from accidentally
using the production path without a reservation.
"""

import json
from types import SimpleNamespace


def install_mock_a04_admission(monkeypatch):
    from src.core.billing import quota_reservations

    class MockQuotaReservations:
        def __init__(self, _pool):
            pass

        async def reserve(self, _operation):
            return {"status": "reserved", "ticket": object()}

        async def commit(self, _operation, _ticket, _final):
            return json.loads(_final.serialized)

        async def renew(self, _operation, ticket):
            return ticket

        async def release(self, _operation, _ticket):
            return {"status": "released"}

    # The fake ticket is not a production capability. The final-proof factory
    # is stubbed at this unit boundary; real proof and atomic persistence are
    # exercised by tests/core/test_quota_reservation_lifecycle.py.
    monkeypatch.setattr(quota_reservations, "QuotaReservations", MockQuotaReservations)
    monkeypatch.setattr(
        quota_reservations, "validate_final_response",
        lambda _operation, _ticket, response, **_kwargs: SimpleNamespace(
            serialized=json.dumps(response.model_dump(mode="json")),
        ),
    )
    return MockQuotaReservations

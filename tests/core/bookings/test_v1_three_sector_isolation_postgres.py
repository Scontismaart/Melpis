"""Tenant boundaries shared by the three V1 booking sectors."""
from itertools import permutations

import pytest

from src.core.db.repositories.booking_allocation_repo import AllocationDenied
from tests.core.bookings.test_allocation_engine_postgres import create_environment
from tests.core.documents.test_b02_publication_postgres import (
    _claim,
    _publish,
    _vectors,
)

pytestmark = pytest.mark.asyncio


async def test_three_sector_catalog_capacity_booking_and_document_isolation(pg_pool, repo):
    sectors = ("ristorante", "parrucchiere", "centro_estetico")
    tenants = [await create_environment(pg_pool, sector) for sector in sectors]
    await pg_pool.execute(
        "UPDATE organizations SET plan='business', subscription_status='trialing', "
        "trial_start=now()-interval '1 hour', trial_end=now()+interval '6 days' "
        "WHERE id=ANY($1::uuid[])",
        [tenant.org for tenant in tenants],
    )

    bookings = {}
    documents = {}
    for tenant, sector in zip(tenants, sectors, strict=True):
        catalog = await tenant.publisher.read(tenant.org)
        assert catalog["published"]["sector"] == sector
        booking = await tenant.booking()
        bookings[tenant.org] = booking
        details = {"party_size": 12} if sector == "ristorante" else {}
        request = tenant.request(booking, **details)
        # The previous tenant already holds the same slot and resource key.
        assert (await tenant.observe(request))["outcome"] == "available"
        assert (await tenant.apply(request))["outcome"] == "allocated"

        claim = await _claim(repo, tenant.org, payload=f"synthetic {sector}")
        await _publish(repo, tenant.org, claim, [f"Reference for {sector}"])
        documents[tenant.org] = claim["document"]["id"]

    for tenant, sector in zip(tenants, sectors, strict=True):
        probe = await tenant.booking()
        details = {"party_size": 12} if sector == "ristorante" else {}
        assert (await tenant.observe(tenant.request(probe, **details)))["outcome"] == "unavailable"
        hits = await repo.search_similar(tenant.org, _vectors()[0])
        assert [str(hit["document_id"]) for hit in hits] == [str(documents[tenant.org])]

    for caller, target in permutations(tenants, 2):
        request = target.request(bookings[target.org], action="cancel", allocation_revision=1)
        with pytest.raises(AllocationDenied):
            await target.engine.apply(target.org, caller.auth, request)
        with pytest.raises(AllocationDenied):
            await caller.engine.apply(caller.org, caller.auth, caller.request(
                bookings[target.org], action="cancel", allocation_revision=1,
            ))

    for tenant in tenants:
        request = tenant.request(bookings[tenant.org], action="cancel", allocation_revision=1)
        assert (await tenant.apply(request))["outcome"] == "cancelled"

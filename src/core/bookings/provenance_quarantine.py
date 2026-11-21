"""Explicit application fence for individually reviewed booking IDs."""
from contextlib import asynccontextmanager
from uuid import UUID


async def quarantine_predicate(conn, relation="bookings", booking_column="id"):
    """Build a tenant-bound SQL predicate for a trusted, static relation alias.

    Pre-migration databases lack the registry and cannot contain reviewed rows.
    Database errors propagate; a failed registry read must never permit access.
    The returned predicate executes in the same statement as the actual read.
    """
    if (relation, booking_column) not in {
        ("bookings", "id"), ("b", "id"),
        ("external_booking_sync", "internal_booking_id"), ("s", "internal_booking_id"),
        ("e", "source_id"),
    }:
        raise ValueError("Unrecognized quarantine predicate scope")
    if not await conn.fetchval("SELECT to_regclass('public.booking_provenance_quarantine') IS NOT NULL"):
        return "TRUE"
    return (
        f"({relation}.{booking_column} IS NULL OR "
        f"NOT public.booking_provenance_is_quarantined({relation}.organization_id,{relation}.{booking_column}))"
    )


async def booking_is_quarantined(conn, organization_id, booking_id):
    if not await conn.fetchval("SELECT to_regclass('public.booking_provenance_quarantine') IS NOT NULL"):
        return False
    return bool(await conn.fetchval(
        "SELECT public.booking_provenance_is_quarantined($1,$2)",
        UUID(str(organization_id)), UUID(str(booking_id)),
    ))


async def confirmation_visibility_predicate(conn, relation="messages"):
    """Exclude only exact same-tenant outbound confirmations for held bookings."""
    if relation not in {"messages", "m"}:
        raise ValueError("Unrecognized confirmation predicate scope")
    if not await conn.fetchval("SELECT to_regclass('public.booking_provenance_quarantine') IS NOT NULL"):
        return "TRUE"
    return f"NOT public.booking_provenance_confirmation_is_quarantined({relation}.organization_id,{relation}.id)"


async def confirmation_is_quarantined(conn, organization_id, message_id):
    if not await conn.fetchval("SELECT to_regclass('public.booking_provenance_quarantine') IS NOT NULL"):
        return False
    return bool(await conn.fetchval(
        "SELECT public.booking_provenance_confirmation_is_quarantined($1,$2)",
        UUID(str(organization_id)), UUID(str(message_id)),
    ))


@asynccontextmanager
async def booking_provider_guard(pool, organization_id, booking_id):
    """Serialize a booking-addressed provider handoff with owner enrollment."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock_shared(hashtextextended($1::text,606))", str(organization_id))
        yield not await booking_is_quarantined(conn, organization_id, booking_id)

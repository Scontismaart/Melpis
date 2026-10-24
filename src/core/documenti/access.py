"""Current plan entitlement for knowledge use; no quota consumption here."""
from datetime import datetime, timezone
from uuid import UUID

from src.core.billing.plans import PLANS
from src.core.billing.quota_reservations import QuotaConflict, _period


def snapshot_allows_rag(snapshot, organization_id):
    if not isinstance(snapshot, dict):
        return False
    slug = snapshot.get("plan")
    if snapshot.get("subscription_status") == "trialing" and not slug:
        slug = "pro"  # Preserve the existing authorized-trial capability fallback.
    plan = PLANS.get(slug)
    if plan is None or not plan.has_rag or snapshot.get("ai_accounting_blocked"):
        return False
    try:
        _period(snapshot, snapshot.get("verified_now") or datetime.now(timezone.utc), UUID(str(organization_id)))
    except (QuotaConflict, ValueError, TypeError, AttributeError):
        return False
    return True


async def rag_access(repo, organization_id):
    try:
        pool = repo.pool
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT *,clock_timestamp() AS verified_now FROM organizations WHERE id=$1", UUID(str(organization_id)))
        return snapshot_allows_rag(dict(row) if row else None, organization_id)
    except Exception:
        return False

import os
from uuid import UUID

from src.core.gdpr.operations import Operations, OperationUnavailable


async def propagate_delete_to_airtable(org_id: str) -> bool:
    """Legacy unverified endpoint: no destructive request is permitted."""
    return False


async def propagate_delete_to_softr(org_id: str) -> bool:
    """No provider scope/correlation receipt contract was certified."""
    return False


async def propagate_hard_delete(org_id: str) -> dict:
    results = {
        "airtable": await propagate_delete_to_airtable(org_id),
        "softr": await propagate_delete_to_softr(org_id),
    }
    return results


class DefinitelyNotSent(RuntimeError):
    """Adapter proved local rejection BEFORE handing anything to HTTP."""


async def propagate_operation(pool, organization_id, operation_id, provider, adapter):
    """Trusted adapter interface; no external adapter is enabled by this PR.

    An adapter must verify provider/account/scope and normalize an authenticated
    deletion receipt. A bare 2xx/202 or a transport exception remains uncertain.
    Same-operation retries require native provider idempotency, never a timer.
    """
    if os.getenv('GDPR_PROVIDER_ERASURE_EXECUTION_ENABLED') != 'true':
        raise OperationUnavailable('Provider erasure requires separate cutover authorization')
    if getattr(adapter, 'provider', None) != provider or getattr(adapter, 'idempotent_delete', False) is not True:
        raise OperationUnavailable('Verified, idempotent provider adapter required')
    org, oid = UUID(str(organization_id)), UUID(str(operation_id))
    async with pool.acquire() as conn:
        authorized = await conn.fetchval(
            '''SELECT EXISTS(SELECT 1 FROM gdpr_operations o JOIN gdpr_operation_steps s
                 ON s.operation_id=o.id AND s.organization_id=$1
               WHERE o.organization_id=$1 AND o.id=$2 AND o.kind='erase' AND o.status='partial'
                 AND s.name='local' AND s.status='confirmed')''', org, oid,
        )
    if not authorized:
        raise OperationUnavailable('Local erasure and durable authorization required')
    ops = Operations(pool)
    fence = await ops.claim_provider(org, oid, provider)
    if fence is None:
        return 'not_dispatched'
    try:
        receipt = await adapter.delete(org, oid, idempotency_key=str(oid))
    except DefinitelyNotSent:
        await ops.provider_result(org, oid, provider, fence, not_sent=True)
        return 'not_sent'
    except Exception:
        await ops.provider_result(org, oid, provider, fence)
        return 'uncertain'
    return await ops.provider_result(org, oid, provider, fence, receipt=receipt)


async def reconcile_operation(pool, organization_id, operation_id, provider, adapter):
    """Safe read/verification, never another destructive POST after uncertainty."""
    if os.getenv('GDPR_PROVIDER_ERASURE_EXECUTION_ENABLED') != 'true':
        raise OperationUnavailable('Provider verification requires separate authorization')
    if getattr(adapter, 'provider', None) != provider:
        raise OperationUnavailable('Provider adapter mismatch')
    receipt = await adapter.lookup_deletion(UUID(str(organization_id)), UUID(str(operation_id)))
    await Operations(pool).reconcile_provider(organization_id, operation_id, provider, receipt=receipt)

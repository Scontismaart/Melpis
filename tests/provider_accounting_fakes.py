"""In-process context double for code paths whose provider transport is mocked."""

import threading
import uuid
from datetime import datetime, timedelta, timezone

from src.core.provider_accounting import ProviderAccountingContext


def provider_context_stub(*, task_type="customer_message", organization_id=None,
                          quota_operation=None, quota_ticket=None):
    context = object.__new__(ProviderAccountingContext)
    context.pool = None
    context.organization_id = uuid.UUID(str(organization_id)) if organization_id else uuid.uuid4()
    context.actor_auth_user_id = uuid.uuid4()
    context.worker_message_id = None
    context.task_type = task_type
    context._quota_operation = quota_operation
    context._quota_ticket = quota_ticket
    context._quota_lease_failed = False
    context._quota_lease_task = None
    context.context = {"trace_id": str(uuid.uuid4())}
    context.billing_snapshot = {
        "subscription_status": "trialing",
        "trial_start": datetime.now(timezone.utc) - timedelta(hours=1),
        "trial_end": datetime.now(timezone.utc) + timedelta(days=2),
        "messages_limit": 150,
        "messages_used_this_period": 0,
        "ai_accounting_blocked": False,
    }
    context.execution_id = uuid.uuid4()
    context._loop = None
    context._closed = False
    context._state_lock = threading.Lock()

    async def close():
        context._closed = True

    context.close = close
    return context

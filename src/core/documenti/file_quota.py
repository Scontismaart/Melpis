"""Original-upload byte accounting; separate from A03/A04 and index storage.

Call admission while holding the existing organization publication lock.
No commercial quantities are configured until the founder approves them.
"""
from uuid import UUID

from src.core.billing import plans
from src.core.documenti.limits import UPLOAD_BYTES


class FileQuotaError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def validate_file_evidence(size, digest):
    if type(size) is not int or not 0 <= size <= UPLOAD_BYTES:
        raise ValueError("invalid_original_file_bytes")
    if size == 0:
        if digest is not None:
            raise ValueError("unexpected_original_file_hash")
    elif (not isinstance(digest, str) or len(digest) != 64
          or any(c not in "0123456789abcdef" for c in digest)):
        raise ValueError("invalid_original_file_hash")


def policy_limit(organization):
    plan = plans.PLANS.get(organization.get("plan"))
    trial = organization.get("subscription_status") == "trialing"
    value = (plans.TRIAL_RAG_FILE_BYTES_LIMIT if trial else
             getattr(plan, "rag_file_bytes_limit", None))
    # NULL is pending approval, never unlimited. Booleans are not byte counts.
    return (value if type(value) is int and value >= 0 else None), ("trial" if trial else "paid")


async def snapshot(conn, organization_id):
    org = UUID(str(organization_id))
    row = await conn.fetchrow("SELECT plan,subscription_status FROM organizations WHERE id=$1", org)
    if row is None:
        raise ValueError("organization_unavailable")
    usage = await conn.fetchrow("""SELECT
        COALESCE(SUM(COALESCE(file_bytes,0)),0) AS committed,
        COALESCE(SUM(GREATEST(COALESCE(index_file_bytes,0)-COALESCE(file_bytes,0),0)),0) AS reserved,
        COUNT(*) FILTER (WHERE file_bytes IS NULL) AS unknown
        FROM documents WHERE organization_id=$1""", org)
    committed, reserved, unknown = (int(usage[k]) for k in ("committed", "reserved", "unknown"))
    limit, scope = policy_limit(dict(row))
    occupied = committed + reserved
    status = ("not_configured" if limit is None else
              "historical_usage_unknown" if unknown else
              "over_limit" if occupied > limit else
              "full" if occupied == limit else "available")
    return {
        "status": status, "limit_bytes": limit, "scope": scope,
        "committed_bytes": committed, "reserved_bytes": reserved,
        "occupied_bytes": occupied, "unknown_documents": unknown,
        "remaining_bytes": None if limit is None or unknown else max(0, limit - occupied),
        "originals_retained": False,
    }


async def admit(conn, organization_id, document, incoming_bytes):
    """Check the proposed peak, including all other outstanding claims.

    A replacement holds max(old,new), not old+new. A downgrade may retain or
    reduce a known existing charge, but may never authorize additional bytes.
    Expired claims keep their reservation until an explicit fenced transition.
    """
    old = document["file_bytes"] if document is not None else 0
    if incoming_bytes == 0:
        if old is None:
            raise FileQuotaError("file_quota_historical_unknown")
        if old is not None and old > 0:
            raise FileQuotaError("file_source_change_requires_delete")
        return
    if old is not None and old > 0 and incoming_bytes <= old:
        return
    current = await snapshot(conn, organization_id)
    if current["limit_bytes"] is None:
        raise FileQuotaError("file_quota_not_configured")
    if current["unknown_documents"]:
        raise FileQuotaError("file_quota_historical_unknown")
    existing_peak = (max(old or 0, document["index_file_bytes"] or 0)
                     if document is not None else 0)
    projected = current["occupied_bytes"] - existing_peak + max(old or 0, incoming_bytes)
    if projected > current["limit_bytes"]:
        raise FileQuotaError("file_quota_exceeded")


def http_error(error):
    from fastapi import HTTPException
    messages = {
        "file_quota_not_configured": "Quota file in attesa di configurazione. Le altre funzioni della knowledge base restano disponibili.",
        "file_quota_historical_unknown": "Verifica dei byte delle fonti storiche necessaria prima di nuovi caricamenti file.",
        "file_quota_exceeded": "Spazio file del piano esaurito. Elimina una fonte o sostituiscila con un file più piccolo.",
        "file_source_change_requires_delete": "Elimina esplicitamente la fonte file prima di convertirla in una fonte testuale.",
    }
    return HTTPException(503 if error.code == "file_quota_not_configured" else 409,
                         messages[error.code])

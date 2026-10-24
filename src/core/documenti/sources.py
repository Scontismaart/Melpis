"""Identity/version evidence for document answers; names are never identities."""
from uuid import UUID


def identities(sources):
    if not isinstance(sources, list) or not sources:
        return None
    result = {}
    try:
        for source in sources:
            doc = UUID(str(source["document_id"]))
            revision = source["revision"]
            if type(revision) is not int or revision < 1 or (doc in result and result[doc] != revision):
                return None
            result[doc] = revision
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    return result


async def sources_current(conn, organization_id, sources):
    requested = identities(sources)
    if not requested:
        return False
    rows = await conn.fetch("""SELECT id,revision FROM documents
        WHERE organization_id=$1 AND id=ANY($2::uuid[]) AND is_active
          AND index_verified AND stato='indicizzata'""", UUID(str(organization_id)), list(requested))
    return len(rows) == len(requested) and all(requested[row["id"]] == row["revision"] for row in rows)


async def authorized_sources(repo, organization_id, sources):
    from src.core.documenti.access import rag_access
    if not await rag_access(repo, organization_id):
        return False
    async with repo.pool.acquire() as conn:
        return await sources_current(conn, organization_id, sources)

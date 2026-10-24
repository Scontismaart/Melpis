"""Unit tests per DocumentRepository.delete_document (Invariante 1 - Tenant Isolation).

Verifica:
1. Atomicità della cancellazione (transazione unica per chunks e documents).
2. Tenant scoping rigoroso: la cancellazione dei chunks filtra esplicitamente per organization_id.
3. Protezione cross-tenant: cancellare un documento dell'Org A non tocca i chunk dell'Org B.
4. Rollback in caso di fallimento della query padre.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock
import pytest

from src.core.db.repositories.document_repo import DocumentRepository


@pytest.fixture
def mock_pool():
    pool = MagicMock()
    conn = AsyncMock()
    # Transaction context manager mock (in asyncpg, conn.transaction() is synchronous returning an async ctx mgr)
    tx = AsyncMock()
    conn.transaction = MagicMock(return_value=tx)
    tx.__aenter__.return_value = tx
    tx.__aexit__.return_value = None

    # Connection acquire context manager mock
    acquire_ctx = AsyncMock()
    acquire_ctx.__aenter__.return_value = conn
    acquire_ctx.__aexit__.return_value = None
    pool.acquire.return_value = acquire_ctx

    return pool, conn, tx


@pytest.mark.asyncio
async def test_delete_document_atomic_transaction(mock_pool):
    pool, conn, tx = mock_pool
    repo = DocumentRepository(pool)
    org_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())

    conn.fetchrow.side_effect = [{"id": org_id}, {"id": doc_id}]

    result = await repo.delete_document(org_id, doc_id)

    assert result == 1
    # Verifica che la transazione sia stata aperta ed eseguita
    # Publication takes an org-scoped lock before the delete transaction.
    assert conn.transaction.call_count == 2
    assert tx.__aenter__.await_count == 2
    assert tx.__aexit__.await_count == 2

    # Verifica query cancellazione chunk con doppio filtro (document_id E organization_id)
    delete_call = next(call for call in conn.execute.await_args_list
                       if "DELETE FROM document_chunks" in call.args[0])
    chunk_query = delete_call.args[0]
    chunk_args = delete_call.args[1:]
    assert "DELETE FROM document_chunks" in chunk_query
    assert "document_id = $1" in chunk_query
    assert "organization_id = $2" in chunk_query
    assert chunk_args == (doc_id, org_id)

    # Verifica query cancellazione documento con doppio filtro (id E organization_id)
    delete_doc_call = conn.fetchrow.await_args_list[1]
    doc_query = delete_doc_call.args[0]
    doc_args = delete_doc_call.args[1:]
    assert "DELETE FROM documents" in doc_query
    assert "id = $1" in doc_query
    assert "organization_id = $2" in doc_query
    assert doc_args == (doc_id, org_id)


@pytest.mark.asyncio
async def test_delete_document_cross_tenant_isolation(mock_pool):
    """Verifica che se Org A tenta di cancellare un documento,
    la query NON possa cancellare i chunk di Org B."""
    pool, conn, _ = mock_pool
    repo = DocumentRepository(pool)
    org_a = str(uuid.uuid4())
    org_b = str(uuid.uuid4())
    doc_b_id = str(uuid.uuid4())

    # Documento appartiene a Org B, quindi Org A non lo trova
    conn.fetchrow.side_effect = [{"id": org_a}, None]

    result = await repo.delete_document(org_a, doc_b_id)

    assert result == 0
    # La query su document_chunks ha cercato solo chunk con organization_id = org_a
    delete_call = next(call for call in conn.execute.await_args_list
                       if "DELETE FROM document_chunks" in call.args[0])
    chunk_args = delete_call.args[1:]
    assert chunk_args[1] == org_a  # Non org_b!
    assert chunk_args[1] != org_b

    # La query su documents ha cercato solo con organization_id = org_a
    doc_args = conn.fetchrow.await_args_list[1].args[1:]
    assert doc_args[1] == org_a
    assert doc_args[1] != org_b


@pytest.mark.asyncio
async def test_delete_document_rollback_on_failure(mock_pool):
    """Verifica che se la cancellazione del documento fallisce,
    l'eccezione propaga e la transazione fa rollback."""
    pool, conn, tx = mock_pool
    repo = DocumentRepository(pool)
    org_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())

    conn.execute.return_value = "DELETE 5"
    conn.fetchrow.side_effect = [{"id": org_id}, RuntimeError("Database connection lost")]

    with pytest.raises(RuntimeError, match="Database connection lost"):
        await repo.delete_document(org_id, doc_id)

    # Verifica che tx.__aexit__ sia stato chiamato con l'eccezione (triggerando il rollback)
    assert tx.__aexit__.await_count == 2
    assert all(call.args[0] is RuntimeError for call in tx.__aexit__.await_args_list)


@pytest.mark.asyncio
async def test_delete_document_multi_tenant_chunk_preservation():
    """Simula uno stato con due organizzazioni (Org A e Org B) e verifica che
    cancellare il documento di Org A lasci intatti al 100% i chunk di Org B."""
    org_a = str(uuid.uuid4())
    org_b = str(uuid.uuid4())
    doc_a_id = str(uuid.uuid4())
    doc_b_id = str(uuid.uuid4())

    # Database in-memory finto
    db_documents = {
        (doc_a_id, org_a): {"id": doc_a_id, "org_id": org_a, "name": "doc_a"},
        (doc_b_id, org_b): {"id": doc_b_id, "org_id": org_b, "name": "doc_b"},
    }
    db_chunks = [
        {"id": "c1", "document_id": doc_a_id, "organization_id": org_a, "content": "chunk 1 Org A"},
        {"id": "c2", "document_id": doc_a_id, "organization_id": org_a, "content": "chunk 2 Org A"},
        {"id": "c3", "document_id": doc_b_id, "organization_id": org_b, "content": "chunk 1 Org B"},
        {"id": "c4", "document_id": doc_b_id, "organization_id": org_b, "content": "chunk 2 Org B"},
    ]

    pool = MagicMock()
    conn = AsyncMock()
    tx = AsyncMock()
    conn.transaction = MagicMock(return_value=tx)
    tx.__aenter__.return_value = tx
    tx.__aexit__.return_value = None

    acquire_ctx = AsyncMock()
    acquire_ctx.__aenter__.return_value = conn
    acquire_ctx.__aexit__.return_value = None
    pool.acquire.return_value = acquire_ctx

    async def mock_execute(query, *args):
        nonlocal db_chunks
        if "DELETE FROM document_chunks" in query:
            doc_id, org_id = args
            db_chunks = [
                c for c in db_chunks
                if not (c["document_id"] == doc_id and c["organization_id"] == org_id)
            ]

    async def mock_fetchrow(query, *args):
        nonlocal db_documents
        if "FROM organizations" in query:
            return {"id": args[0]}
        if "DELETE FROM documents" in query:
            doc_id, org_id = args
            key = (doc_id, org_id)
            if key in db_documents:
                return db_documents.pop(key)
        return None

    conn.execute.side_effect = mock_execute
    conn.fetchrow.side_effect = mock_fetchrow

    repo = DocumentRepository(pool)

    # Org A cancella il suo documento
    res = await repo.delete_document(org_a, doc_a_id)
    assert res == 1

    # Verifiche stato:
    # 1. Il documento di Org A è eliminato
    assert (doc_a_id, org_a) not in db_documents
    # 2. Il documento di Org B è INTATTO
    assert (doc_b_id, org_b) in db_documents

    # 3. I chunk di Org A sono stati eliminati
    remaining_chunks_a = [c for c in db_chunks if c["organization_id"] == org_a]
    assert len(remaining_chunks_a) == 0

    # 4. I chunk di Org B sono INTATTI al 100%
    remaining_chunks_b = [c for c in db_chunks if c["organization_id"] == org_b]
    assert len(remaining_chunks_b) == 2
    assert [c["id"] for c in remaining_chunks_b] == ["c3", "c4"]


@pytest.mark.asyncio
async def test_knowledge_version_reads_tenant_epoch(mock_pool):
    pool, conn, _ = mock_pool
    repo = DocumentRepository(pool)
    org_id = uuid.uuid4()
    conn.fetchval.return_value = 12

    version = await repo.knowledge_version(org_id)

    assert version == 12
    query, scoped_org_id = conn.fetchval.await_args.args
    assert "knowledge_epoch" in query
    assert "WHERE id=$1" in query
    assert scoped_org_id == org_id


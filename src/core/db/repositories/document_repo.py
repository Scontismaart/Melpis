import hashlib
import json
import uuid

from src.core.db.scoping import TenantScopedRepository
from src.core.db.repositories.document_publication import DocumentPublication


class DocumentRepository(DocumentPublication, TenantScopedRepository):
    """Repository specializzato per Documenti (RAG pgvector), Chunks e Cache Semantica FAQ."""

    def __init__(self, pool):
        self.pool = pool

    @staticmethod
    def _vec_str(embedding: list) -> str:
        return "[" + ",".join(str(v) for v in embedding) + "]"

    async def create_document(self, organization_id, nome, tipo="upload",
                                fonte="", caricato_il=None, is_active=True,
                                stato="indicizzata", errore="", metadata=None):
        async with self._publication(organization_id) as (conn, _org):
            row = await conn.fetchrow("""
                INSERT INTO documents (
                    id, organization_id, nome, tipo, fonte,
                    is_active, stato, errore, metadata,
                    caricato_il, updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, COALESCE($10, NOW()), NOW())
                RETURNING *
            """, uuid.uuid4(), organization_id, nome, tipo, fonte,
            is_active, stato, errore, json.dumps(metadata or {}), caricato_il)
            result = dict(row)
            if isinstance(result.get("metadata"), str):
                result["metadata"] = json.loads(result["metadata"])
            return result

    async def get_document(self, organization_id, document_id):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT * FROM documents
                WHERE id = $1 AND organization_id = $2
            """, document_id, organization_id)
            if not row:
                return None
            res = dict(row)
            if isinstance(res.get("metadata"), str):
                res["metadata"] = json.loads(res["metadata"])
            return res

    async def update_document(self, organization_id, document_id, **fields):
        if not fields:
            return await self.get_document(organization_id, document_id)

        vals = [document_id, organization_id]
        for col in ("nome", "tipo", "fonte", "is_active", "stato", "errore", "metadata"):
            value = json.dumps(fields[col]) if col == "metadata" and col in fields else fields.get(col)
            vals.extend((col in fields, value))

        sql = """
            UPDATE documents
            SET nome = CASE WHEN $3 THEN $4 ELSE nome END,
                tipo = CASE WHEN $5 THEN $6 ELSE tipo END,
                fonte = CASE WHEN $7 THEN $8 ELSE fonte END,
                is_active = CASE WHEN $9 THEN $10 ELSE is_active END,
                stato = CASE WHEN $11 THEN $12 ELSE stato END,
                errore = CASE WHEN $13 THEN $14 ELSE errore END,
                metadata = CASE WHEN $15 THEN $16::jsonb ELSE metadata END,
                index_verified = CASE WHEN $3 OR $5 OR $7 OR $11 OR $13 OR $15 THEN FALSE ELSE index_verified END,
                updated_at = NOW()
            WHERE id = $1 AND organization_id = $2
            RETURNING *
        """
        async with self._publication(organization_id) as (conn, _org):
            row = await conn.fetchrow(sql, *vals)
            if not row:
                return None
            res = dict(row)
            if isinstance(res.get("metadata"), str):
                res["metadata"] = json.loads(res["metadata"])
            return res

    async def toggle_document_active(self, organization_id, document_id):
        async with self._publication(organization_id) as (conn, _org):
            row = await conn.fetchrow("""
                UPDATE documents
                SET is_active = NOT is_active, updated_at = NOW()
                WHERE id = $1 AND organization_id = $2
                RETURNING *
            """, document_id, organization_id)
            if not row:
                return None
            res = dict(row)
            if isinstance(res.get("metadata"), str):
                res["metadata"] = json.loads(res["metadata"])
            return res

    async def delete_document_chunks(self, organization_id, document_id):
        async with self._publication(organization_id) as (conn, _org):
            res = await conn.execute("""
                DELETE FROM document_chunks
                WHERE document_id = $1 AND organization_id = $2
            """, document_id, organization_id)
            return int(res.split()[-1]) if res else 0

    async def add_chunk(self, organization_id, document_id, chunk_index,
                         content, embedding, metadata=None):
        async with self._publication(organization_id) as (conn, _org):
            vec_str = self._vec_str(embedding)
            row = await conn.fetchrow("""
                INSERT INTO document_chunks (id, organization_id, document_id,
                                             chunk_index, content, embedding, metadata)
                VALUES ($1, $2, $3, $4, $5, $6::vector, $7::jsonb)
                RETURNING *
            """, uuid.uuid4(), organization_id, document_id,
            chunk_index, content, vec_str,
            json.dumps(metadata or {}))
            result = dict(row)
            if isinstance(result.get("metadata"), str):
                result["metadata"] = json.loads(result["metadata"])
            return result

    async def search_similar(self, organization_id, embedding, k=5, only_active=True):
        """Ricerca semantica vettoriale su document_chunks. Implementazione canonica unificata."""
        from src.core.documenti.access import rag_access
        if type(k) is not int or not 1 <= k <= 20:
            raise ValueError("invalid_retrieval_limit")
        if not await rag_access(self, organization_id):
            return []
        async with self.pool.acquire() as conn:
            vec_str = self._vec_str(embedding)
            sql = """
                SELECT dc.id, dc.content, dc.metadata, dc.chunk_index,
                       dc.document_id, d.nome as document_name, d.tipo, d.stato, d.is_active, d.revision,
                       dc.embedding <=> $2::vector AS distance
                FROM document_chunks dc
                JOIN documents d ON d.id = dc.document_id
                              AND d.organization_id = $1
                WHERE dc.organization_id = $1
                  AND d.index_verified = TRUE AND d.stato = 'indicizzata'
                  AND d.is_active = TRUE
                ORDER BY dc.embedding <=> $2::vector
                LIMIT $3
            """
            rows = await conn.fetch(sql, organization_id, vec_str, k)
            results = [dict(r) for r in rows]
            for r in results:
                if isinstance(r.get("metadata"), str):
                    r["metadata"] = json.loads(r["metadata"])
            return results

    async def list_documents(self, organization_id):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM documents WHERE organization_id = $1 ORDER BY created_at DESC",
                organization_id,
            )
            return [dict(r) for r in rows]

    async def get_ui_summary(self, organization_id):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT
                  (SELECT COUNT(*) FROM conversations
                   WHERE organization_id = $1 AND deleted_at IS NULL
                     AND ticket_status IN ('PENDING_STAFF', 'CLAIMED')) AS inbox_attivi,
                  (SELECT COUNT(*) FROM bookings
                   WHERE organization_id = $1) AS prenotazioni,
                  (SELECT COUNT(*) FROM documents
                   WHERE organization_id = $1) AS documenti,
                  (SELECT COUNT(*) FROM reviews
                   WHERE organization_id = $1 AND stato = 'bozza_generata') AS recensioni_da_approvare
            """, organization_id)
            inbox_ids = await conn.fetch("""
                SELECT id FROM conversations
                WHERE organization_id = $1 AND deleted_at IS NULL
                  AND ticket_status IN ('PENDING_STAFF', 'CLAIMED')
                ORDER BY pending_staff_at ASC NULLS LAST
            """, organization_id)
            result = dict(row)
            result["inbox_attivi_ids"] = [str(r["id"]) for r in inbox_ids]
            return result

    async def count_chunks(self, organization_id):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT COUNT(*) AS n FROM document_chunks c JOIN documents d
                    ON d.id=c.document_id AND d.organization_id=$1
                    WHERE c.organization_id=$1 AND d.is_active AND d.index_verified
                      AND d.stato='indicizzata'""",
                organization_id,
            )
            return row["n"]

    async def list_sources(self, organization_id, tipo=None):
        async with self.pool.acquire() as conn:
            if tipo:
                rows = await conn.fetch("""
                    SELECT d.id, d.nome, d.tipo, d.fonte, d.is_active, d.stato, d.errore,
                           d.metadata, d.caricato_il, d.updated_at, d.index_verified, d.revision,
                           COUNT(dc.id) AS chunk
                    FROM documents d
                    LEFT JOIN document_chunks dc ON dc.document_id = d.id
                                                  AND dc.organization_id = $1
                    WHERE d.organization_id = $1 AND d.tipo = $2
                    GROUP BY d.id
                    ORDER BY d.caricato_il DESC, d.nome
                """, organization_id, tipo)
            else:
                rows = await conn.fetch("""
                    SELECT d.id, d.nome, d.tipo, d.fonte, d.is_active, d.stato, d.errore,
                           d.metadata, d.caricato_il, d.updated_at, d.index_verified, d.revision,
                           COUNT(dc.id) AS chunk
                    FROM documents d
                    LEFT JOIN document_chunks dc ON dc.document_id = d.id
                                                  AND dc.organization_id = $1
                    WHERE d.organization_id = $1
                    GROUP BY d.id
                    ORDER BY d.caricato_il DESC, d.nome
                """, organization_id)
            results = [dict(r) for r in rows]
            for r in results:
                if isinstance(r.get("metadata"), str):
                    r["metadata"] = json.loads(r["metadata"])
                if r.get("errore"):
                    r["errore"] = "indexing_failed"
            return results

    async def list_all_active_chunks(self, organization_id):
        from src.core.documenti.access import rag_access
        if not await rag_access(self, organization_id):
            return []
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT dc.id, left(dc.content,500) AS content,
                       length(dc.content)>500 AS content_truncated,
                       jsonb_build_object('tipo',d.tipo,'fonte',d.fonte) AS metadata, dc.chunk_index,
                       dc.document_id, d.nome as document_name, d.tipo, d.stato, d.is_active
                FROM document_chunks dc
                JOIN documents d ON d.id = dc.document_id
                              AND d.organization_id = $1
                WHERE dc.organization_id = $1 AND d.is_active = TRUE
                  AND d.index_verified = TRUE AND d.stato = 'indicizzata'
                ORDER BY d.caricato_il DESC
                LIMIT 4001
            """, organization_id)
            results = [dict(r) for r in rows]
            for r in results:
                if isinstance(r.get("metadata"), str):
                    r["metadata"] = json.loads(r["metadata"])
            return results

    async def delete_document(self, organization_id, document_id):
        async with self._publication(organization_id) as (conn, _org):
            async with conn.transaction():
                await conn.execute("""
                    DELETE FROM document_chunks WHERE document_id = $1 AND organization_id = $2
                """, document_id, organization_id)
                row = await conn.fetchrow("""
                    DELETE FROM documents WHERE id = $1 AND organization_id = $2
                    RETURNING id
                """, document_id, organization_id)
                return 1 if row else 0

    # ── Guardrails: Cache FAQ Semantica ───────────────────────────

    async def knowledge_version(self, organization_id):
        async with self.pool.acquire() as conn:
            return await conn.fetchval("SELECT knowledge_epoch FROM organizations WHERE id=$1", uuid.UUID(str(organization_id)))

    async def faq_cache_lookup(self, organization_id: str, embedding: list,
                               max_distance: float = 0.08) -> dict | None:
        async with self._publication(organization_id, require_entitlement=True) as (conn, _org):
            row = await conn.fetchrow("""
                UPDATE faq_cache SET hit_count = hit_count + 1, last_used_at = NOW()
                WHERE id = (
                    SELECT id FROM faq_cache
                    WHERE organization_id = $1::uuid
                      AND knowledge_epoch = (SELECT knowledge_epoch FROM organizations WHERE id=$1::uuid)
                      AND expires_at > NOW()
                      AND (question_embedding <=> $2::vector) <= $3
                    ORDER BY question_embedding <=> $2::vector
                    LIMIT 1
                )
                  AND organization_id = $1::uuid
                RETURNING *
            """, organization_id, self._vec_str(embedding), max_distance)
            return dict(row) if row else None

    async def faq_cache_store(self, organization_id: str, question_text: str,
                              answer_text: str, embedding: list,
                              prompt_variant: str = "control",
                              ttl_hours: int = 72, expected_epoch: int | None = None) -> dict | None:
        if type(expected_epoch) is not int:
            return None
        normalized = " ".join(question_text.lower().split())
        question_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        async with self._publication(organization_id, require_entitlement=True) as (conn, _org):
            current = await conn.fetchval("SELECT knowledge_epoch FROM organizations WHERE id=$1", uuid.UUID(str(organization_id)))
            if current != expected_epoch:
                return None
            row = await conn.fetchrow("""
                INSERT INTO faq_cache (id, organization_id, question_text, question_hash,
                                       question_embedding, answer_text, prompt_variant, expires_at, knowledge_epoch)
                VALUES ($1, $2::uuid, $3, $4, $5::vector, $6, $7,
                        NOW() + ($8 || ' hours')::interval, $9)
                ON CONFLICT (organization_id, question_hash)
                    DO UPDATE SET
                        answer_text = EXCLUDED.answer_text,
                        question_embedding = EXCLUDED.question_embedding,
                        prompt_variant = EXCLUDED.prompt_variant,
                        expires_at = EXCLUDED.expires_at,
                        knowledge_epoch = EXCLUDED.knowledge_epoch
                RETURNING *
            """, uuid.uuid4(), organization_id, question_text, question_hash,
                self._vec_str(embedding), answer_text, prompt_variant, str(ttl_hours), expected_epoch)
            return dict(row)

    async def faq_cache_invalidate(self, organization_id: str) -> int:
        """Svuota la cache FAQ dell'org. Implementazione canonica unificata."""
        async with self._publication(organization_id) as (conn, _org):
            await conn.execute("UPDATE organizations SET knowledge_epoch=knowledge_epoch+1 WHERE id=$1", uuid.UUID(str(organization_id)))
            result = await conn.execute(
                "DELETE FROM faq_cache WHERE organization_id = $1::uuid",
                organization_id,
            )
            return int(result.split()[-1]) if result else 0

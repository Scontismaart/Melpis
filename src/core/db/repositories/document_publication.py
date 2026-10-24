"""Fenced publication over the existing documents/chunks, never a second index."""
from contextlib import asynccontextmanager
import json
import hashlib
import math
from uuid import UUID, uuid4


class DocumentPublication:
    @asynccontextmanager
    async def _publication(self, organization_id, *, require_entitlement=False):
        org = UUID(str(organization_id))
        async with self.pool.acquire() as conn, conn.transaction():
            organization = await conn.fetchrow("SELECT *,clock_timestamp() AS verified_now FROM organizations WHERE id=$1 FOR UPDATE", org)
            if not organization:
                raise ValueError("organization_unavailable")
            if require_entitlement:
                from src.core.documenti.access import snapshot_allows_rag
                if not snapshot_allows_rag(dict(organization), org):
                    raise ValueError("rag_not_authorized")
            await conn.execute("SELECT pg_advisory_xact_lock_shared(hashtextextended($1::text,606))", str(org))
            yield conn, org

    @staticmethod
    def _document(row):
        value = dict(row)
        if isinstance(value.get("metadata"), str):
            value["metadata"] = json.loads(value["metadata"])
        return value

    async def begin_index(self, organization_id, name, request_key, payload_hash, *,
                          tipo="upload", fonte="", document_id=None, expected_profile=None):
        key = UUID(str(request_key))
        if not isinstance(payload_hash, str) or len(payload_hash) != 64 or any(c not in "0123456789abcdef" for c in payload_hash):
            raise ValueError("invalid_payload_hash")
        async with self._publication(organization_id, require_entitlement=True) as (conn, org):
            if tipo == "dati_struttura":
                current_profile = await conn.fetchval("SELECT business_profile FROM organizations WHERE id=$1", org)
                if isinstance(current_profile, str):
                    current_profile = json.loads(current_profile)
                if not isinstance(expected_profile, dict) or current_profile != expected_profile:
                    raise ValueError("profile_changed")
            specification = {"name": name, "tipo": tipo, "fonte": fonte, "target": str(document_id) if document_id else None, "pipeline": 1}
            # Keep replay identities without retaining old filenames/URLs in
            # deleted-source tombstones. Full content already has its hash.
            spec = {"signature": hashlib.sha256(json.dumps(specification, sort_keys=True).encode()).hexdigest()}
            history = await conn.fetchrow("SELECT * FROM document_index_requests WHERE organization_id=$1 AND request_key=$2", org, key)
            existing = None
            if history:
                stored_spec = history["request_spec"]
                if isinstance(stored_spec, str):
                    stored_spec = json.loads(stored_spec)
                if history["payload_hash"] != payload_hash or stored_spec != spec or history["document_id"] is None:
                    return {"status": "conflict", "document": None}
                existing = await conn.fetchrow("SELECT * FROM documents WHERE organization_id=$1 AND id=$2 FOR UPDATE", org, history["document_id"])
                if not existing or existing["index_key"] != key:
                    return {"status": "conflict", "document": None}
            if existing:
                if existing["content_hash"] != payload_hash or (document_id and str(existing["id"]) != str(document_id)):
                    return {"status": "conflict", "document": self._document(existing)}
                if existing["index_verified"] and existing["stato"] == "indicizzata":
                    return {"status": "replay", "document": self._document(existing)}
                live = await conn.fetchval("SELECT $1::timestamptz>clock_timestamp()", existing["index_lease_until"])
                if live:
                    return {"status": "in_progress", "document": self._document(existing)}
            elif document_id:
                existing = await conn.fetchrow("SELECT * FROM documents WHERE organization_id=$1 AND id=$2 FOR UPDATE", org, UUID(str(document_id)))
                if not existing:
                    raise ValueError("document_not_found")
                if await conn.fetchval("SELECT $1::timestamptz>clock_timestamp()", existing["index_lease_until"]):
                    return {"status": "conflict", "document": None}
            token = uuid4()
            if existing:
                row = await conn.fetchrow("""UPDATE documents SET nome=$3,tipo=$4,fonte=$5,index_key=$6,content_hash=$7,
                    index_token=$8,index_fence=index_fence+1,index_lease_until=clock_timestamp()+INTERVAL '660 seconds',
                    index_verified=FALSE,stato='in_elaborazione',errore='',updated_at=NOW()
                    WHERE organization_id=$1 AND id=$2 RETURNING *""", org, existing["id"], name, tipo, fonte, key, payload_hash, token)
            else:
                row = await conn.fetchrow("""INSERT INTO documents(id,organization_id,nome,tipo,fonte,is_active,stato,
                    index_key,content_hash,index_token,index_fence,index_lease_until)
                    VALUES($1,$2,$3,$4,$5,TRUE,'in_elaborazione',$6,$7,$8,1,clock_timestamp()+INTERVAL '660 seconds') RETURNING *""",
                    uuid4(), org, name, tipo, fonte, key, payload_hash, token)
            if history is None:
                await conn.execute("""INSERT INTO document_index_requests(organization_id,request_key,document_id,payload_hash,request_spec)
                    VALUES($1,$2,$3,$4,$5::jsonb)""", org, key, row["id"], payload_hash, json.dumps(spec))
            return {"status": "claimed", "document": self._document(row), "token": token, "fence": row["index_fence"]}

    async def publish_index(self, organization_id, document_id, token, fence, chunks, embeddings, metadata):
        if (not chunks or len(chunks)>4000 or len(chunks) != len(embeddings) or any(not isinstance(c, str) or not c.strip() or len(c)>500 for c in chunks)
                or any(not isinstance(v, (list, tuple)) or len(v) != 384 or any(isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) for x in v) for v in embeddings)):
            raise ValueError("invalid_index_vectors")
        async with self._publication(organization_id, require_entitlement=True) as (conn, org):
            row = await conn.fetchrow("""SELECT * FROM documents WHERE organization_id=$1 AND id=$2
                AND index_token=$3 AND index_fence=$4 AND index_lease_until>clock_timestamp() FOR UPDATE""",
                org, UUID(str(document_id)), UUID(str(token)), fence)
            if not row:
                raise ValueError("index_claim_lost")
            await conn.execute("DELETE FROM document_chunks WHERE organization_id=$1 AND document_id=$2", org, row["id"])
            for i, (content, vector) in enumerate(zip(chunks, embeddings)):
                meta = {"fonte": row["fonte"] or row["nome"], "tipo": row["tipo"], "document_id": str(row["id"]),
                        "revision": row["revision"]+1}
                pages = metadata.get("chunk_pages", [])
                if i < len(pages) and type(pages[i]) is int and pages[i] > 0:
                    meta["pagina"] = pages[i]
                await conn.execute("""INSERT INTO document_chunks(id,organization_id,document_id,chunk_index,content,embedding,metadata)
                    VALUES($1,$2,$3,$4,$5,$6::vector,$7::jsonb)""", uuid4(), org, row["id"], i, content,
                    self._vec_str(vector), json.dumps(meta))
            ready = await conn.fetchrow("""UPDATE documents SET stato='indicizzata',index_verified=TRUE,
                revision=revision+1,metadata=$5::jsonb,index_token=NULL,index_lease_until=NULL,errore='',updated_at=NOW()
                WHERE organization_id=$1 AND id=$2 AND index_token=$3 AND index_fence=$4
                  AND index_lease_until>clock_timestamp() RETURNING *""", org, row["id"], UUID(str(token)), fence,
                json.dumps({**metadata, "chunks": len(chunks)}))
            if not ready:
                raise ValueError("index_claim_lost")
            await conn.execute("""UPDATE document_index_requests SET published_revision=$3
                WHERE organization_id=$1 AND request_key=$2""", org, row["index_key"], ready["revision"])
            return self._document(ready)

    async def fail_index(self, organization_id, document_id, token, fence, error_code):
        async with self._publication(organization_id) as (conn, org):
            return bool(await conn.fetchval("""UPDATE documents SET stato='errore',index_verified=FALSE,
                errore=$5,index_token=NULL,index_lease_until=NULL,updated_at=NOW()
                WHERE organization_id=$1 AND id=$2 AND index_token=$3 AND index_fence=$4 RETURNING TRUE""",
                org, UUID(str(document_id)), UUID(str(token)), fence, error_code))

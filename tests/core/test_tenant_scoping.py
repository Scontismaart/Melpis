"""Regressione tenant-isolation (task 4, brief 2026-08-22-tenant-scoping-wrapper-ci).

Una criticita' = un test dedicato (matrice di tracciabilita' nel brief).
Seed: due organizzazioni su pg_pool full-schema (tests/core/conftest.py,
reset_db esplicito per isolare ogni test). Le righe usano uuid4 per non
collidere tra run.
"""
import asyncio
import uuid
from datetime import datetime, timezone

import pytest

from src.core.db.repository import CoreRepository
from src.core.db.repositories.message_repo import MessageRepository
from src.core.db.scoping import ScopedConnection, TenantScopeViolation
from src.whatsapp.repository import Repository


async def _seed_org(pg_pool, name):
    org_id = uuid.uuid4()
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO organizations (id, name) VALUES ($1, $2)", org_id, name
        )
    return org_id


async def _seed_contact(pg_pool, org_id):
    contact_id = uuid.uuid4()
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO contacts (id, organization_id, phone_number) VALUES ($1, $2, $3)",
            contact_id, org_id, f"+39{uuid.uuid4().int % 10**10:010d}",
        )
    return contact_id


async def _seed_conversation(pg_pool, org_id, contact_id):
    conv_id = uuid.uuid4()
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO conversations (id, organization_id, contact_id) VALUES ($1, $2, $3)",
            conv_id, org_id, contact_id,
        )
    return conv_id


@pytest.fixture
async def two_orgs(pg_pool, reset_db):
    """Due org con contatto + conversazione ciascuna."""
    a = await _seed_org(pg_pool, "Org A")
    b = await _seed_org(pg_pool, "Org B")
    return {
        "a": {"id": a,
              "contact": await _seed_contact(pg_pool, a),
              "conv": await _seed_conversation(pg_pool, a, await _seed_contact(pg_pool, a))},
        "b": {"id": b,
              "contact": await _seed_contact(pg_pool, b),
              "conv": await _seed_conversation(pg_pool, b, await _seed_contact(pg_pool, b))},
    }


# Criticita' #1: update_template_status metteva organization_id nel SET e mai
# nella WHERE: l'update colpiva le righe di TUTTE le org con stesso
# name+language (e tentava il furto di identita' della riga altrui).
async def test_update_template_status_non_sovrascrive_altra_org(two_orgs, pg_pool):
    repo = Repository(pool=pg_pool)
    name = f"promo_{uuid.uuid4().hex[:8]}"
    async with pg_pool.acquire() as conn:
        for org in (two_orgs["a"], two_orgs["b"]):
            await conn.execute(
                """INSERT INTO whatsapp_templates
                       (id, organization_id, name, language, category, status, components)
                   VALUES ($1, $2, $3, 'it', 'MARKETING', 'PENDING', '[]'::jsonb)""",
                uuid.uuid4(), org["id"], name,
            )

    await repo.update_template_status(
        organization_id=two_orgs["a"]["id"],
        name=name,
        language="it",
        status="APPROVED",
    )

    async with pg_pool.acquire() as conn:
        status_a = await conn.fetchval(
            "SELECT status FROM whatsapp_templates WHERE organization_id = $1 AND name = $2 AND language = 'it'",
            two_orgs["a"]["id"], name,
        )
        status_b = await conn.fetchval(
            "SELECT status FROM whatsapp_templates WHERE organization_id = $1 AND name = $2 AND language = 'it'",
            two_orgs["b"]["id"], name,
        )
    assert status_a == "APPROVED"
    assert status_b == "PENDING", (
        "la riga dell'altra organizzazione NON deve essere toccata"
    )


# Criticita' #2: get_conversation filtrava solo per id -> IDOR cross-tenant.
async def test_get_conversation_idor(two_orgs, pg_pool):
    repo = Repository(pool=pg_pool)
    conv_b = two_orgs["b"]["conv"]

    leaked = await repo.get_conversation(str(conv_b), str(two_orgs["a"]["id"]))
    assert leaked is None, "una conversazione di un'altra org NON deve esistere per org_a"

    own = await repo.get_conversation(str(conv_b), str(two_orgs["b"]["id"]))
    assert own is not None
    assert str(own["id"]) == str(conv_b)


# Criticita' #3: fallback SELECT per wam_id senza filtro org -> in caso di
# collisione wam_id la seconda org riceveva la riga della prima.
async def test_upsert_wamid_collision_per_org(two_orgs, pg_pool):
    repo = Repository(pool=pg_pool)
    wam_id = f"wam-collision-{uuid.uuid4().hex[:12]}"
    common = dict(
        direction="inbound",
        message_type="text",
        content={"body": "ciao"},
        content_text="ciao",
        status="received_pending_ai",
    )

    res_a = await repo.upsert_message(
        id=uuid.uuid4(),
        organization_id=two_orgs["a"]["id"],
        conversation_id=two_orgs["a"]["conv"],
        wam_id=wam_id,
        **common,
    )
    assert str(res_a["organization_id"]) == str(two_orgs["a"]["id"])

    res_b = await repo.upsert_message(
        id=uuid.uuid4(),
        organization_id=two_orgs["b"]["id"],
        conversation_id=two_orgs["b"]["conv"],
        wam_id=wam_id,
        **common,
    )
    # La collisione globale su wam_id resta, ma org_b non deve MAI leggere la
    # riga di org_a: o la sua riga, o niente.
    assert res_b is None or str(res_b["organization_id"]) == str(two_orgs["b"]["id"]), (
        f"leak cross-org: org_b ha ricevuto {res_b}"
    )
    if res_b is not None:
        assert str(res_b["id"]) != str(res_a["id"])


# Criticita' #4: get_outbound_dedup senza org -> riga dedup lettabile da
# qualunque org conoscendo il message_id.
async def test_outbound_dedup_scoped(two_orgs, pg_pool):
    repo = Repository(pool=pg_pool)
    message_id = uuid.uuid4()
    async with pg_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO outbound_dedup (message_id, organization_id, response_text)
               VALUES ($1, $2, 'risposta-segreta-org-a')""",
            message_id, two_orgs["a"]["id"],
        )

    other = await repo.get_outbound_dedup(two_orgs["b"]["id"], message_id)
    assert other is None, "il dedup di org_a non deve essere visibile a org_b"

    own = await repo.get_outbound_dedup(two_orgs["a"]["id"], message_id)
    assert own is not None
    assert own["response_text"] == "risposta-segreta-org-a"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM contacts WHERE NOT (organization_id = $1)",
        "SELECT id FROM contacts WHERE organization_id = $1 IS FALSE",
        "SELECT id FROM contacts WHERE CASE WHEN organization_id = $1 THEN FALSE ELSE TRUE END",
        "UPDATE contacts SET phone_number = '+399999999999' "
        "WHERE NOT (organization_id = $1)",
        "UPDATE contacts SET phone_number = '+399999999999' "
        "WHERE organization_id = $1 IS FALSE",
        "UPDATE contacts SET phone_number = '+399999999999' "
        "WHERE CASE WHEN organization_id = $1 THEN FALSE ELSE TRUE END",
    ],
)
async def test_postgres_guard_rejects_negative_tenant_predicates(two_orgs, pg_pool, sql):
    async with pg_pool.acquire() as conn:
        scoped = ScopedConnection(conn, two_orgs["a"]["id"])
        with pytest.raises(TenantScopeViolation):
            if sql.lstrip().upper().startswith("SELECT"):
                await scoped.fetch(sql, two_orgs["a"]["id"])
            else:
                await scoped.execute(sql, two_orgs["a"]["id"])

        phone_b = await conn.fetchval(
            "SELECT phone_number FROM contacts WHERE id = $1",
            two_orgs["b"]["contact"],
        )
    assert phone_b.startswith("+39")


async def _seed_microfix_consent(two_orgs, conn):
    children = {}
    for tenant in ("a", "b"):
        children[tenant] = uuid.uuid4()
        await conn.execute(
            "INSERT INTO contact_consent_log (id, contact_id, event_type, method) "
            "VALUES ($1, $2, 'opt_out', 'keyword_match')",
            children[tenant], two_orgs[tenant]["contact"],
        )
    return children


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT \"C\".id, \"C\".organization_id FROM contacts c JOIN contacts \"C\" ON TRUE WHERE c.organization_id = $1",
        "SELECT c.id, c.organization_id FROM organizations o RIGHT JOIN contacts c ON c.organization_id = $1",
        "SELECT c.id, c.organization_id FROM organizations o RIGHT OUTER JOIN contacts c ON c.organization_id = $1",
        "SELECT c.id, c.organization_id FROM organizations o FULL JOIN contacts c ON o.id = c.organization_id AND c.organization_id = $1",
        "SELECT l.id, c.organization_id FROM contact_consent_log l LEFT JOIN contacts c ON l.contact_id = c.id AND c.organization_id = $1",
        "SELECT c.id, c.organization_id FROM contacts c LEFT JOIN organizations o ON c.organization_id = $1",
    ],
)
async def test_postgres_final_microfix_blocks_proven_cross_tenant_rows(two_orgs, pg_pool, sql):
    async with pg_pool.acquire() as conn:
        children = await _seed_microfix_consent(two_orgs, conn)
        raw = await conn.fetch(sql, two_orgs["a"]["id"])
        foreign_id = children["b"] if "contact_consent_log" in sql else two_orgs["b"]["contact"]
        assert foreign_id in {row["id"] for row in raw}
        scoped = ScopedConnection(conn, two_orgs["a"]["id"])
        with pytest.raises(TenantScopeViolation):
            await scoped.fetch(sql, two_orgs["a"]["id"])


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT \"C\".id, \"C\".organization_id FROM contacts \"C\" WHERE \"C\".organization_id = $1",
        "SELECT c.id, c.organization_id FROM contacts c WHERE c.organization_id = $1",
        "SELECT \"c\".id, \"c\".organization_id FROM contacts C WHERE \"c\".organization_id = $1",
        "SELECT \"where\".id, \"where\".organization_id FROM contacts \"where\" WHERE \"where\".organization_id = $1",
        "SELECT \"C\"\"alias\".id, \"C\"\"alias\".organization_id FROM contacts \"C\"\"alias\" WHERE \"C\"\"alias\".organization_id = $1",
        "SELECT c.id, c.organization_id FROM organizations o INNER JOIN contacts c ON c.organization_id = $1",
        "SELECT c.id, c.organization_id FROM organizations o LEFT JOIN contacts c ON o.id = c.organization_id WHERE c.organization_id = $1",
        "SELECT c.id, c.organization_id FROM organizations o RIGHT OUTER JOIN contacts c ON o.id = c.organization_id WHERE c.organization_id = $1",
        "SELECT c.id, c.organization_id FROM organizations o LEFT JOIN contacts c ON o.id = c.organization_id AND c.organization_id = $1 WHERE c.id IS NOT NULL",
        "SELECT l.id, c.organization_id FROM contact_consent_log l INNER JOIN contacts c ON l.contact_id = c.id AND c.organization_id = $1",
        "SELECT l.id, c.organization_id FROM contact_consent_log l LEFT JOIN contacts c ON TRUE WHERE l.contact_id = c.id AND c.organization_id = $1",
    ],
)
async def test_postgres_final_microfix_returns_only_current_tenant(two_orgs, pg_pool, sql):
    async with pg_pool.acquire() as conn:
        await _seed_microfix_consent(two_orgs, conn)
        scoped = ScopedConnection(conn, two_orgs["a"]["id"])
        rows = await scoped.fetch(sql, two_orgs["a"]["id"])
        assert rows
        assert {row["organization_id"] for row in rows} == {two_orgs["a"]["id"]}


async def test_postgres_guard_rejects_comma_join_bypass_and_accepts_scoped_join(
    two_orgs, pg_pool,
):
    repo = Repository(pool=pg_pool)
    message_a = await repo.upsert_message(
        uuid.uuid4(), two_orgs["a"]["id"], two_orgs["a"]["conv"],
        f"wamid.join.a.{uuid.uuid4().hex}", "inbound", "text",
        {"text": {"body": "A"}}, "A", "received_pending_ai",
    )
    message_b = await repo.upsert_message(
        uuid.uuid4(), two_orgs["b"]["id"], two_orgs["b"]["conv"],
        f"wamid.join.b.{uuid.uuid4().hex}", "inbound", "text",
        {"text": {"body": "B"}}, "B", "received_pending_ai",
    )
    unsafe = (
        "SELECT m.id FROM contacts c, messages m "
        "WHERE c.organization_id = $1"
    )
    async with pg_pool.acquire() as conn:
        raw_ids = await conn.fetch(unsafe, two_orgs["a"]["id"])
        assert message_b["id"] in {row["id"] for row in raw_ids}

        hidden = (
            "SELECT m.id FROM organizations o, messages m WHERE o.id = $1"
        )
        hidden_ids = await conn.fetch(hidden, two_orgs["a"]["id"])
        assert message_b["id"] in {row["id"] for row in hidden_ids}

        comma_after_join_on = (
            "SELECT m.id FROM organizations o "
            "JOIN contacts c ON c.organization_id = $1, messages m "
            "WHERE c.organization_id = $1"
        )
        exposed_ids = await conn.fetch(comma_after_join_on, two_orgs["a"]["id"])
        assert message_b["id"] in {row["id"] for row in exposed_ids}

        for quoted_keyword in ("select", "update", "where"):
            query = (
                "SELECT m.id FROM organizations o JOIN ("
                f'SELECT id, organization_id, organization_id AS "{quoted_keyword}" FROM contacts '
                "WHERE organization_id = $1) c "
                "ON c.organization_id = $1 AND c.organization_id = o.id "
                f'AND c."{quoted_keyword}" = o.id, messages m '
                "WHERE c.organization_id = $1"
            )
            leaked_ids = await conn.fetch(query, two_orgs["a"]["id"])
            assert message_b["id"] in {row["id"] for row in leaked_ids}

        scoped = ScopedConnection(conn, two_orgs["a"]["id"])
        with pytest.raises(TenantScopeViolation):
            await scoped.fetch(unsafe, two_orgs["a"]["id"])
        with pytest.raises(TenantScopeViolation):
            await scoped.fetch(hidden, two_orgs["a"]["id"])
        with pytest.raises(TenantScopeViolation):
            await scoped.fetch(comma_after_join_on, two_orgs["a"]["id"])
        for quoted_keyword in ("select", "update", "where"):
            query = (
                "SELECT m.id FROM organizations o JOIN ("
                f'SELECT id, organization_id, organization_id AS "{quoted_keyword}" FROM contacts '
                "WHERE organization_id = $1) c "
                "ON c.organization_id = $1 AND c.organization_id = o.id "
                f'AND c."{quoted_keyword}" = o.id, messages m '
                "WHERE c.organization_id = $1"
            )
            with pytest.raises(TenantScopeViolation):
                await scoped.fetch(query, two_orgs["a"]["id"])

        safe = (
            "SELECT c.id AS contact_id, m.id AS message_id "
            "FROM contacts c JOIN messages m "
            "ON m.organization_id = c.organization_id "
            "WHERE c.organization_id = $1 AND m.organization_id = $1"
        )
        rows = await scoped.fetch(safe, two_orgs["a"]["id"])
    assert {row["message_id"] for row in rows} == {message_a["id"]}


@pytest.mark.parametrize(
    "child_table,parent_table,child_column",
    [
        ("contact_consent_log", "contacts", "contact_id"),
        ("message_delivery_attempts", "messages", "message_id"),
    ],
)
async def test_postgres_guard_requires_positive_parent_derived_relation(
    two_orgs, pg_pool, child_table, parent_table, child_column,
):
    ids = {}
    if child_table == "contact_consent_log":
        for tenant in ("a", "b"):
            parent_id = two_orgs[tenant]["contact"]
            child_id = uuid.uuid4()
            async with pg_pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO contact_consent_log "
                    "(id, contact_id, event_type, method) "
                    "VALUES ($1, $2, 'opt_out', 'keyword_match')",
                    child_id, parent_id,
                )
            ids[tenant] = (child_id, parent_id)
        parent_alias = "c"
        parent_key = "c.id"
    else:
        repo = Repository(pool=pg_pool)
        for tenant in ("a", "b"):
            message = await repo.upsert_message(
                uuid.uuid4(), two_orgs[tenant]["id"], two_orgs[tenant]["conv"],
                f"wamid.parent.{tenant}.{uuid.uuid4().hex}", "outbound", "text",
                {"text": {"body": tenant}}, tenant, "queued",
            )
            attempt = await repo.insert_delivery_attempt(
                message["id"], datetime.now(timezone.utc)
            )
            ids[tenant] = (attempt["id"], message["id"])
        parent_alias = "m"
        parent_key = "m.id"

    child_alias = "l" if child_table == "contact_consent_log" else "a"
    positive = (
        f"SELECT {child_alias}.id FROM {child_table} {child_alias} "
        f"JOIN {parent_table} {parent_alias} "
        f"ON {child_alias}.{child_column} = {parent_key} "
        f"WHERE {parent_alias}.organization_id = $1"
    )
    negative = positive.replace(
        f"ON {child_alias}.{child_column} = {parent_key}",
        f"ON NOT ({child_alias}.{child_column} = {parent_key})",
    )
    wrong = positive.replace(
        f"ON {child_alias}.{child_column} = {parent_key}",
        f"ON {child_alias}.{child_column} <> {parent_key}",
    )
    disconnected = positive.replace(
        f"ON {child_alias}.{child_column} = {parent_key}", "ON TRUE"
    )
    async with pg_pool.acquire() as conn:
        scoped = ScopedConnection(conn, two_orgs["a"]["id"])
        rows = await scoped.fetch(positive, two_orgs["a"]["id"])
        assert {row["id"] for row in rows} == {ids["a"][0]}
        for unsafe in (negative, wrong, disconnected):
            with pytest.raises(TenantScopeViolation):
                await scoped.fetch(unsafe, two_orgs["a"]["id"])


async def test_postgres_guard_rejects_upsert_ownership_move(two_orgs, pg_pool):
    original_contact_id = two_orgs["a"]["contact"]
    phone = await pg_pool.fetchval(
        "SELECT phone_number FROM contacts WHERE id = $1", original_contact_id
    )
    sql = (
        "INSERT INTO contacts (id, organization_id, phone_number) "
        "VALUES ($1, $2, $3) "
        "ON CONFLICT (organization_id, phone_number) "
        "DO UPDATE SET organization_id = $4"
    )
    async with pg_pool.acquire() as conn:
        scoped = ScopedConnection(conn, two_orgs["a"]["id"])
        with pytest.raises(TenantScopeViolation):
            await scoped.execute(
                sql, uuid.uuid4(), two_orgs["a"]["id"], phone,
                two_orgs["b"]["id"],
            )
        owner = await conn.fetchval(
            "SELECT organization_id FROM contacts WHERE id = $1",
            original_contact_id,
        )
        assert owner == two_orgs["a"]["id"]

        safe_sql = (
            "INSERT INTO contacts (id, organization_id, phone_number) "
            "VALUES ($1, $2, $3) "
            "ON CONFLICT (organization_id, phone_number) "
            "DO UPDATE SET phone_number = EXCLUDED.phone_number"
        )
        await scoped.execute(
            safe_sql, uuid.uuid4(), two_orgs["a"]["id"], phone,
        )


async def test_delivery_attempt_claim_locking_does_not_lock_parent(two_orgs, pg_pool):
    repo = Repository(pool=pg_pool)
    message = await repo.upsert_message(
        uuid.uuid4(), two_orgs["a"]["id"], two_orgs["a"]["conv"],
        f"wamid.lock.{uuid.uuid4().hex}", "outbound", "text",
        {"text": {"body": "lock"}}, "lock", "queued",
    )
    attempt = await repo.insert_delivery_attempt(
        message["id"], datetime.now(timezone.utc)
    )

    # Locking the parent must not prevent another transaction from claiming
    # the attempt. claim_delivery_attempts uses FOR UPDATE OF a SKIP LOCKED.
    async with pg_pool.acquire() as parent_lock_conn:
        async with parent_lock_conn.transaction():
            await parent_lock_conn.fetchrow(
                "SELECT id FROM messages WHERE id = $1 FOR UPDATE", message["id"]
            )
            claimed = await asyncio.wait_for(
                repo.claim_delivery_attempts(limit=1), timeout=3
            )
            assert {row["id"] for row in claimed} == {attempt["id"]}

    async with pg_pool.acquire() as conn:
        await conn.execute(
            "UPDATE message_delivery_attempts SET status = 'pending', claimed_at = NULL "
            "WHERE id = $1",
            attempt["id"],
        )

    # An attempt lock must make a concurrent claim skip that attempt, while a
    # normal parent message update remains independent and completes promptly.
    async with pg_pool.acquire() as attempt_lock_conn:
        async with attempt_lock_conn.transaction():
            await attempt_lock_conn.fetchrow(
                "SELECT id FROM message_delivery_attempts WHERE id = $1 FOR UPDATE",
                attempt["id"],
            )
            skipped = await asyncio.wait_for(
                repo.claim_delivery_attempts(limit=1), timeout=3
            )
            assert all(row["id"] != attempt["id"] for row in skipped)
            async with pg_pool.acquire() as update_conn:
                updated = await asyncio.wait_for(
                    update_conn.execute(
                        "UPDATE messages SET status = 'sent' "
                        "WHERE id = $1 AND organization_id = $2",
                        message["id"], two_orgs["a"]["id"],
                    ),
                    timeout=3,
                )
                assert updated == "UPDATE 1"
    assert await pg_pool.fetchval(
        "SELECT status FROM messages WHERE id = $1", message["id"]
    ) == "sent"


def _vec384(*head):
    """Vettore 384 dimensioni: valori distinti in testa, resto a zero."""
    return list(head) + [0.0] * (384 - len(head))


async def _seed_doc_with_chunk(pg_pool, org_id, nome, content, embedding):
    from src.core.documenti.indexing import index_content
    await pg_pool.execute(
        "UPDATE organizations SET plan='business',subscription_status='trialing',"
        "trial_start=now()-interval '1 hour',trial_end=now()+interval '6 days' WHERE id=$1",
        org_id,
    )
    result = await index_content(
        CoreRepository(pg_pool), org_id, nome, text=content,
        request_key=str(uuid.uuid4()),
        embedding_fn=lambda texts, tipo="passage": [embedding for _ in texts],
    )
    assert result["status"] == "indexed"
    assert result["document"]["index_verified"] is True
    doc_id = result["document"]["id"]
    chunk_id = await pg_pool.fetchval(
        "SELECT id FROM document_chunks WHERE organization_id=$1 AND document_id=$2",
        org_id, doc_id,
    )
    return {"doc": doc_id, "chunk": chunk_id}


# Criticita' #6 (QA task 8): il retrieval vettoriale non deve mai attraversare
# il confine tenant. L'embedding di org_b e' IDENTICO al vettore di query
# (distanza 0 -> vincitore certo in un eventuale ranking cross-tenant):
# se il filtro organization_id venisse meno, org_b vincerebbe comunque.
async def test_search_similar_isolato(two_orgs, pg_pool):
    repo = Repository(pool=pg_pool)
    core_repo = CoreRepository(pool=pg_pool)
    query_vec = _vec384(1.0, 0.0)

    chunk_b = await _seed_doc_with_chunk(
        pg_pool, two_orgs["b"]["id"], "manuale-org-b.pdf",
        "SEGRETO-ORG-B: procedura riservata all'altra organizzazione",
        query_vec,
    )
    chunk_a = await _seed_doc_with_chunk(
        pg_pool, two_orgs["a"]["id"], "manuale-org-a.pdf",
        "documento pubblico dell'organizzazione A",
        _vec384(0.99, 0.02),
    )

    for search in (
        lambda: repo.search_similar(str(two_orgs["a"]["id"]), query_vec, k=10),
        lambda: core_repo.search_similar(str(two_orgs["a"]["id"]), query_vec, k=10),
    ):
        results = await search()
        returned_ids = {r["id"] for r in results}
        assert chunk_b["chunk"] not in returned_ids, (
            "LEAK cross-tenant: la ricerca vettoriale di org_a ha ritornato "
            "un chunk di org_b"
        )
        assert all("SEGRETO-ORG-B" not in r["content"] for r in results)
        assert all(r["document_name"] != "manuale-org-b.pdf" for r in results)
        assert returned_ids == {chunk_a["chunk"]}, (
            "il chunk proprio di org_a deve essere l'unico risultato"
        )


# Criticita' #5: dead code cross-tenant rimosso (nessun chiamante in src/).
def test_dead_code_rimosso():
    for method in ("soft_delete_message", "soft_delete_conversation", "soft_delete_contact"):
        assert not hasattr(Repository, method), f"Repository.{method} deve essere rimosso"
    assert not hasattr(CoreRepository, "list_onboarding_profiles"), (
        "CoreRepository.list_onboarding_profiles deve essere rimosso"
    )

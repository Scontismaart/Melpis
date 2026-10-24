"""PostgreSQL checks for idempotent, fenced file-byte quota accounting."""

import asyncio
import hashlib
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from src.core.billing import plans
from src.core.db.repositories.document_repo import DocumentRepository
from src.core.documenti.file_quota import FileQuotaError, snapshot

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def paid_file_budget(pg_pool, sample_org, monkeypatch):
    # Synthetic test-only quota; no commercial byte values are approved.
    monkeypatch.setattr(plans.PLANS["starter"], "rag_file_bytes_limit", 100)
    monkeypatch.setattr(plans.PLANS["pro"], "rag_file_bytes_limit", 100)
    monkeypatch.setattr(plans.PLANS["business"], "rag_file_bytes_limit", 100)
    await pg_pool.execute(
        """UPDATE organizations SET plan='business',subscription_status='active',
             subscription_id='sub_fileQuotaTest',
             current_period_start=clock_timestamp()-interval '1 day',
             current_period_end=clock_timestamp()+interval '20 days',
             messages_limit=10000 WHERE id=$1""",
        sample_org["id"],
    )
    return sample_org["id"]


def _raw_hash(payload):
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _claim(repo, org_id, *, payload="file-payload", key=None, file_bytes=0,
                 file_sha256=None, document_id=None, tipo="documento"):
    source = payload.encode("utf-8")
    if file_bytes and len(source) != file_bytes:
        source = (source * ((file_bytes + len(source) - 1) // len(source)))[:file_bytes]
    return await repo.doc_repo.begin_index(
        org_id,
        "source.pdf",
        key or uuid4(),
        _raw_hash(payload),
        tipo=tipo,
        fonte="quota-test",
        document_id=document_id,
        file_bytes=file_bytes,
        file_sha256=file_sha256 if file_sha256 is not None else (
            hashlib.sha256(source).hexdigest() if file_bytes else None
        ),
    )


async def _publish(repo, org_id, claim, *, metadata=None, text="indexed text"):
    return await repo.doc_repo.publish_index(
        org_id,
        claim["document"]["id"],
        claim["token"],
        claim["fence"],
        [text],
        [[0.1] * 384],
        metadata or {},
    )


async def _snapshot(pool, org_id):
    async with pool.acquire() as conn:
        return await snapshot(conn, org_id)


async def _assert_current_retrieval(repo, org_id, document_id):
    from src.core.documenti.rag_context import recupera_contesto_documenti, require_current_context

    context = await recupera_contesto_documenti(
        str(org_id), "synthetic quota retrieval", repo,
        q_emb=[0.1] * 384,
    )
    assert context.reason == "verified"
    assert any(str(item["document_id"]) == str(document_id) for item in context.chunks)
    await require_current_context(context, repo, org_id)


async def test_concurrent_new_uploads_reserve_under_same_org_lock(
    pg_pool, paid_file_budget,
):
    org_id = paid_file_budget
    async with pg_pool.acquire() as conn1, pg_pool.acquire() as conn2:
        assert conn1 is not conn2

        class PinnedPool:
            def __init__(self, connection):
                self.connection = connection

            @asynccontextmanager
            async def acquire(self):
                yield self.connection

        repo1 = DocumentRepository(PinnedPool(conn1))
        repo2 = DocumentRepository(PinnedPool(conn2))
        results = await asyncio.gather(
            repo1.begin_index(org_id, "one.pdf", uuid4(), _raw_hash("one"),
                              tipo="documento", file_bytes=60,
                              file_sha256=hashlib.sha256(b"1" * 60).hexdigest()),
            repo2.begin_index(org_id, "two.pdf", uuid4(), _raw_hash("two"),
                              tipo="documento", file_bytes=60,
                              file_sha256=hashlib.sha256(b"2" * 60).hexdigest()),
            return_exceptions=True,
        )

    accepted = [item for item in results if isinstance(item, dict) and item["status"] == "claimed"]
    rejected = [item for item in results if isinstance(item, FileQuotaError)]
    assert len(accepted) == 1
    assert len(rejected) == 1
    usage = await _snapshot(pg_pool, org_id)
    assert usage["occupied_bytes"] == 60
    assert usage["reserved_bytes"] == 60


async def test_replay_reclaim_and_payload_collision_do_not_double_reserve(
    repo, pg_pool, paid_file_budget,
):
    org_id = paid_file_budget
    key = uuid4()
    first = await _claim(repo, org_id, payload="same-file", key=key, file_bytes=40)
    same = await _claim(repo, org_id, payload="same-file", key=key, file_bytes=40)
    assert same["status"] == "in_progress"
    assert (await _snapshot(pg_pool, org_id))["reserved_bytes"] == 40

    await pg_pool.execute(
        "UPDATE documents SET index_lease_until=clock_timestamp()-interval '1 second' WHERE id=$1",
        first["document"]["id"],
    )
    retry = await _claim(repo, org_id, payload="same-file", key=key, file_bytes=40)
    assert retry["status"] == "claimed"
    assert retry["fence"] > first["fence"]
    assert (await _snapshot(pg_pool, org_id))["reserved_bytes"] == 40

    conflict = await _claim(repo, org_id, payload="different-file", key=key, file_bytes=40)
    assert conflict["status"] == "conflict"
    assert (await _snapshot(pg_pool, org_id))["reserved_bytes"] == 40

    await _publish(repo, org_id, retry)
    usage = await _snapshot(pg_pool, org_id)
    assert usage["committed_bytes"] == 40
    assert usage["reserved_bytes"] == 0


async def test_failed_replace_releases_only_reservation_and_keeps_old_charge(
    repo, pg_pool, paid_file_budget,
):
    org_id = paid_file_budget
    old = await _claim(repo, org_id, payload="old-file", file_bytes=40)
    await _publish(repo, org_id, old)
    replacement = await _claim(
        repo, org_id, payload="replacement-file", file_bytes=80,
        document_id=old["document"]["id"],
    )

    pending = await _snapshot(pg_pool, org_id)
    assert pending["committed_bytes"] == 40
    assert pending["reserved_bytes"] == 40
    assert pending["occupied_bytes"] == 80

    assert await repo.doc_repo.fail_index(
        org_id, replacement["document"]["id"], replacement["token"],
        replacement["fence"], "synthetic_failure",
    ) is True
    after = await _snapshot(pg_pool, org_id)
    assert after["committed_bytes"] == 40
    assert after["reserved_bytes"] == 0


async def test_downgrade_during_claim_blocks_growth_but_keeps_existing_bytes(
    repo, pg_pool, paid_file_budget, monkeypatch,
):
    org_id = paid_file_budget
    initial = await _claim(repo, org_id, payload="known-old", file_bytes=80)
    await _publish(repo, org_id, initial)
    replacement = await _claim(
        repo, org_id, payload="larger-new", file_bytes=90,
        document_id=initial["document"]["id"],
    )
    monkeypatch.setattr(plans.PLANS["starter"], "rag_file_bytes_limit", 50)
    await pg_pool.execute("UPDATE organizations SET plan='starter' WHERE id=$1", org_id)

    with pytest.raises(ValueError, match="file_quota_exceeded"):
        await _publish(repo, org_id, replacement)
    assert await repo.doc_repo.fail_index(
        org_id, replacement["document"]["id"], replacement["token"],
        replacement["fence"], "quota_rejected",
    ) is True
    after = await _snapshot(pg_pool, org_id)
    assert after["committed_bytes"] == 80
    assert after["reserved_bytes"] == 0


async def test_downgrade_allows_nonincremental_replacement_and_preserves_read(
    repo, pg_pool, paid_file_budget, monkeypatch,
):
    org_id = paid_file_budget
    initial = await _claim(repo, org_id, payload="known-old", file_bytes=80)
    await _publish(repo, org_id, initial)
    monkeypatch.setattr(plans.PLANS["starter"], "rag_file_bytes_limit", 10)
    await pg_pool.execute("UPDATE organizations SET plan='starter' WHERE id=$1", org_id)

    smaller = await _claim(
        repo, org_id, payload="smaller-new", file_bytes=60,
        document_id=initial["document"]["id"],
    )
    published = await _publish(repo, org_id, smaller)
    assert published["index_verified"] is True
    usage = await _snapshot(pg_pool, org_id)
    assert usage["committed_bytes"] == 60
    assert usage["occupied_bytes"] == 60


async def test_delete_pending_document_releases_charge_and_fences_late_publish(
    repo, pg_pool, paid_file_budget,
):
    org_id = paid_file_budget
    claim = await _claim(repo, org_id, payload="pending-file", file_bytes=55)
    assert (await _snapshot(pg_pool, org_id))["reserved_bytes"] == 55

    assert await repo.doc_repo.delete_document(org_id, claim["document"]["id"]) == 1
    usage = await _snapshot(pg_pool, org_id)
    assert usage["committed_bytes"] == 0
    assert usage["reserved_bytes"] == 0
    with pytest.raises(ValueError, match="index_claim_lost"):
        await _publish(repo, org_id, claim)


async def test_client_metadata_cannot_change_attested_file_bytes(
    repo, pg_pool, paid_file_budget,
):
    org_id = paid_file_budget
    claim = await _claim(repo, org_id, payload="attested", file_bytes=7)
    published = await _publish(
        repo, org_id, claim,
        metadata={"bytes": 0, "file_bytes": 1000, "file_sha256": "f" * 64},
    )
    assert published["file_bytes"] == 7
    assert published["file_sha256"] == hashlib.sha256(b"atteste").hexdigest()
    assert (await _snapshot(pg_pool, org_id))["committed_bytes"] == 7


async def test_historical_bytes_are_unknown_despite_metadata_and_block_growth(
    repo, pg_pool, paid_file_budget,
):
    org_id = paid_file_budget
    legacy = await repo.doc_repo.create_document(
        org_id, "legacy.pdf", tipo="documento", stato="indicizzata",
        metadata={"bytes": 4},
    )
    assert (await _snapshot(pg_pool, org_id))["unknown_documents"] >= 1

    with pytest.raises(ValueError, match="file_quota_historical_unknown"):
        await repo.doc_repo.begin_index(
            org_id, "legacy.pdf", uuid4(), _raw_hash("new-file"),
            tipo="documento", document_id=legacy["id"], file_bytes=2,
            file_sha256=_raw_hash("new-file"),
        )


async def test_historical_unknown_charge_cannot_be_cleared_by_text_replacement(
    repo, pg_pool, paid_file_budget,
):
    org_id = paid_file_budget
    legacy = await repo.doc_repo.create_document(
        org_id, "legacy-text.pdf", tipo="documento", stato="indicizzata",
        metadata={"bytes": 4},
    )

    with pytest.raises(ValueError, match="file_quota_historical_unknown"):
        await repo.doc_repo.begin_index(
            org_id, "legacy-text.pdf", uuid4(), _raw_hash("replacement text"),
            tipo="faq", document_id=legacy["id"], file_bytes=0,
        )

    usage = await _snapshot(pg_pool, org_id)
    assert usage["unknown_documents"] == 1


@pytest.mark.parametrize("plan", ["starter", "pro", "business"])
async def test_active_plan_file_limits_are_independent(plan, repo, pg_pool,
                                                       paid_file_budget, monkeypatch):
    org_id = paid_file_budget
    monkeypatch.setattr(plans.PLANS[plan], "rag_file_bytes_limit", 100)
    await pg_pool.execute("UPDATE organizations SET plan=$2 WHERE id=$1", org_id, plan)

    state = await repo.doc_repo.file_quota_status(org_id)
    assert state["rag_enabled"] is True
    assert state["file_quota"]["limit_bytes"] == 100
    assert state["file_quota"]["scope"] == "paid"

    claim = await _claim(repo, org_id, payload=f"active-{plan}", file_bytes=20)
    await _publish(repo, org_id, claim, text=f"Active plan source {plan}.")
    await _assert_current_retrieval(repo, org_id, claim["document"]["id"])


async def test_trial_without_plan_uses_only_trial_file_limit(repo, pg_pool,
                                                             paid_file_budget, monkeypatch):
    org_id = paid_file_budget
    monkeypatch.setattr(plans, "TRIAL_RAG_FILE_BYTES_LIMIT", 23)
    await pg_pool.execute(
        """UPDATE organizations SET plan=NULL,subscription_status='trialing',subscription_id=NULL,
             trial_start=clock_timestamp()-interval '1 day',
             trial_end=clock_timestamp()+interval '2 days' WHERE id=$1""",
        org_id,
    )

    state = await repo.doc_repo.file_quota_status(org_id)
    assert state["rag_enabled"] is True
    assert state["file_quota"]["limit_bytes"] == 23
    assert state["file_quota"]["scope"] == "trial"


@pytest.mark.parametrize("plan", ["starter", "pro", "business", None])
async def test_trial_uses_trial_budget_for_every_plan_and_retrieves(
    plan, repo, pg_pool, paid_file_budget, monkeypatch,
):
    org_id = paid_file_budget
    if plan:
        monkeypatch.setattr(plans.PLANS[plan], "rag_file_bytes_limit", 5)
    monkeypatch.setattr(plans, "TRIAL_RAG_FILE_BYTES_LIMIT", 23)
    await pg_pool.execute(
        """UPDATE organizations SET plan=$2,subscription_status='trialing',subscription_id=NULL,
             trial_start=clock_timestamp()-interval '1 day',
             trial_end=clock_timestamp()+interval '2 days' WHERE id=$1""",
        org_id, plan,
    )

    state = await repo.doc_repo.file_quota_status(org_id)
    assert state["rag_enabled"] is True
    assert state["file_quota"]["limit_bytes"] == 23
    assert state["file_quota"]["scope"] == "trial"

    claim = await _claim(repo, org_id, payload=f"trial-{plan or 'no-plan'}", file_bytes=20)
    await _publish(repo, org_id, claim, text="Trial source remains retrievable.")
    await _assert_current_retrieval(repo, org_id, claim["document"]["id"])
    with pytest.raises(FileQuotaError, match="file_quota_exceeded"):
        await _claim(repo, org_id, payload=f"trial-growth-{plan or 'no-plan'}", file_bytes=5)


async def test_upgrade_and_period_change_do_not_reset_document_charge(
    repo, pg_pool, paid_file_budget, monkeypatch,
):
    org_id = paid_file_budget
    monkeypatch.setattr(plans.PLANS["starter"], "rag_file_bytes_limit", 50)
    monkeypatch.setattr(plans.PLANS["business"], "rag_file_bytes_limit", 100)
    await pg_pool.execute("UPDATE organizations SET plan='starter' WHERE id=$1", org_id)
    claim = await _claim(repo, org_id, payload="kept-file", file_bytes=30)
    await _publish(repo, org_id, claim)

    await pg_pool.execute(
        """UPDATE organizations SET plan='business',
             current_period_start=clock_timestamp()+interval '1 day',
             current_period_end=clock_timestamp()+interval '31 days' WHERE id=$1""",
        org_id,
    )
    state = (await _snapshot(pg_pool, org_id))
    assert state["committed_bytes"] == 30
    assert state["occupied_bytes"] == 30
    assert state["limit_bytes"] == 100


async def test_expired_lease_keeps_reservation_until_fenced_reclaim(
    repo, pg_pool, paid_file_budget,
):
    org_id = paid_file_budget
    claim = await _claim(repo, org_id, payload="held-file", file_bytes=80)
    await pg_pool.execute(
        "UPDATE documents SET index_lease_until=clock_timestamp()-interval '1 second' WHERE id=$1",
        claim["document"]["id"],
    )

    usage = await _snapshot(pg_pool, org_id)
    assert usage["reserved_bytes"] == 80
    assert usage["occupied_bytes"] == 80
    with pytest.raises(FileQuotaError, match="file_quota_exceeded"):
        await _claim(repo, org_id, payload="other-file", file_bytes=30)


async def test_expired_owner_can_fail_own_claim_and_late_publish_is_fenced(
    repo, pg_pool, paid_file_budget,
):
    org_id = paid_file_budget
    claim = await _claim(repo, org_id, payload="expired-owner", file_bytes=80)
    await pg_pool.execute(
        "UPDATE documents SET index_lease_until=clock_timestamp()-interval '1 second' WHERE id=$1",
        claim["document"]["id"],
    )
    assert (await _snapshot(pg_pool, org_id))["reserved_bytes"] == 80

    assert await repo.doc_repo.fail_index(
        org_id, claim["document"]["id"], claim["token"], claim["fence"], "worker_failed",
    ) is True
    usage = await _snapshot(pg_pool, org_id)
    assert usage["reserved_bytes"] == 0
    with pytest.raises(ValueError, match="index_claim_lost"):
        await _publish(repo, org_id, claim)


async def test_stale_failure_after_reclaim_cannot_clear_new_owner_reservation(
    repo, pg_pool, paid_file_budget,
):
    org_id = paid_file_budget
    key = uuid4()
    old = await _claim(repo, org_id, payload="same-reclaimed-file", key=key, file_bytes=80)
    await pg_pool.execute(
        "UPDATE documents SET index_lease_until=clock_timestamp()-interval '1 second' WHERE id=$1",
        old["document"]["id"],
    )
    new = await _claim(repo, org_id, payload="same-reclaimed-file", key=key, file_bytes=80)
    assert new["status"] == "claimed"
    assert new["fence"] > old["fence"]

    assert await repo.doc_repo.fail_index(
        org_id, old["document"]["id"], old["token"], old["fence"], "stale_worker_failed",
    ) is False
    assert (await _snapshot(pg_pool, org_id))["reserved_bytes"] == 80
    await _publish(repo, org_id, new)
    usage = await _snapshot(pg_pool, org_id)
    assert usage["committed_bytes"] == 80
    assert usage["reserved_bytes"] == 0


async def test_file_quota_is_scoped_to_each_organization(
    repo, pg_pool, paid_file_budget, other_org,
):
    org_id = paid_file_budget
    other_id = other_org["id"]
    await pg_pool.execute(
        """UPDATE organizations SET plan='business',subscription_status='active',
             subscription_id='sub_fileQuotaOther',
             current_period_start=clock_timestamp()-interval '1 day',
             current_period_end=clock_timestamp()+interval '20 days' WHERE id=$1""",
        other_id,
    )
    await _claim(repo, org_id, payload="tenant-a", file_bytes=80)
    other_claim = await _claim(repo, other_id, payload="tenant-b", file_bytes=80)

    assert other_claim["status"] == "claimed"
    assert (await _snapshot(pg_pool, org_id))["reserved_bytes"] == 80
    assert (await _snapshot(pg_pool, other_id))["reserved_bytes"] == 80


async def test_client_roles_cannot_write_file_quota_evidence_columns(pg_pool):
    for role in ("anon", "authenticated"):
        for privilege in ("INSERT", "UPDATE"):
            for column in ("file_bytes", "file_sha256", "index_file_bytes", "index_file_sha256"):
                allowed = await pg_pool.fetchval(
                    "SELECT has_column_privilege($1,'documents',$2,$3)",
                    role, column, privilege,
                )
                assert allowed is False, (role, privilege, column)


async def test_manual_text_source_is_not_charged_as_original_file(
    repo, pg_pool, paid_file_budget,
):
    org_id = paid_file_budget
    await pg_pool.execute("UPDATE organizations SET plan='starter' WHERE id=$1", org_id)
    # The synthetic plan cap is deliberately tiny; text/manual RAG does not consume it.
    claim = await _claim(repo, org_id, payload="long manual text", file_bytes=0,
                         tipo="faq")
    await _publish(repo, org_id, claim, metadata={"bytes": 100000})
    usage = await _snapshot(pg_pool, org_id)
    assert usage["committed_bytes"] == 0
    assert usage["reserved_bytes"] == 0

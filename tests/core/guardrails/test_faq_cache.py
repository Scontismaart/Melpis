"""Cache FAQ semantica (task 12) su DB reale: lookup/soglia/TTL,
invalidazione su upload documento, isolamento tra tenant."""

from unittest.mock import patch

import pytest

pytestmark = pytest.mark.usefixtures("reset_db")

EMB = [0.1] * 384
EMB_LONTANO = [-0.9] * 384  # distanza cosine ~2: mai sotto soglia


@pytest.fixture
async def wrepo(pg_pool, sample_org, other_org):
    # Cache operations require the live Scala entitlement; this exercises the
    # real repository gate rather than bypassing rag_access in PG tests.
    await pg_pool.execute(
        """UPDATE organizations SET plan='business', subscription_status='active',
           subscription_id=CASE WHEN id=$1 THEN 'sub_faqCacheA' ELSE 'sub_faqCacheB' END,
           current_period_start=now()-interval '1 day',
           current_period_end=now()+interval '29 days' WHERE id=ANY($2::uuid[])""",
        sample_org["id"], [sample_org["id"], other_org["id"]],
    )
    from src.whatsapp.repository import Repository
    return Repository(pool=pg_pool)


async def _count_faq_cache(pool, org_id) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM faq_cache WHERE organization_id = $1", org_id
        )


class TestRepoFaqCache:
    async def test_store_e_lookup_hit_con_hit_count(self, wrepo, sample_org):
        await _store_current(wrepo,
            str(sample_org["id"]), "A che ora aprite?", "Apriamo alle 12.", EMB
        )
        row = await wrepo.faq_cache_lookup(str(sample_org["id"]), EMB, max_distance=0.08)
        assert row["answer_text"] == "Apriamo alle 12."
        assert row["hit_count"] == 1

        row2 = await wrepo.faq_cache_lookup(str(sample_org["id"]), EMB)
        assert row2["hit_count"] == 2
        assert row2["last_used_at"] is not None

    async def test_lookup_miss_oltre_soglia(self, wrepo, sample_org):
        await _store_current(wrepo,
            str(sample_org["id"]), "A che ora aprite?", "Apriamo alle 12.", EMB
        )
        row = await wrepo.faq_cache_lookup(str(sample_org["id"]), EMB_LONTANO)
        assert row is None

    async def test_lookup_scaduto_dopo_ttl(self, wrepo, sample_org):
        await _store_current(wrepo,
            str(sample_org["id"]), "A che ora aprite?", "Apriamo alle 12.", EMB,
            ttl_hours=0,
        )
        row = await wrepo.faq_cache_lookup(str(sample_org["id"]), EMB)
        assert row is None

    async def test_invalidazione_per_org(self, wrepo, sample_org, other_org):
        await _store_current(wrepo, str(sample_org["id"]), "q1", "r1", EMB)
        await _store_current(wrepo, str(other_org["id"]), "q2", "r2", EMB)

        eliminati = await wrepo.faq_cache_invalidate(str(sample_org["id"]))
        assert eliminati == 1
        assert await wrepo.faq_cache_lookup(str(sample_org["id"]), EMB) is None
        # l'altra org non viene toccata
        row = await wrepo.faq_cache_lookup(str(other_org["id"]), EMB)
        assert row["answer_text"] == "r2"

    async def test_isolamento_tenant_nel_lookup(self, wrepo, sample_org, other_org):
        await _store_current(wrepo, str(sample_org["id"]), "q", "segreto org A", EMB)
        assert await wrepo.faq_cache_lookup(str(other_org["id"]), EMB) is None

    async def test_stessa_domanda_aggiorna_non_duplica(self, wrepo, sample_org):
        await _store_current(wrepo,
            str(sample_org["id"]), "A che ora aprite?", "Apriamo alle 12.", EMB
        )
        await _store_current(wrepo,
            str(sample_org["id"]), "a che ora  aprite?", "Apriamo alle 12:30.", EMB
        )
        assert await _count_faq_cache(wrepo.pool, sample_org["id"]) == 1
        row = await wrepo.faq_cache_lookup(str(sample_org["id"]), EMB)
        assert row["answer_text"] == "Apriamo alle 12:30."


class TestModuloFaqCache:
    async def test_cerca_e_salva_via_testo(self, wrepo, sample_org):
        from src.core.guardrails import faq_cache

        with patch("src.core.guardrails.faq_cache.vettorizza", return_value=[EMB]):
            assert await faq_cache.cerca_in_cache(str(sample_org["id"]), "dove siete?", wrepo) is None
            await _save_current(str(sample_org["id"]), "dove siete?", "Via Roma 1.", wrepo)
            hit = await faq_cache.cerca_in_cache(str(sample_org["id"]), "dove siete?", wrepo)
        assert hit == "Via Roma 1."

    async def test_cache_disabilitata_skip_tutto(self, wrepo, sample_org, monkeypatch):
        from src.core.guardrails import faq_cache

        monkeypatch.setenv("GUARDRAIL_CACHE_ENABLED", "false")
        with patch("src.core.guardrails.faq_cache.vettorizza") as mock_vett:
            assert await faq_cache.cerca_in_cache(str(sample_org["id"]), "q", wrepo) is None
            assert await _save_current(str(sample_org["id"]), "q", "r", wrepo) is None
        mock_vett.assert_not_called()
        assert await _count_faq_cache(wrepo.pool, sample_org["id"]) == 0


async def _store_current(repo, organization_id, *args, **kwargs):
    epoch = await repo.doc_repo.knowledge_version(organization_id)
    return await repo.faq_cache_store(organization_id, *args, expected_epoch=epoch, **kwargs)


async def _save_current(organization_id, text, answer, repo, **kwargs):
    from src.core.guardrails import faq_cache
    epoch = await repo.doc_repo.knowledge_version(organization_id)
    return await faq_cache.salva_in_cache(organization_id, text, answer, repo, expected_epoch=epoch, **kwargs)

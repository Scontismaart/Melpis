"""Real PG checks: source changes cannot repopulate cache or commit stale replies."""
import hashlib
from uuid import uuid4

import pytest

from src.core.billing.quota_reservations import QuotaConflict, QuotaReservations, validate_final_response
from src.models.schemas import RispostaOutput
from tests.core.test_quota_reservation_lifecycle import _organization, _auth_member, _operation

pytestmark = pytest.mark.asyncio


async def _fixture(pool, repo, *, status="trialing"):
    org = await _organization(pool, status=status)
    await pool.execute("UPDATE organizations SET plan='business' WHERE id=$1", org['id'])
    actor = await _auth_member(pool, org['id'])
    documents = repo.doc_repo
    claim = await documents.begin_index(org['id'], 'synthetic.pdf', uuid4(), hashlib.sha256(b'fixture').hexdigest())
    published = await documents.publish_index(org['id'], claim['document']['id'], claim['token'], claim['fence'],
        ['Informazioni sintetiche aggiornate.'], [[0.1]*384], {})
    return org['id'], actor, published


async def test_late_cache_write_rejected_after_source_toggle(pg_pool, repo):
    org, _, doc = await _fixture(pg_pool, repo)
    epoch = await repo.doc_repo.knowledge_version(org)
    assert await repo.doc_repo.faq_cache_store(org, 'domanda', 'risposta', [0.1]*384, expected_epoch=epoch)
    await repo.toggle_document_active(org, doc['id'])
    assert not await repo.doc_repo.faq_cache_lookup(org, [0.1]*384)
    assert not await repo.doc_repo.faq_cache_store(org, 'domanda', 'vecchia risposta', [0.1]*384, expected_epoch=epoch)
    assert await pg_pool.fetchval('SELECT count(*) FROM faq_cache WHERE organization_id=$1', org) == 0


@pytest.mark.parametrize('change', ['toggle', 'delete', 'profile'])
async def test_changed_evidence_prevents_commercial_commit(pg_pool, repo, change):
    org, actor, doc = await _fixture(pg_pool, repo)
    quota = QuotaReservations(pg_pool)
    operation = _operation(org, actor_id=actor)
    reserved = await quota.reserve(operation)
    epoch = await repo.doc_repo.knowledge_version(org)
    final = validate_final_response(operation, reserved['ticket'],
        RispostaOutput(risposta='Informazioni sintetiche aggiornate.', richiede_umano=False, motivo='', categoria='faq'),
        knowledge_epoch=epoch)
    if change == 'toggle':
        await repo.toggle_document_active(org, doc['id'])
    elif change == 'delete':
        await repo.delete_document(org, doc['id'])
    elif change == 'profile':
        await pg_pool.execute("UPDATE organizations SET business_profile='{" + '"nome":"Profilo nuovo"' + "}'::jsonb WHERE id=$1", org)
    with pytest.raises(QuotaConflict):
        await quota.commit(operation, reserved['ticket'], final)
    assert await pg_pool.fetchval('SELECT messages_used_this_period FROM organizations WHERE id=$1', org) == 0
    assert await quota.release(operation, reserved['ticket'])


async def test_committed_replay_after_delete_is_denied_without_refund(pg_pool, repo):
    org, actor, doc = await _fixture(pg_pool, repo)
    quota = QuotaReservations(pg_pool)
    operation = _operation(org, actor_id=actor)
    reserved = await quota.reserve(operation)
    final = validate_final_response(operation, reserved['ticket'],
        RispostaOutput(risposta='Risposta sintetica.', richiede_umano=False, motivo='', categoria='faq'),
        knowledge_epoch=await repo.doc_repo.knowledge_version(org))
    await quota.commit(operation, reserved['ticket'], final)
    assert (await quota.reserve(operation))['status'] == 'replay'
    await repo.delete_document(org, doc['id'])
    with pytest.raises(QuotaConflict):
        await quota.reserve(operation)
    assert not await quota.release(operation, reserved['ticket'])
    assert await pg_pool.fetchval('SELECT messages_used_this_period FROM organizations WHERE id=$1', org) == 1


async def test_profile_edit_fences_preparing_structured_source(pg_pool, repo):
    org = await _organization(pg_pool)
    await pg_pool.execute("UPDATE organizations SET plan='business' WHERE id=$1", org['id'])
    await pg_pool.execute("UPDATE organizations SET business_profile=$2::jsonb WHERE id=$1", org['id'], '{"nome":"Prima"}')
    claim = await repo.doc_repo.begin_index(org['id'], 'Profilo', uuid4(), hashlib.sha256(b'old').hexdigest(), tipo='dati_struttura', expected_profile={'nome':'Prima'})
    await pg_pool.execute("UPDATE organizations SET business_profile='{" + '"nome":"Profilo nuovo"' + "}'::jsonb WHERE id=$1", org['id'])
    with pytest.raises(ValueError, match='index_claim_lost'):
        await repo.doc_repo.publish_index(org['id'], claim['document']['id'], claim['token'], claim['fence'], ['Vecchio profilo'], [[0.1]*384], {})


async def test_late_claim_cannot_republish_old_profile(pg_pool, repo):
    org = await _organization(pg_pool)
    await pg_pool.execute("UPDATE organizations SET plan='business',business_profile=$2::jsonb WHERE id=$1", org['id'], '{"nome":"Nuovo"}')
    with pytest.raises(ValueError, match='profile_changed'):
        await repo.doc_repo.begin_index(org['id'], 'Profilo', uuid4(), hashlib.sha256(b'old').hexdigest(), tipo='dati_struttura', expected_profile={'nome':'Vecchio'})


async def test_qa_profile_change_denies_even_when_pdf_revision_is_unchanged(pg_pool, repo):
    from src.core.billing.quota_reservations import validate_qa_final_response
    org, actor, doc = await _fixture(pg_pool, repo)
    quota = QuotaReservations(pg_pool)
    operation = _operation(org, actor_id=actor, channel='qa')
    ticket = (await quota.reserve(operation))['ticket']
    final = validate_qa_final_response(operation, ticket, {
        'risposta':'Informazioni sintetiche aggiornate.',
        'fonti':[{'documento':'synthetic.pdf','tipo':'upload','score':0.1,
                  'priorita':3,'document_id':str(doc['id']),'revision':doc['revision']}],
    }, knowledge_epoch=await repo.doc_repo.knowledge_version(org))
    await pg_pool.execute('UPDATE organizations SET business_profile=$2::jsonb WHERE id=$1', org, '{"nome":"Changed during QA"}')
    with pytest.raises(QuotaConflict):
        await quota.commit(operation, ticket, final)
    assert await pg_pool.fetchval('SELECT messages_used_this_period FROM organizations WHERE id=$1', org) == 0


@pytest.mark.parametrize("target_plan", ["starter", "pro", "business"])
async def test_a04_commit_remains_valid_after_plan_change_between_rag_plans(
    pg_pool, repo, target_plan,
):
    org, actor, doc = await _fixture(pg_pool, repo, status="active")
    quota = QuotaReservations(pg_pool)
    operation = _operation(org, actor_id=actor)
    reserved = await quota.reserve(operation)
    final = validate_final_response(
        operation, reserved["ticket"],
        RispostaOutput(risposta="Informazioni sintetiche aggiornate.",
                       richiede_umano=False, motivo="", categoria="faq"),
        knowledge_epoch=await repo.doc_repo.knowledge_version(org),
    )

    await pg_pool.execute("UPDATE organizations SET plan=$2 WHERE id=$1", org, target_plan)

    committed = await quota.commit(operation, reserved["ticket"], final)
    assert committed["status"] == "committed"
    assert await pg_pool.fetchval(
        "SELECT messages_used_this_period FROM organizations WHERE id=$1", org,
    ) == 1

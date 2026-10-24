import hashlib
import json
import os
import uuid
from unittest.mock import patch

import httpx
import pytest

from src.core.documenti.qa_agent import rispondi
from src.core.documenti.rag_context import recupera_contesto_documenti

API_KEY = "test-api-key-12345"
pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def set_env():
    os.environ["DATABASE_URL"] = ""
    os.environ["API_KEY_SERVICE"] = API_KEY


@pytest.fixture
async def async_client(repo, pg_pool, install_test_identity, sample_org, other_org):
    from src.api.main import app
    await pg_pool.execute(
        "UPDATE organizations SET subscription_status = 'trialing', plan = 'business', "
        "trial_start=now()-interval '1 hour', trial_end=now()+interval '6 days', "
        "messages_limit=500, messages_used_this_period=0 WHERE id = ANY($1::uuid[])",
        [sample_org["id"], other_org["id"]],
    )
    install_test_identity(app, API_KEY)
    app.state.repo = repo
    app.state.pool = pg_pool
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        try:
            yield c
        finally:
            app.dependency_overrides.clear()


def _headers(org_id):
    return {"X-API-Key": API_KEY, "X-Organization-Id": str(org_id)}


async def _publish_text(repo, org_id, name, content, *, tipo, fonte):
    from src.core.documenti.indexing import index_content

    result = await index_content(
        repo, str(org_id), name, text=content, request_key=str(uuid.uuid4()),
        tipo=tipo, fonte=fonte,
        expected_profile=await repo.get_org_business_profile(org_id) if tipo == "dati_struttura" else None,
        embedding_fn=lambda texts, tipo="passage": [[0.1] * 384 for _ in texts],
    )
    assert result["status"] == "indexed"
    assert result["document"]["index_verified"] is True
    assert result["document"]["stato"] == "indicizzata"
    return result["document"]


async def test_vertical_aware_prompt_and_hierarchy(repo, sample_org):
    """Verifica che:
    1. L'agente usi la strategia verticale dell'organizzazione (es. parrucchiere) invece del vecchio prompt ristorante hardcodato.
    2. La gerarchia delle fonti (Dati struttura P1 > Documenti P3) sia iniettata nel prompt per risolvere conflitti.
    """
    # Imposta il profilo dell'organizzazione come parrucchiere
    await repo.update_org_business_profile(sample_org["id"], {
        "verticale": "parrucchiere",
        "nome": "Salone Bella Vista",
        "servizi_strutturati": [
            {"nome": "Taglio Uomo", "prezzo": 25.0, "durata_minuti": 30, "operatore": "Luigi"}
        ],
    })

    # Il publisher canonico richiede entitlement valido: stabilisci il trial
    # prima di pubblicare le due fonti sintetiche.
    await repo.pool.execute(
        "UPDATE organizations SET subscription_status='trialing', plan='business', "
        "trial_start=now()-interval '1 hour', trial_end=now()+interval '2 days', "
        "messages_limit=150, messages_used_this_period=0, ai_accounting_blocked=false WHERE id=$1",
        sample_org["id"],
    )
    await _publish_text(
        repo, sample_org["id"], "Dati struttura e listino servizi",
        "Taglio Uomo: Prezzo 25.00€ | Durata 30 min | Operatore: Luigi",
        tipo="dati_struttura", fonte="Dati struttura",
    )
    await _publish_text(
        repo, sample_org["id"], "vecchio_listino.pdf",
        "Offerta speciale: Taglio Uomo a soli 18 euro!",
        tipo="documento", fonte="vecchio_listino.pdf",
    )

    auth_user_id = uuid.uuid4()
    async with repo.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO auth.users(id,email) VALUES($1,$2)",
            auth_user_id, f"{auth_user_id}@example.test",
        )
        profile_id = await conn.fetchval(
            "SELECT id FROM user_profiles WHERE auth_user_id=$1", auth_user_id,
        )
        await conn.execute(
            "INSERT INTO organization_memberships(organization_id,user_id,ruolo,joined_at) "
            "VALUES($1,$2,'owner',now())",
            sample_org["id"], profile_id,
        )

    prompt_catturato = []

    # The prompt test exercises an answer-capable Q&A call, so establish the
    # same tenant-scoped A04 reservation the API route requires before A03.
    from src.core.billing.quota_reservations import QuotaOperation, QuotaReservations

    question = "Quanto costa il taglio uomo?"
    source_id = uuid.uuid4()
    payload_hash = hashlib.sha256(json.dumps(
        {"organization_id": str(sample_org["id"]), "domanda": question, "k": 5},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    operation = QuotaOperation(
        organization_id=uuid.UUID(str(sample_org["id"])), source_id=source_id,
        channel="qa", payload_hash=payload_hash, auth_user_id=auth_user_id,
    )
    reservations = QuotaReservations(repo.pool)
    reservation = await reservations.reserve(operation)
    assert reservation["status"] == "reserved"

    class _CaptureLLM:
        async def acall(self, prompt):
            prompt_catturato.append(prompt)
            return "Il prezzo ufficiale del Taglio Uomo è di 25 euro."

    with patch("src.core.documenti.rag_context.vettorizza", side_effect=lambda texts, tipo="query": [[0.1] * 384 for _ in texts]), \
         patch("src.core.documenti.qa_agent.crea_llm", return_value=_CaptureLLM()) as make_llm:
        res = await rispondi(
            str(sample_org["id"]), question, repo,
            billing=reservation["billing"], actor_auth_user_id=auth_user_id,
            quota_operation=operation, quota_ticket=reservation["ticket"],
        )

    final = res.pop("_quota_final", None)
    assert final is not None
    committed = await reservations.commit(operation, reservation["ticket"], final)

    assert len(prompt_catturato) == 1
    assert make_llm.call_args.kwargs["provider_context"].organization_id == sample_org["id"]
    prompt = prompt_catturato[0]

    # Verifica: il prompt NON parla di ristorante / allergeni / carta vini
    assert "ristorante" not in prompt.lower()
    assert "carta vini" not in prompt.lower()

    # Verifica: il prompt include l'identità verticale corretta del salone
    assert "Salone Bella Vista" in prompt
    assert "Parrucchiere" in prompt

    # Verifica: il prompt include la gerarchia di priorità
    assert "1. Dati struttura" in prompt
    assert "2. FAQ" in prompt
    assert "3. Documenti" in prompt
    assert "4. Pagine web" in prompt
    assert '"priority":1' in prompt
    assert '"priority":3' in prompt

    # Verifica: il tester restituisce metadati arricchiti con priorità e stato
    assert set(committed) == {"risposta", "fonti"}
    fonti = committed["fonti"]
    assert len(fonti) >= 1
    p1_fonti = [f for f in fonti if f["priorita"] == 1]
    assert len(p1_fonti) == 1
    assert p1_fonti[0]["stato"] == "indicizzata"
    assert p1_fonti[0]["is_active"] is True


async def test_toggle_disattivazione_esclude_fonte_da_retrieval(repo, sample_org):
    """Verifica che una fonte disattivata tramite soft-toggle NON venga recuperata dal RAG."""
    await repo.pool.execute(
        "UPDATE organizations SET subscription_status='trialing', plan='business', "
        "trial_start=now()-interval '1 hour', trial_end=now()+interval '6 days' WHERE id=$1",
        sample_org["id"],
    )
    doc = await _publish_text(
        repo, sample_org["id"], "promozione_segreta.txt",
        "Codice sconto segreto: VIP2026", tipo="documento", fonte="promozione_segreta.txt",
    )

    # Con documento attivo: recupera contesto lo trova
    with patch("src.core.documenti.rag_context.vettorizza", side_effect=lambda texts, tipo="query": [[0.1] * 384 for _ in texts]):
        ctx_attivo = await recupera_contesto_documenti(str(sample_org["id"]), "codice sconto", repo)
    assert "VIP2026" in ctx_attivo.testo
    assert ctx_attivo.reason == "verified"
    assert any(c["stato"] == "indicizzata" and c["is_active"] for c in ctx_attivo.chunks)

    # Disattiva il documento
    toggled = await repo.toggle_document_active(sample_org["id"], doc["id"])
    assert toggled["is_active"] is False

    # Con documento disattivato: recupera contesto restituisce vuoto
    with patch("src.core.documenti.rag_context.vettorizza", side_effect=lambda texts, tipo="query": [[0.1] * 384 for _ in texts]):
        ctx_disattivato = await recupera_contesto_documenti(str(sample_org["id"]), "codice sconto", repo)
    assert "VIP2026" not in ctx_disattivato.testo


async def test_tenant_isolation_conoscenza(async_client, sample_org, other_org):
    """Verifica che l'isolamento multi-tenant impedisca leak tra organizzazioni."""
    with patch("src.api.main.vettorizza", side_effect=lambda texts, tipo="passage": [[0.1] * 384 for _ in texts]):
        # Crea FAQ per Org A
        faq_res = await async_client.post(
            "/api/conoscenza/faq",
            json={"domanda": "Orario segreto org A", "risposta": "Solo su appuntamento privato."},
            headers=_headers(sample_org["id"]),
        )
        assert faq_res.status_code == 200
        faq_id = faq_res.json()["id"]

    # Org B non deve vedere la FAQ in elenco
    elenco_b = await async_client.get(
        "/api/documenti/elenco?tipo=faq",
        headers=_headers(other_org["id"]),
    )
    assert all(d["id"] != faq_id for d in elenco_b.json()["documenti"])

    # Org B non deve poter modificare o cancellare la FAQ di Org A
    del_b = await async_client.delete(
        f"/api/conoscenza/faq/{faq_id}",
        headers=_headers(other_org["id"]),
    )
    assert del_b.status_code == 404

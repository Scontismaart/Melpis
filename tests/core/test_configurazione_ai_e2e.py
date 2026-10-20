import os
import re
import uuid
from unittest.mock import patch
import httpx
import pytest

from src.core.documenti.rag_context import recupera_contesto_documenti
from src.core.onboarding import save_profile
from src.core.receptionist.conversation_orchestrator import ConversationOrchestrator
from src.core.receptionist.models import OrchestrationInput
from src.core.guardrails.intent_classifier import IntentResult
from src.models.schemas import OnboardingProfileInput, RispostaOutput
from src.agents.responder_agent import crea_responder_agent
from src.agents.prompts import serializza_dati_non_fidati

API_KEY = "test-api-key-12345"
pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def set_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("API_KEY_SERVICE", API_KEY)
    monkeypatch.setenv("MISTRAL_API_KEY", "mock-mistral-ci-key")


@pytest.fixture
async def async_client(repo, pg_pool, install_test_identity, sample_org, other_org):
    from src.api.main import app
    await pg_pool.execute(
        "UPDATE organizations SET subscription_status = 'active', plan = 'business' WHERE id = ANY($1::uuid[])",
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


async def test_configurazione_ai_salvataggio_completo_e_rag_sync(async_client, repo, sample_org, other_org):
    """Verifica che:
    1. Il salvataggio del profilo da 'Configurazione AI' persista identità, tono, multilingua e regole.
    2. Gli orari di apertura vengano sincronizzati come chunk RAG a Priorità 1 (Dati struttura).
    3. Il prompt del responder rifletta il nuovo nome, settore e regole.
    4. Ci sia rigoroso isolamento multi-tenant (Org B non vede le impostazioni di Org A).
    """
    payload = {
        "verticale": "centro_estetico",
        "nome_attivita": "Oasi del Benessere SPA",
        "orari": "Mar-Sab: 10:00 - 20:00 (Lunedì e Domenica chiuso)",
        "descrizione": "Centro estetico e SPA specializzato in massaggi rilassanti e trattamenti viso anti-age.",
        "tono": "formale_elegante",
        "servizi": [],  # La nuova vista non usa la textarea di servizi grezzi
        "regole_escalation": [
            "Donne in gravidanza per trattamenti specifici",
            "Richieste pacchetti matrimonio personalizzati"
        ],
        "lingue_supportate": ["it", "en", "fr"],
        "lingua_default": "it"
    }

    with patch("src.api.main.vettorizza", return_value=[[0.1] * 384]):
        resp = await async_client.post(
            "/api/onboarding/profilo",
            json=payload,
            headers=_headers(sample_org["id"]),
        )
    assert resp.status_code == 200
    res_data = resp.json()["profilo"]
    assert res_data["nome_attivita"] == "Oasi del Benessere SPA"
    assert res_data["verticale"] == "centro_estetico"
    assert res_data["tono"] == "formale_elegante"
    assert len(res_data["regole_escalation"]) == 2

    # 1. Verifica sincronizzazione RAG Dati Struttura (Priorità 1)
    with patch("src.core.documenti.rag_context.vettorizza", return_value=[[0.1] * 384]):
        ctx = await recupera_contesto_documenti(str(sample_org["id"]), "quando siete aperti?", repo)
    assert "Mar-Sab: 10:00 - 20:00" in ctx.testo
    assert any(c["tipo"] == "dati_struttura" for c in ctx.chunks)

    # 2. Verifica prompt del responder per il verticale centro estetico
    bp = await repo.get_org_business_profile(sample_org["id"])
    from src.whatsapp.inbound_processor import _profile_from_dict
    profilo_obj = _profile_from_dict(bp)
    with patch("src.agents.responder_agent.crea_llm", return_value="openai/mock-model"):
        agent = crea_responder_agent(profilo_obj)
    backstory = agent.backstory

    assert "Oasi del Benessere SPA" in backstory
    assert "Centro Estetico" in backstory or "Estetico" in backstory
    assert "Donne in gravidanza" in backstory
    assert "Mar-Sab: 10:00 - 20:00" in backstory

    # 3. Verifica isolamento multi-tenant
    resp_other = await async_client.get(
        "/api/onboarding/profilo",
        headers=_headers(other_org["id"]),
    )
    assert resp_other.status_code == 200
    other_prof = resp_other.json().get("profilo") or {}
    assert other_prof.get("nome_attivita") != "Oasi del Benessere SPA"


async def test_bidirezionalita_orari_conoscenza_e_configurazione_ai(async_client, repo, sample_org):
    """Verifica che aggiornando gli orari da Dati struttura in Conoscenza,
    il business profile venga aggiornato e reso disponibile alla Configurazione AI."""
    with patch("src.api.main.vettorizza", return_value=[[0.1] * 384]):
        put_res = await async_client.put(
            "/api/conoscenza/dati-struttura",
            json={
                "servizi": [
                    {"nome": "Massaggio Relax", "prezzo": 60.0, "durata_minuti": 50, "operatore": "Sara"}
                ],
                "orari": "Tutti i giorni: 08:00 - 22:00"
            },
            headers=_headers(sample_org["id"]),
        )
    assert put_res.status_code == 200

    # Recupera il profilo tramite l'endpoint usato da Configurazione AI
    get_res = await async_client.get(
        "/api/onboarding/profilo",
        headers=_headers(sample_org["id"]),
    )
    assert get_res.status_code == 200
    prof = get_res.json()["profilo"]
    assert prof["orari"] == "Tutti i giorni: 08:00 - 22:00"


async def test_tone_only_profile_save_invalidates_only_organization_faq_cache(
    async_client, repo, sample_org, other_org
):
    original = {
        "verticale": "centro_estetico",
        "nome_attivita": "Oasi del Benessere SPA",
        "orari": "",
        "descrizione": "Centro estetico e SPA.",
        "tono": "caldo e informale",
        "servizi": ["Massaggio Relax"],
        "regole_escalation": [],
        "lingue_supportate": ["it"],
        "lingua_default": "it",
    }
    await save_profile(
        str(sample_org["id"]), OnboardingProfileInput(**original), repo
    )
    embedding = [0.1] * 384
    await repo.doc_repo.faq_cache_store(
        str(sample_org["id"]), "Quanto costa il massaggio?", "Risposta precedente A",
        embedding,
    )
    await repo.doc_repo.faq_cache_store(
        str(other_org["id"]), "Quanto costa il massaggio?", "Risposta precedente B",
        embedding,
    )
    payload = {**original, "tono": "formale e istituzionale"}

    response = await async_client.post(
        "/api/onboarding/profilo", json=payload,
        headers=_headers(sample_org["id"]),
    )

    assert response.status_code == 200
    assert await repo.doc_repo.faq_cache_lookup(str(sample_org["id"]), embedding, 0.08) is None
    still_cached = await repo.doc_repo.faq_cache_lookup(str(other_org["id"]), embedding, 0.08)
    assert still_cached["answer_text"] == "Risposta precedente B"


async def test_profile_save_reports_org_scoped_faq_invalidation_failure(
    async_client, repo, sample_org, other_org, monkeypatch
):
    attempts = []
    invalidate = repo.faq_cache_invalidate

    async def fail_only_sample_org(organization_id):
        attempts.append(str(organization_id))
        if str(organization_id) == str(sample_org["id"]):
            raise RuntimeError("private cache backend details")
        return await invalidate(organization_id)

    monkeypatch.setattr(repo, "faq_cache_invalidate", fail_only_sample_org)
    payload = {
        "verticale": "centro_estetico",
        "nome_attivita": "Profilo salvato prima dell'errore",
        "orari": "",
        "tono": "calmo e chiaro",
        "servizi": [],
        "regole_escalation": [],
        "lingue_supportate": ["it"],
        "lingua_default": "it",
    }

    failed = await async_client.post(
        "/api/onboarding/profilo", json=payload,
        headers=_headers(sample_org["id"]),
    )
    assert failed.status_code == 503
    assert failed.json()["detail"] == (
        "Profilo salvato, ma non è stato possibile aggiornare la cache FAQ. Riprova."
    )
    assert "private cache backend details" not in failed.text
    persisted = await repo.get_onboarding_profile(sample_org["id"])
    assert persisted["nome_attivita"] == payload["nome_attivita"]

    other_payload = {**payload, "nome_attivita": "Altro tenant"}
    succeeded = await async_client.post(
        "/api/onboarding/profilo", json=other_payload,
        headers=_headers(other_org["id"]),
    )
    assert succeeded.status_code == 200
    assert attempts == [str(sample_org["id"]), str(other_org["id"])]


async def test_same_question_uses_live_profile_and_knowledge_context_after_config_changes(
    async_client, repo, sample_org, monkeypatch, mock_provider_context_open,
):
    """Build the real CrewAI prompts from DB profile/RAG data with runtime mocked."""
    question = "Quanto costa il Massaggio Relax?"
    document = await repo.create_document(
        str(sample_org["id"]), "listino.txt", tipo="upload", fonte="listino.txt"
    )
    await repo.add_chunk(
        str(sample_org["id"]), str(document["id"]), 0,
        "Massaggio Relax: 25 euro, durata 50 minuti.", [0.1] * 384,
        {"fonte": "listino.txt"},
    )

    payload = {
        "verticale": "centro_estetico",
        "nome_attivita": "Oasi del Benessere SPA",
        "orari": "Mar-Sab: 10:00 - 20:00",
        "descrizione": "Centro estetico e SPA.",
        "tono": "caldo e informale",
        "servizi": ["Massaggio Relax"],
        "regole_escalation": [],
        "lingue_supportate": ["it"],
        "lingua_default": "it",
    }
    observed = []

    async def mock_provider_boundary(message, profile, **kwargs):
        from src.agents.responder_agent import crea_responder_agent, crea_responder_task

        agent = crea_responder_agent(
            profile,
            model="mock-runtime",
            variante=kwargs.get("variante", "control"),
            provider_context=kwargs.get("provider_context"),
        )
        task = crea_responder_task(
            agent,
            message,
            kwargs.get("cronologia"),
            kwargs.get("contesto_documenti", ""),
        )
        observed.append({
            "role": agent.role,
            "backstory": agent.backstory,
            "task": task.description,
        })
        return RispostaOutput(
            risposta="Risposta generata dal runtime mock.",
            richiede_umano=False, motivo="", categoria="info",
        )

    monkeypatch.setattr(
        "src.core.receptionist.conversation_orchestrator.genera_risposta_async",
        mock_provider_boundary,
    )
    monkeypatch.setattr("src.agents.responder_agent.crea_llm", lambda **_kwargs: "mock-runtime")
    monkeypatch.setattr(
        "src.core.receptionist.conversation_orchestrator.classifica_intent",
        lambda _text, **_kwargs: _async_value(IntentResult(intent="faq", confidence=1.0, source="test")),
    )
    monkeypatch.setattr(
        "src.core.receptionist.conversation_orchestrator.faq_cache.cache_enabled",
        lambda: False,
    )
    monkeypatch.setattr(
        "src.core.documenti.rag_context.vettorizza",
        lambda _texts, tipo="query": [[0.1] * 384],
    )
    monkeypatch.setattr(
        "src.integrations.airtable.wiring.select_airtable_tools",
        lambda **_kwargs: _async_value([]),
    )
    orchestrator = ConversationOrchestrator(org_repo=repo, doc_repo=repo)

    async def ask():
        await orchestrator.orchestrate(OrchestrationInput(
            organization_id=sample_org["id"], text=question,
            auth_user_id=uuid.uuid4(), record_billing_usage=False,
        ))

    profile_response = await async_client.post(
        "/api/onboarding/profilo", json=payload,
        headers=_headers(sample_org["id"]),
    )
    assert profile_response.status_code == 200
    await ask()
    payload["tono"] = "formale e istituzionale"
    payload["nome_attivita"] = "Oasi SPA Milano"
    profile_response = await async_client.post(
        "/api/onboarding/profilo", json=payload,
        headers=_headers(sample_org["id"]),
    )
    assert profile_response.status_code == 200
    await ask()
    removed = await async_client.delete(
        f"/api/documenti/{document['id']}",
        headers=_headers(sample_org["id"]),
    )
    assert removed.status_code == 200
    updated = await repo.create_document(
        str(sample_org["id"]), "listino-aggiornato.txt", tipo="upload", fonte="listino.txt"
    )
    await repo.add_chunk(
        str(sample_org["id"]), str(updated["id"]), 0,
        "Massaggio Relax: 40 euro, durata 50 minuti.", [0.1] * 384,
        {"fonte": "listino.txt"},
    )
    await ask()

    assert len(observed) == 3
    first, second, third = observed
    assert all(serializza_dati_non_fidati(question) in call["task"] for call in observed)
    old_fact = "Massaggio Relax: 25 euro, durata 50 minuti."
    new_fact = "Massaggio Relax: 40 euro, durata 50 minuti."
    assert old_fact in first["task"] and new_fact not in first["task"]
    def without_live_clock(prompt: str) -> str:
        return re.sub(r"\bore \d{2}:\d{2}\b", "ore <time>", prompt)

    assert without_live_clock(second["task"]) == without_live_clock(first["task"])
    assert new_fact in third["task"] and old_fact not in third["task"]
    assert first["role"] == "Assistente clienti di Oasi del Benessere SPA"
    assert second["role"] == "Assistente clienti di Oasi SPA Milano"
    assert first["backstory"] != second["backstory"]
    assert "caldo e informale" in first["backstory"]
    assert "formale e istituzionale" in second["backstory"]
    assert second["backstory"] == third["backstory"]


async def _async_value(value):
    return value

import json

import pytest
from unittest.mock import patch

from src.core import onboarding
from src.models.schemas import OnboardingProfileInput, PreviewInput, RispostaOutput
from src.whatsapp.inbound_processor import _profile_from_dict


def _profile(**overrides):
    data = {
        "verticale": "parrucchiere",
        "nome_attivita": "Studio Capelli Nora",
        "orari": "Martedi-Sabato 09:00-19:00",
        "tono": "gentile, curato, pratico",
        "servizi": ["Taglio", "Colore", "Piega"],
        "regole_escalation": ["Correzioni colore complesse", "Reclami"],
    }
    data.update(overrides)
    return OnboardingProfileInput(**data)


def test_list_verticals():
    verticali = {v["id"]: v for v in onboarding.list_verticals()}
    assert "ristorante" in verticali
    assert "parrucchiere" in verticali
    assert verticali["parrucchiere"]["label"] == "Parrucchiere / Barber"


def test_build_business_profile_con_default_dal_template():
    profile = onboarding.build_business_profile(
        _profile(tono="", servizi=[], regole_escalation=[])
    )
    # template del verticale usato quando il wizard non compila i campi
    assert profile.tono == "curato, rassicurante, pratico"
    assert "Taglio donna e uomo" in profile.servizi_principali


[pytest.mark.asyncio]
async def test_save_e_get_profilo_org_scoped(repo, sample_org, other_org):
    saved = await onboarding.save_profile(sample_org["id"], _profile(), repo)
    assert saved["verticale"] == "parrucchiere"
    assert saved["nome_attivita"] == "Studio Capelli Nora"

    fetched = await onboarding.get_profile(sample_org["id"], repo)
    assert fetched is not None
    assert fetched["profilo"]["nome"] == "Studio Capelli Nora"
    # l'altra org non vede nulla
    assert await onboarding.get_profile(other_org["id"], repo) is None


[pytest.mark.asyncio]
async def test_sync_business_profile_leggibile_dal_responder_reale(repo, sample_org):
    """Il JSON scritto su organizations.business_profile deve avere esattamente
    le chiavi che _profile_from_dict (inbound_processor) si aspetta: se il
    sync avesse un formato diverso, il responder in produzione riceverebbe un
    profilo silenziosamente errato. Verifica il round-trip 1:1."""
    payload = _profile()
    await onboarding.save_profile(sample_org["id"], payload, repo)

    async with repo.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT business_profile FROM organizations WHERE id = $1",
            sample_org["id"],
        )
        # asyncpg restituisce jsonb come stringa: il repo la parsea con
        # json.loads (vedi repository._json_fields_*), qui lo facciamo a mano.
        raw = json.loads(row["business_profile"])

    riletto = _profile_from_dict(raw)
    atteso = onboarding.build_business_profile(payload)
    assert riletto.nome == atteso.nome
    assert riletto.tipo_attivita == atteso.tipo_attivita
    assert riletto.tono == atteso.tono
    assert riletto.orari == atteso.orari
    assert riletto.servizi_principali == atteso.servizi_principali
    assert riletto.note_speciali == atteso.note_speciali


[pytest.mark.asyncio]
async def test_cross_tenant_non_sovrascrive_business_profile(repo, sample_org, other_org):
    await onboarding.save_profile(sample_org["id"], _profile(nome_attivita="Studio A"), repo)
    await onboarding.save_profile(other_org["id"], _profile(nome_attivita="Studio B"), repo)

    async with repo.pool.acquire() as conn:
        row_a = await conn.fetchrow(
            "SELECT business_profile FROM organizations WHERE id = $1", sample_org["id"]
        )
        row_b = await conn.fetchrow(
            "SELECT business_profile FROM organizations WHERE id = $1", other_org["id"]
        )
    assert json.loads(row_a["business_profile"])["nome"] == "Studio A"
    assert json.loads(row_b["business_profile"])["nome"] == "Studio B"


[pytest.mark.asyncio]
async def test_upsert_su_secondo_salvataggio_una_sola_riga(repo, sample_org):
    await onboarding.save_profile(sample_org["id"], _profile(), repo)
    saved2 = await onboarding.save_profile(
        sample_org["id"], _profile(nome_attivita="Studio Capelli Nora 2"), repo
    )
    assert saved2["nome_attivita"] == "Studio Capelli Nora 2"
    async with repo.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM onboarding_profiles WHERE organization_id = $1",
            sample_org["id"],
        )
    assert len(rows) == 1


[pytest.mark.asyncio]
async def test_save_scrive_evento_su_event_log(repo, sample_org):
    """Audit riusando event_log (stesso feed di task13/HITL): il record lo
    scrive un trigger DB, come per messages/reviews."""
    await onboarding.save_profile(sample_org["id"], _profile(), repo)
    async with repo.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM event_log WHERE organization_id = $1 AND tipo_evento = 'onboarding'",
            sample_org["id"],
        )
    assert len(rows) == 1
    assert rows[0]["source_table"] == "onboarding_profiles"
    assert rows[0]["testo_originale"] == "Studio Capelli Nora"


async def test_preview_rag_org_scoped_no_leak(repo, sample_org, other_org):
    """Il contesto RAG della preview e' org-scoped: una preview dell'org B
    NON deve mai ricevere i documenti dell'org A (nemmeno un chunk). E'
    il controllo di isolamento multi-tenant piu' importante del wizard."""
    doc = await repo.create_document(
        str(sample_org["id"]), "menu-segreto.txt", tipo="upload", fonte="menu-segreto.txt"
    )
    await repo.add_chunk(
        str(sample_org["id"]), str(doc["id"]), 0,
        "Ingrediente segreto dell'org A: tartufo nero. Solo i clienti dell'org A lo vedono.",
        [0.1] * 384,
        {"fonte": "menu-segreto.txt"},
    )

    captured: dict[str, str] = {"contesto": ""}

    async def fake_risposta(messaggio, profilo, billing=None, contesto_documenti="", intent=None, variante="control", **kwargs):
        captured["contesto"] = contesto_documenti
        return RispostaOutput(risposta="ok", richiede_umano=False, motivo="", categoria="info")

    with patch("src.core.documenti.rag_context.vettorizza", return_value=[[0.1] * 384]), \
         patch("src.core.onboarding.genera_risposta_async", side_effect=fake_risposta):
        await onboarding.generate_preview(
            str(sample_org["id"]),
            PreviewInput(profilo=_profile(), messaggio="Cosa contiene il menu?"),
            repo,
            billing={"subscription_status": "active", "messages_limit": 500, "messages_used_this_period": 0},
        )
    assert "tartufo nero" in captured["contesto"]

    captured["contesto"] = ""
    with patch("src.core.documenti.rag_context.vettorizza", return_value=[[0.1] * 384]), \
         patch("src.core.onboarding.genera_risposta_async", side_effect=fake_risposta):
        await onboarding.generate_preview(
            str(other_org["id"]),
            PreviewInput(profilo=_profile(), messaggio="Cosa contiene il menu?"),
            repo,
            billing={"subscription_status": "active", "messages_limit": 500, "messages_used_this_period": 0},
        )
    # l'org B non ha documenti: contesto vuoto, nessun leak dal tenant A
    assert captured["contesto"] == ""


@pytest.mark.asyncio
async def test_generate_preview_forwards_usage_sink(repo, sample_org):
    usage_sink = {"attempts": []}

    async def fake_risposta(*args, usage_sink=None, **kwargs):
        assert usage_sink is not None
        usage_sink["attempts"].append({"model": "test-model"})
        return RispostaOutput(risposta="ok", richiede_umano=False, motivo="", categoria="info")

    with patch("src.core.onboarding.genera_risposta_async", side_effect=fake_risposta):
        await onboarding.generate_preview(
            str(sample_org["id"]),
            PreviewInput(profilo=_profile(), messaggio="Cosa contiene il menu?"),
            repo,
            usage_sink=usage_sink,
            billing={"subscription_status": "active", "messages_limit": 500, "messages_used_this_period": 0},
        )

    assert usage_sink["attempts"] == [{"model": "test-model"}]

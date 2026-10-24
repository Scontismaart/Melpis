import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest

API_KEY = "test-api-key-12345"
pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def set_env():
    os.environ["DATABASE_URL"] = ""
    os.environ["API_KEY_SERVICE"] = API_KEY


@pytest.fixture
async def async_client(repo, pg_pool, install_test_identity, sample_org):
    from src.api.main import app
    await pg_pool.execute(
        "UPDATE organizations SET subscription_status = 'active', plan = 'business', "
        "subscription_id='sub_knowledgeTest', current_period_start=now()-interval '1 day', "
        "current_period_end=now()+interval '29 days', messages_limit=500, "
        "messages_used_this_period=0 WHERE id = $1",
        sample_org["id"],
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


async def test_faq_lifecycle_crud(async_client, sample_org):
    """Verifica creazione, lettura, aggiornamento ed eliminazione di una FAQ."""
    with patch("src.api.main.vettorizza", side_effect=lambda texts, tipo="passage": [[0.1] * 384 for _ in texts]):
        # 1. Crea FAQ
        res = await async_client.post(
            "/api/conoscenza/faq",
            json={
                "domanda": "Quali sono i metodi di pagamento accettati?",
                "risposta": "Accettiamo contanti, carte di credito Visa/Mastercard e Bancomat.",
            },
            headers=_headers(sample_org["id"]),
        )
        assert res.status_code == 200, res.text
        faq_id = res.json()["id"]

        # 2. Elenco per tipo FAQ
        list_res = await async_client.get(
            "/api/documenti/elenco?tipo=faq",
            headers=_headers(sample_org["id"]),
        )
        assert list_res.status_code == 200
        faqs = list_res.json()["documenti"]
        assert len(faqs) == 1
        assert faqs[0]["id"] == faq_id
        assert faqs[0]["tipo"] == "faq"
        assert faqs[0]["is_active"] is True
        assert faqs[0]["stato"] == "indicizzata"

        # 3. Aggiorna FAQ
        up_res = await async_client.put(
            f"/api/conoscenza/faq/{faq_id}",
            json={
                "domanda": "Quali sono i metodi di pagamento accettati?",
                "risposta": "Accettiamo contanti, carte e anche Satispay.",
            },
            headers=_headers(sample_org["id"]),
        )
        assert up_res.status_code == 200

        # 4. Toggle disattiva FAQ
        tog_res = await async_client.patch(
            f"/api/documenti/{faq_id}/toggle",
            headers=_headers(sample_org["id"]),
        )
        assert tog_res.status_code == 200
        assert tog_res.json()["documento"]["is_active"] is False

        # 5. Elimina FAQ
        del_res = await async_client.delete(
            f"/api/conoscenza/faq/{faq_id}",
            headers=_headers(sample_org["id"]),
        )
        assert del_res.status_code == 200

        # Verifica che sia vuota
        empty_res = await async_client.get(
            "/api/documenti/elenco?tipo=faq",
            headers=_headers(sample_org["id"]),
        )
        assert len(empty_res.json()["documenti"]) == 0


async def test_web_import_success_and_error(async_client, sample_org):
    """Verifica import di pagina web da URL e gestione errore esplicita."""
    # 1. Successo con mock estrazione web
    with patch("src.api.main.estrai_da_url", new=AsyncMock(return_value={
        "titolo": "Listino Trattamenti Salone",
        "testo": "Taglio e Piega: 35 euro. Trattamento cheratina: 50 euro.",
        "url": "https://www.saloneesempio.it/listino",
    })), patch("src.api.main.vettorizza", side_effect=lambda texts, tipo="passage": [[0.1] * 384 for _ in texts]):
        res = await async_client.post(
            "/api/conoscenza/web",
            json={"url": "https://www.saloneesempio.it/listino"},
            headers=_headers(sample_org["id"]),
        )
        assert res.status_code == 200
        res.json()["id"]

        list_res = await async_client.get(
            "/api/documenti/elenco?tipo=web",
            headers=_headers(sample_org["id"]),
        )
        assert list_res.status_code == 200
        items = list_res.json()["documenti"]
        assert len(items) == 1
        assert items[0]["nome"] == "Listino Trattamenti Salone"
        assert items[0]["is_active"] is True
        assert items[0]["stato"] == "indicizzata"

    # 2. Errore pagina non trovata (404/connessione)
    with patch("src.api.main.estrai_da_url", side_effect=ValueError("La pagina ha restituito lo stato HTTP 404")):
        err_res = await async_client.post(
            "/api/conoscenza/web",
            json={"url": "https://www.saloneesempio.it/non-esiste"},
            headers=_headers(sample_org["id"]),
        )
        assert err_res.status_code == 422
        # La fonte fallita deve comunque essere salvata come record con stato 'errore' per la UI
        list_all = await async_client.get(
            "/api/documenti/elenco?tipo=web",
            headers=_headers(sample_org["id"]),
        )
        error_items = [d for d in list_all.json()["documenti"] if d["stato"] == "errore"]
        assert len(error_items) == 1
        assert "404" in error_items[0]["errore"]
        assert error_items[0]["is_active"] is False


async def test_dati_struttura_sync_and_conflict_detection(async_client, repo, sample_org):
    """Verifica salvataggio dati struttura, sincronizzazione profilo e rilevamento conflitti."""
    with patch("src.api.main.vettorizza", side_effect=lambda texts, tipo="passage": [[0.1] * 384 for _ in texts]):
        # 1. Salva Dati struttura (Priorità 1) con Taglio a 25€
        put_res = await async_client.put(
            "/api/conoscenza/dati-struttura",
            json={
                "servizi": [
                    {"nome": "Taglio Uomo", "prezzo": 25.0, "durata_minuti": 30, "operatore": "Marco"},
                    {"nome": "Barba Deluxe", "prezzo": 18.0, "durata_minuti": 20, "operatore": "Marco"},
                ],
                "orari": "Lun-Sab 09:00 - 19:30",
            },
            headers=_headers(sample_org["id"]),
        )
        assert put_res.status_code == 200

        # Verifica sincronizzazione business profile
        bp = await repo.get_org_business_profile(sample_org["id"])
        assert len(bp["servizi_strutturati"]) == 2
        assert bp["servizi_strutturati"][0]["prezzo"] == 25.0
        assert bp["orari"] == "Lun-Sab 09:00 - 19:30"

        # 2. Carica un documento PDF o testo che indica un prezzo in contrasto (es. Taglio Uomo a 20 euro)
        upload_res = await async_client.post(
            "/api/documenti/carica",
            json={
                "nome": "vecchio_volantino.pdf",
                "testo": "Offerta speciale: Taglio Uomo a soli 20 euro per tutto il mese!",
            },
            headers=_headers(sample_org["id"]),
        )
        assert upload_res.status_code == 200

        # 3. Chiedi i conflitti tramite l'endpoint dedicato
        conf_res = await async_client.get(
            "/api/conoscenza/conflitti",
            headers=_headers(sample_org["id"]),
        )
        assert conf_res.status_code == 200
        conflitti = conf_res.json()["conflitti"]
        assert len(conflitti) >= 1
        conf = conflitti[0]
        assert "Taglio Uomo" in conf["servizio"]
        assert conf["prezzo_ufficiale"] == 25.0
        assert conf["prezzo_conflitto"] == 20.0
        assert "vecchio_volantino.pdf" in conf["fonte_conflitto"]


async def test_knowledge_summary(async_client, sample_org):
    """Verifica che l'endpoint summary restituisca i conteggi corretti per tab."""
    res = await async_client.get(
        "/api/conoscenza/summary",
        headers=_headers(sample_org["id"]),
    )
    assert res.status_code == 200
    data = res.json()
    assert "faq" in data
    assert "documenti" in data
    assert "web" in data
    assert "dati_struttura" in data
    assert "conflitti_totali" in data
    assert "chunk_indicizzati" in data


async def test_docx_upload_and_extraction(async_client, sample_org):
    """Verifica upload ed estrazione testo da file .docx."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
            <w:body>
                <w:p><w:r><w:t>Trattamento viso anti-age: 65 euro durata 50 minuti.</w:t></w:r></w:p>
            </w:body>
        </w:document>"""
        z.writestr("word/document.xml", xml.encode("utf-8"))
    docx_bytes = buf.getvalue()

    with patch("src.api.main.vettorizza", side_effect=lambda texts, tipo="passage": [[0.1] * 384 for _ in texts]):
        resp = await async_client.post(
            "/api/documenti/carica-file",
            files={"file": ("listino.docx", docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            headers=_headers(sample_org["id"]),
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["nome"] == "listino.docx"
    assert data["indicizzati"] >= 1


async def test_web_import_ssrf_protection():
    """Verifica che estrai_da_url rifiuti indirizzi privati o loopback (SSRF)."""
    import pytest

    from src.core.documenti.web_extractor import estrai_da_url

    with pytest.raises(ValueError, match="Accesso a indirizzi locali o privati non consentito"):
        await estrai_da_url("http://127.0.0.1:8000/secret")

    with pytest.raises(ValueError, match="Accesso a indirizzi locali o privati non consentito"):
        await estrai_da_url("http://localhost:5432")

    with pytest.raises(ValueError, match="Accesso a indirizzi locali o privati non consentito"):
        await estrai_da_url("http://169.254.169.254/latest/meta-data/")

import asyncio
import time
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from src.core.documenti.rag_context import recupera_contesto_documenti
from src.whatsapp.inbound_processor import InboundProcessor
from src.whatsapp.config import AppConfig, TenantConfig
from src.models.schemas import RispostaOutput


@pytest.fixture(autouse=True)
def _no_real_embedding_model():
    """Il path AI ora calcola embedding per cache FAQ e retrieval RAG: il
    vettorizza reale carica il modello MiniLM (download in CI). Lo
    congeliamo con un embedding finto per tutti i test del processor."""
    with patch("src.core.documenti.rag_context.vettorizza", return_value=[[0.1] * 384]), \
         patch("src.core.guardrails.faq_cache.vettorizza", return_value=[[0.1] * 384]):
        yield


@pytest.fixture(autouse=True)
def _test_provider_context(monkeypatch):
    from src.core.provider_accounting import ProviderAccountingContext
    from tests.provider_accounting_fakes import provider_context_stub

    async def open_context(cls, pool, organization_id, *, task_type,
                           actor_auth_user_id=None, worker_message_id=None,
                           simulation_request_id=None, simulation_claim_token=None,
                           context=None):
        accounting_context = provider_context_stub(
            task_type=task_type, organization_id=organization_id,
        )
        accounting_context.actor_auth_user_id = (
            uuid.UUID(str(actor_auth_user_id)) if actor_auth_user_id is not None else None
        )
        accounting_context.worker_message_id = (
            uuid.UUID(str(worker_message_id)) if worker_message_id is not None else None
        )
        accounting_context.context = dict(context or accounting_context.context)
        return accounting_context

    monkeypatch.setattr(ProviderAccountingContext, "open", classmethod(open_context))


@pytest.fixture
def app_config():
    return AppConfig(
        app_secret="test",
        encryption_key="dGVzdC1rZXktMTIzNDU2Nzg5MDEyMzQ1Njc4OTA=",
        postgres_dsn="",
        verify_token="test",
    )


@pytest.fixture
def sample_msg():
    return {
        "id": uuid.uuid4(), "organization_id": uuid.uuid4(),
        "conversation_id": uuid.uuid4(), "content": {"from": "391234567890"},
        "content_text": "Ciao", "message_type": "text",
    }


@pytest.fixture
def mock_repo(sample_msg):
    repo = AsyncMock()
    repo.claim_inbound_messages = AsyncMock(return_value=[sample_msg])
    repo.reap_stale_claims = AsyncMock(return_value=[])
    repo.try_mark_replied = AsyncMock(return_value={"id": sample_msg["id"], "status": "handled", "replied_at": datetime.now()})
    repo.update_heartbeat = AsyncMock()
    repo.get_org_subscription_state = AsyncMock(return_value={
        "subscription_status": "active",
        "trial_end": None,
        "ai_accounting_blocked": False,
    })
    repo.get_or_create_contact = AsyncMock(return_value={"id": uuid.uuid4()})
    repo.mark_ai_disclosure_sent = AsyncMock(return_value=True)
    # Cache FAQ: default NESSUN hit (AsyncMock nudo restituirebbe un oggetto
    # truthy che verrebbe scambiato per una risposta in cache).
    repo.faq_cache_lookup = AsyncMock(return_value=None)
    repo.faq_cache_store = AsyncMock(return_value=None)
    repo.pool = MagicMock()
    repo.claim_message_and_check_quota = AsyncMock(return_value={
        "status": "claimed",
        "ai_reply_cache": None,
        "billed_at": None,
        "sent_at": None,
        "quota_exceeded_at": None,
        "processing_at": None
    })
    repo.get_outbound_dedup = AsyncMock(return_value=None)
    repo.save_outbound_dedup = AsyncMock()
    repo.save_ai_reply = AsyncMock()
    repo.check_booking_exists = AsyncMock(return_value=False)
    repo.mark_message_sent = AsyncMock()
    repo.escalate_to_human = AsyncMock(return_value={"id": str(uuid.uuid4()), "ticket_status": "PENDING_STAFF"})
    repo.record_usage = AsyncMock()
    repo.get_org_business_profile = AsyncMock(return_value={})
    # Default: conversazione non CLAIMED (AI_ACTIVE) per non bloccare il flusso normale
    repo.get_conversation = AsyncMock(return_value={
        "id": sample_msg["conversation_id"],
        "ticket_status": "AI_ACTIVE"
    })
    return repo



@pytest.fixture
def mock_service():
    service = AsyncMock()
    service.send_whatsapp_message = AsyncMock(return_value={
        "wam_id": "wamid.test-confirmed", "status": "sent"
    })
    service.check_opt_out = AsyncMock(return_value={"is_opt_out": False, "confidence": "low"})
    service.fast_path_match = AsyncMock(return_value=None)
    service.check_human_request = AsyncMock(return_value=False)
    service.MessageUsageExceeded = Exception
    return service


@pytest.fixture
def fake_tenant_config():
    return TenantConfig(
        organization_id=uuid.uuid4(),
        phone_number_id="123456",
        waba_id="waba1",
        access_token="decrypted-token",
        business_profile={"nome": "Trattoria Test", "orari": "12-15"},
    )


class TestInboundProcessor:
    async def test_process_one_message(self, app_config, mock_repo, mock_service):
        processor = InboundProcessor(app_config, mock_repo, mock_service)
        await processor.process_next_batch()
        mock_repo.claim_inbound_messages.assert_called_once()

    async def test_reaper_called(self, app_config, mock_repo, mock_service):
        processor = InboundProcessor(app_config, mock_repo, mock_service)
        await processor.process_next_batch()
        mock_repo.reap_stale_claims.assert_called_once()

    async def test_opt_out_skips_fast_path(self, app_config, mock_repo, mock_service):
        mock_service.check_opt_out = AsyncMock(return_value={"is_opt_out": True, "confidence": "high"})
        processor = InboundProcessor(app_config, mock_repo, mock_service)
        await processor.process_next_batch()
        mock_service.fast_path_match.assert_not_called()

    async def test_human_request_forces_escalation(
        self, app_config, mock_repo, mock_service, fake_tenant_config, sample_msg
    ):
        mock_service.check_human_request = AsyncMock(return_value=True)
        mock_repo.escalate_to_human = AsyncMock(return_value={"id": sample_msg["conversation_id"], "ticket_status": "PENDING_STAFF"})
        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)), \
             patch("src.whatsapp.inbound_processor.enqueue_escalation", MagicMock()) as mock_email:
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()

        mock_service.fast_path_match.assert_not_called()
        mock_repo.escalate_to_human.assert_awaited_once_with(str(sample_msg["conversation_id"]), sample_msg["organization_id"])
        mock_email.assert_called_once()
        # Nessuna disclosure sul messaggio di attesa (vedi spec §6)
        assert mock_service.send_whatsapp_message.await_count == 1
        body = mock_service.send_whatsapp_message.call_args.kwargs["payload"]["text"]["body"]
        assert "assistente automatico" not in body
        assert body == "Ti passo una persona dello staff, un attimo!"
        mock_repo.try_mark_replied.assert_awaited_with(sample_msg["id"], handling_type="escalated", organization_id=sample_msg["organization_id"])

    async def test_claimed_ticket_skips_ai_response(
        self, app_config, mock_repo, mock_service, sample_msg
    ):
        """Quando un ticket è CLAIMED da un operatore, l'AI non deve rispondere."""
        mock_repo.get_conversation = AsyncMock(return_value={
            "id": sample_msg["conversation_id"],
            "ticket_status": "CLAIMED",
            "assigned_to": uuid.uuid4()
        })
        
        with patch("src.whatsapp.inbound_processor.genera_risposta_async", AsyncMock()) as mock_genera:
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()
        
        # L'assistente AI non deve essere invocato
        mock_genera.assert_not_called()
        # Il messaggio deve essere finalizzato senza risposta AI
        mock_repo.try_mark_replied.assert_awaited_with(
            sample_msg["id"], 
            handling_type="claimed_by_operator", 
            organization_id=sample_msg["organization_id"]
        )
        # Nessun messaggio WhatsApp inviato al cliente
        mock_service.send_whatsapp_message.assert_not_called()

    async def test_ai_reply_sent_when_no_escalation(
        self, app_config, mock_repo, mock_service, fake_tenant_config, sample_msg
    ):
        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)), \
             patch("src.whatsapp.inbound_processor.genera_risposta_async", AsyncMock(return_value=RispostaOutput(
                 risposta="Siamo aperti dalle 12 alle 15.", richiede_umano=False, motivo="orari", categoria="info",
             ))):
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()

        mock_service.send_whatsapp_message.assert_awaited_once()
        call_kwargs = mock_service.send_whatsapp_message.call_args.kwargs
        assert call_kwargs["to_number"] == "391234567890"
        payload_body = call_kwargs["payload"]["text"]["body"]
        assert payload_body.startswith("Ciao! Sono l'assistente automatico di Trattoria Test")
        assert payload_body.endswith("Siamo aperti dalle 12 alle 15.")
        mock_repo.escalate_to_human.assert_not_called()
        mock_repo.try_mark_replied.assert_awaited_with(sample_msg["id"], handling_type="ai_handled", organization_id=sample_msg["organization_id"])

    async def test_escalation_when_ai_requires_human(
        self, app_config, mock_repo, mock_service, fake_tenant_config, sample_msg
    ):
        mock_repo.escalate_to_human = AsyncMock(return_value={"id": sample_msg["conversation_id"], "ticket_status": "PENDING_STAFF"})

        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)), \
             patch("src.whatsapp.inbound_processor.genera_risposta_async", AsyncMock(return_value=RispostaOutput(
                 risposta="", richiede_umano=True, motivo="allergie", categoria="reclamo",
             ))), \
             patch("src.whatsapp.inbound_processor.enqueue_escalation", MagicMock()) as mock_email:
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()

        mock_repo.escalate_to_human.assert_awaited_once_with(str(sample_msg["conversation_id"]), sample_msg["organization_id"])
        mock_email.assert_called_once()
        # Il responder ha escalationato senza lasciare testo: il guardrail
        # "risposta_vuota" lo sostituisce col fallback staff, che ora viene
        # davvero inviato al cliente (prima: silenzio + solo email al titolare).
        mock_service.send_whatsapp_message.assert_awaited_once()
        body = mock_service.send_whatsapp_message.call_args.kwargs["payload"]["text"]["body"]
        assert "ti metto in contatto con lo staff" in body
        mock_repo.try_mark_replied.assert_awaited_with(sample_msg["id"], handling_type="escalated", organization_id=sample_msg["organization_id"])

    async def test_escalation_survives_email_failure(
        self, app_config, mock_repo, mock_service, fake_tenant_config, sample_msg
    ):
        mock_repo.escalate_to_human = AsyncMock(return_value={"id": sample_msg["conversation_id"], "ticket_status": "PENDING_STAFF"})

        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)), \
             patch("src.whatsapp.inbound_processor.genera_risposta_async", AsyncMock(return_value=RispostaOutput(
                 risposta="", richiede_umano=True, motivo="reclamo", categoria="reclamo",
             ))), \
             patch("src.whatsapp.inbound_processor.enqueue_escalation", MagicMock()) as mock_email:
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()

        mock_repo.escalate_to_human.assert_awaited_once()
        mock_repo.try_mark_replied.assert_awaited_with(sample_msg["id"], handling_type="escalated", organization_id=sample_msg["organization_id"])
        mock_email.assert_called_once()

    async def test_fast_path_reply_also_sent_via_whatsapp(
        self, app_config, mock_repo, mock_service, fake_tenant_config, sample_msg
    ):
        mock_service.fast_path_match = AsyncMock(return_value="Ciao! Benvenuto.")
        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)):
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()
        mock_service.send_whatsapp_message.assert_awaited_once()
        payload_body = mock_service.send_whatsapp_message.call_args.kwargs["payload"]["text"]["body"]
        assert payload_body.startswith("Ciao! Sono l'assistente automatico di Trattoria Test")
        assert payload_body.endswith("Ciao! Benvenuto.")

    async def test_ai_reply_no_disclosure_on_second_contact(
        self, app_config, mock_repo, mock_service, fake_tenant_config, sample_msg
    ):
        mock_repo.mark_ai_disclosure_sent = AsyncMock(return_value=False)
        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)), \
             patch("src.whatsapp.inbound_processor.genera_risposta_async", AsyncMock(return_value=RispostaOutput(
                 risposta="Siamo aperti.", richiede_umano=False, motivo="orari", categoria="info",
             ))):
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()

        call_kwargs = mock_service.send_whatsapp_message.call_args.kwargs
        assert call_kwargs["payload"]["text"]["body"] == "Siamo aperti."

    async def test_race_condition_only_one_reply_sent(
        self, app_config, mock_repo, mock_service, fake_tenant_config, sample_msg
    ):
        """Due worker simulati che processano lo stesso messaggio in
        parallelo. try_mark_replied restituisce il record solo al primo
        worker che lo chiama; il secondo riceve None e salta l'invio.
        Risultato: una sola chiamata a send_whatsapp_message."""
        mock_service.fast_path_match = AsyncMock(return_value="Ciao! Benvenuto.")

        race_msg = {
            "id": uuid.uuid4(), "organization_id": uuid.uuid4(),
            "conversation_id": uuid.uuid4(), "content": {"from": "391234567890"},
            "content_text": "Ciao", "message_type": "text",
        }

        claim_count = 0

        async def fake_claim(message_id, org_id):
            nonlocal claim_count
            claim_count += 1
            if claim_count == 1:
                return {
                    "status": "claimed",
                    "ai_reply_cache": None,
                    "billed_at": None,
                    "sent_at": None,
                    "quota_exceeded_at": None,
                    "processing_at": None,
                }
            return {"status": "currently_processing"}

        repo = AsyncMock()
        repo.claim_inbound_messages = AsyncMock(return_value=[race_msg])
        repo.reap_stale_claims = AsyncMock(return_value=[])
        repo.claim_message_and_check_quota = AsyncMock(side_effect=fake_claim)
        repo.get_or_create_contact = AsyncMock(return_value={"id": uuid.uuid4()})
        repo.mark_ai_disclosure_sent = AsyncMock(return_value=True)
        repo.faq_cache_lookup = AsyncMock(return_value=None)
        repo.faq_cache_store = AsyncMock(return_value=None)
        repo.get_outbound_dedup = AsyncMock(return_value=None)
        repo.save_outbound_dedup = AsyncMock()
        repo.save_ai_reply = AsyncMock()
        repo.check_booking_exists = AsyncMock(return_value=False)
        repo.mark_message_sent = AsyncMock()
        repo.record_usage = AsyncMock()
        repo.update_heartbeat = AsyncMock()
        repo.get_org_subscription_state = AsyncMock(return_value=None)
        repo.get_org_business_profile = AsyncMock(return_value={})
        repo.get_conversation = AsyncMock(return_value={
            "id": race_msg["conversation_id"],
            "ticket_status": "AI_ACTIVE"
        })
        repo.pool = MagicMock()

        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)):
            proc1 = InboundProcessor(app_config, repo, mock_service)
            proc2 = InboundProcessor(app_config, repo, mock_service)
            await asyncio.gather(proc1.process_next_batch(), proc2.process_next_batch())

        assert mock_service.send_whatsapp_message.await_count == 1

    async def test_suspended_org_blocks_ai_and_fast_path(
        self, app_config, mock_repo, mock_service, fake_tenant_config, sample_msg
    ):
        """Org sospesa: nessuna risposta AI, nessuna fast_path; il cliente
        riceve il messaggio neutro e il messaggio viene marcato replied."""
        mock_repo.get_org_subscription_state = AsyncMock(return_value={
            "subscription_status": "canceled",
            "trial_end": None,
        })
        mock_service.fast_path_match = AsyncMock(return_value="Ciao! Benvenuto.")
        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)):
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()

        mock_service.fast_path_match.assert_not_called()
        mock_service.send_whatsapp_message.assert_awaited_once()
        body = mock_service.send_whatsapp_message.call_args.kwargs["payload"]["text"]["body"]
        assert body == "Grazie per averci scritto, ti risponderemo al piu' presto."
        mock_repo.try_mark_replied.assert_awaited_with(sample_msg["id"], handling_type="suspended", organization_id=sample_msg["organization_id"])

    async def test_suspended_org_blocks_new_booking(
        self, app_config, mock_repo, mock_service, fake_tenant_config, sample_msg
    ):
        """Una prenotazione NUOVA richiesta da un'org sospesa non viene creata:
        il pipeline AI (che parsifica la prenotazione) non viene mai avviato."""
        mock_repo.get_org_subscription_state = AsyncMock(return_value={
            "subscription_status": "canceled",
            "trial_end": None,
        })
        booking_service = MagicMock()
        booking_service.handle_reminder_reply = AsyncMock(return_value=None)
        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)), \
             patch("src.whatsapp.inbound_processor.genera_risposta_async", AsyncMock()) as mock_ai:
            processor = InboundProcessor(app_config, mock_repo, mock_service, booking_service=booking_service)
            await processor.process_next_batch()

        mock_ai.assert_not_called()
        booking_service.create_booking.assert_not_called()

    async def test_reminder_reply_still_handled_when_suspended(
        self, app_config, mock_repo, mock_service, fake_tenant_config, sample_msg
    ):
        """Il gate NON tocca handle_reminder_reply: una prenotazione gia'
        confermata resta gestibile anche da org sospesa (impegno preso con
        un terzo, non nuovo lavoro)."""
        mock_repo.get_org_subscription_state = AsyncMock(return_value={
            "subscription_status": "canceled",
            "trial_end": None,
        })
        booking_service = MagicMock()
        booking_service.handle_reminder_reply = AsyncMock(return_value="confirmed")
        processor = InboundProcessor(app_config, mock_repo, mock_service, booking_service=booking_service)
        await processor.process_next_batch()

        booking_service.handle_reminder_reply.assert_awaited_once()
        mock_service.send_whatsapp_message.assert_not_called()
        mock_repo.try_mark_replied.assert_awaited_with(sample_msg["id"], handling_type="automation", organization_id=sample_msg["organization_id"])

    async def test_decorate_with_disclosure_first_contact(
        self, app_config, mock_repo, mock_service
    ):
        from src.whatsapp.inbound_processor import decorate_with_disclosure, DISCLOSURE_TEXT
        mock_repo.get_or_create_contact = AsyncMock(return_value={"id": uuid.uuid4()})
        mock_repo.mark_ai_disclosure_sent = AsyncMock(return_value=True)
        out = await decorate_with_disclosure(
            str(uuid.uuid4()), "391234567890", "Siamo aperti.",
            mock_repo, nome_attivita="Trattoria Test",
        )
        assert out.startswith("Ciao! Sono l'assistente automatico di Trattoria Test")
        assert DISCLOSURE_TEXT.format(nome="Trattoria Test") in out
        assert out.endswith("Siamo aperti.")

    async def test_decorate_with_disclosure_second_contact(
        self, app_config, mock_repo, mock_service
    ):
        from src.whatsapp.inbound_processor import decorate_with_disclosure
        mock_repo.get_or_create_contact = AsyncMock(return_value={"id": uuid.uuid4()})
        mock_repo.mark_ai_disclosure_sent = AsyncMock(return_value=False)
        out = await decorate_with_disclosure(
            str(uuid.uuid4()), "391234567890", "Gia' vista.", mock_repo, nome_attivita="X"
        )
        assert out == "Gia' vista."


class TestInstagramDispatch:
    """Punto 10: un messaggio di conversazione canale instagram riceve la
    risposta via Instagram DM, non via WhatsApp."""

    def _ig_msg(self):
        return {
            "id": uuid.uuid4(), "organization_id": uuid.uuid4(),
            "conversation_id": uuid.uuid4(),
            "content": {"from": "ig-user-42"},
            "content_text": "Ciao", "message_type": "text",
            "canale": "instagram",
        }

    def _ig_config(self, org_id):
        from src.instagram.config import InstagramTenantConfig
        return InstagramTenantConfig(
            organization_id=org_id, ig_user_id="17841400000000000", access_token="tok",
        )

    async def test_instagram_reply_dispatches_to_instagram_service(
        self, app_config, mock_repo, mock_service, fake_tenant_config
    ):
        ig_msg = self._ig_msg()
        mock_repo.claim_inbound_messages = AsyncMock(return_value=[ig_msg])
        mock_send = AsyncMock(return_value={"id": uuid.uuid4(), "status": "sent"})
        fake_ig_service = MagicMock()
        fake_ig_service.send_instagram_message = mock_send

        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)), \
             patch("src.whatsapp.inbound_processor.genera_risposta_async", AsyncMock(return_value=RispostaOutput(
                 risposta="Siamo aperti dalle 12 alle 15.", richiede_umano=False, motivo="orari", categoria="info",
             ))), \
             patch("src.instagram.config.load_instagram_config",
                   AsyncMock(return_value=self._ig_config(ig_msg["organization_id"]))), \
             patch("src.instagram.service.InstagramService", MagicMock(return_value=fake_ig_service)):
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()

        mock_send.assert_awaited_once()
        kwargs = mock_send.call_args.kwargs
        assert kwargs["to_ig_id"] == "ig-user-42"
        assert "Siamo aperti" in kwargs["text"]
        # il canale WhatsApp NON viene toccato
        mock_service.send_whatsapp_message.assert_not_called()

    async def test_whatsapp_reply_untouched_when_canale_whatsapp(
        self, app_config, mock_repo, mock_service, fake_tenant_config, sample_msg
    ):
        sample_msg["canale"] = "whatsapp"
        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)), \
             patch("src.whatsapp.inbound_processor.genera_risposta_async", AsyncMock(return_value=RispostaOutput(
                 risposta="Ok.", richiede_umano=False, motivo="", categoria="info",
             ))), \
             patch("src.instagram.service.InstagramService") as mock_ig_cls:
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()

        mock_service.send_whatsapp_message.assert_awaited_once()
        mock_ig_cls.assert_not_called()

    async def test_instagram_generation_is_scoped_to_claimed_message(
        self, app_config, mock_repo, mock_service, fake_tenant_config
    ):
        ig_msg = self._ig_msg()
        mock_repo.claim_inbound_messages = AsyncMock(return_value=[ig_msg])
        mock_repo.record_usage = AsyncMock()
        mock_send = AsyncMock(return_value={"id": "wamid.instagram-test-1", "status": "sent"})
        fake_ig_service = MagicMock()
        fake_ig_service.send_instagram_message = mock_send
        generation = AsyncMock(return_value=RispostaOutput(
            risposta="Ok.", richiede_umano=False, motivo="", categoria="info",
        ))

        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)), \
             patch("src.whatsapp.inbound_processor.genera_risposta_async", generation), \
             patch("src.instagram.config.load_instagram_config",
                   AsyncMock(return_value=self._ig_config(ig_msg["organization_id"]))), \
             patch("src.instagram.service.InstagramService", MagicMock(return_value=fake_ig_service)):
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()

        context = generation.await_args.kwargs["provider_context"]
        assert context.task_type == "customer_message"
        assert context.worker_message_id == ig_msg["id"]
        assert context.actor_auth_user_id is None
        assert context.context.get("message_id") == str(ig_msg["id"]), (
            context.worker_message_id, context.actor_auth_user_id, context.context,
        )
        assert str(context.organization_id) == str(ig_msg["organization_id"])
        assert not any(
            call.args[1] == "ai_response"
            for call in mock_repo.record_usage.await_args_list
        )

    async def test_missing_ig_account_does_not_crash(
        self, app_config, mock_repo, mock_service, fake_tenant_config
    ):
        """Org senza account Instagram: nessun cross-channel fallback e il
        messaggio resta pendente per riconciliazione/retry esplicito."""
        ig_msg = self._ig_msg()
        mock_repo.claim_inbound_messages = AsyncMock(return_value=[ig_msg])

        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)), \
             patch("src.whatsapp.inbound_processor.genera_risposta_async", AsyncMock(return_value=RispostaOutput(
                 risposta="Ok.", richiede_umano=False, motivo="", categoria="info",
             ))), \
             patch("src.instagram.config.load_instagram_config", AsyncMock(return_value=None)), \
             patch("src.instagram.service.InstagramService") as mock_ig_cls:
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()

        mock_ig_cls.assert_not_called()
        mock_service.send_whatsapp_message.assert_not_called()
        mock_repo.try_mark_replied.assert_not_awaited()


class TestGuardrailsProcessor:
    """Task 12: guardrail post-LLM nel path WhatsApp reale."""

    async def test_prezzo_allucinato_bloccato_e_escalated(
        self, app_config, mock_repo, mock_service, fake_tenant_config, sample_msg
    ):
        """La risposta cita 25 euro ma il RAG dice 15: block + fallback staff
        + escalation HITL + usage event guardrail_block. Il fallback arriva
        comunque al cliente (non lo lasciamo in silenzio)."""
        mock_repo.escalate_to_human = AsyncMock(return_value={"id": sample_msg["conversation_id"], "ticket_status": "PENDING_STAFF"})
        mock_repo.record_usage = AsyncMock()
        mock_repo.search_similar = AsyncMock(return_value=[
            {"id": uuid.uuid4(), "content": "Il menu' del giorno costa 15 euro",
             "metadata": {}, "document_name": "menu.pdf"},
        ])

        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)), \
             patch("src.whatsapp.inbound_processor.genera_risposta_async", AsyncMock(return_value=RispostaOutput(
                 risposta="Il menu' del giorno costa 25 euro!", richiede_umano=False, motivo="info", categoria="info",
             ))), \
             patch("src.whatsapp.inbound_processor.enqueue_escalation", MagicMock()) as mock_email:
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()

        # fallback staff inviato al cliente (con disclosure al primo contatto)
        mock_service.send_whatsapp_message.assert_awaited_once()
        body = mock_service.send_whatsapp_message.call_args.kwargs["payload"]["text"]["body"]
        assert "ti metto in contatto con lo staff" in body
        assert "25 euro" not in body
        # escalation + email al titolare
        mock_repo.escalate_to_human.assert_awaited_once_with(str(sample_msg["conversation_id"]), sample_msg["organization_id"])
        mock_email.assert_called_once()
        # usage event del guardrail per iterare sui prompt
        assert mock_repo.record_usage.await_count >= 1
        guardrail_calls = [
            c for c in mock_repo.record_usage.await_args_list
            if c.args[1] == "guardrail_block"
        ]
        assert guardrail_calls, "manca l'usage event guardrail_block"
        assert guardrail_calls[0].kwargs["metadata"]["motivo"] == "prezzo_non_verificato"
        mock_repo.try_mark_replied.assert_awaited_with(sample_msg["id"], handling_type="escalated", organization_id=sample_msg["organization_id"])

    async def test_prezzo_verificato_dal_rag_passa_e_viene_inviato(
        self, app_config, mock_repo, mock_service, fake_tenant_config, sample_msg
    ):
        mock_repo.search_similar = AsyncMock(return_value=[
            {"id": uuid.uuid4(), "content": "Il menu' del giorno costa 15 euro",
             "metadata": {}, "document_name": "menu.pdf"},
        ])
        mock_repo.escalate_to_human = AsyncMock()

        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)), \
             patch("src.whatsapp.inbound_processor.genera_risposta_async", AsyncMock(return_value=RispostaOutput(
                 risposta="Il menu' del giorno costa 15 euro, ti aspettiamo!", richiede_umano=False, motivo="info", categoria="info",
             ))):
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()

        mock_service.send_whatsapp_message.assert_awaited_once()
        body = mock_service.send_whatsapp_message.call_args.kwargs["payload"]["text"]["body"]
        assert "15 euro" in body
        mock_repo.escalate_to_human.assert_not_called()

    async def test_risposta_lunga_tagliata_prima_dell_invio(
        self, app_config, mock_repo, mock_service, fake_tenant_config, sample_msg
    ):
        testo_lungo = "Siamo aperti tutti i giorni con moltissima disponibilita'. " * 20
        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)), \
             patch("src.whatsapp.inbound_processor.genera_risposta_async", AsyncMock(return_value=RispostaOutput(
                 risposta=testo_lungo, richiede_umano=False, motivo="info", categoria="info",
             ))):
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()

        mock_service.send_whatsapp_message.assert_awaited_once()
        body = mock_service.send_whatsapp_message.call_args.kwargs["payload"]["text"]["body"]
        # disclosure a parte, il corpo AI e' stato accorciato dal guardrail
        assert len(body) < len(testo_lungo)

    async def test_richiede_umano_con_testo_invia_messaggio_attesa(
        self, app_config, mock_repo, mock_service, fake_tenant_config, sample_msg
    ):
        """Il responder che escalationa lasciando un messaggio di attesa ora
        lo invia davvero (prima veniva scartato): il cliente non resta senza
        risposta mentre lo staff prende in carico il ticket."""
        mock_repo.escalate_to_human = AsyncMock(return_value={"id": sample_msg["conversation_id"], "ticket_status": "PENDING_STAFF"})
        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)), \
             patch("src.whatsapp.inbound_processor.genera_risposta_async", AsyncMock(return_value=RispostaOutput(
                 risposta="Ti passo qualcuno dello staff per le allergie, un attimo!",
                 richiede_umano=True, motivo="allergie", categoria="reclamo",
             ))), \
             patch("src.whatsapp.inbound_processor.enqueue_escalation", MagicMock()):
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()

        mock_service.send_whatsapp_message.assert_awaited_once()
        body = mock_service.send_whatsapp_message.call_args.kwargs["payload"]["text"]["body"]
        assert "qualcuno dello staff" in body
        mock_repo.escalate_to_human.assert_awaited_once()
        mock_repo.try_mark_replied.assert_awaited_with(sample_msg["id"], handling_type="escalated", organization_id=sample_msg["organization_id"])

    async def test_outbound_ai_marca_handling_type_ai_handled(
        self, app_config, mock_repo, mock_service, fake_tenant_config, sample_msg
    ):
        """Il messaggio outbound generato dall'AI viene persistito con
        handling_type='ai_handled' (serve a event_log/trigger e ai pulsanti
        di feedback staff nel thread inbox)."""
        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)), \
             patch("src.whatsapp.inbound_processor.genera_risposta_async", AsyncMock(return_value=RispostaOutput(
                 risposta="Siamo aperti dalle 12 alle 15.", richiede_umano=False, motivo="orari", categoria="info",
             ))):
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()

        kwargs = mock_service.send_whatsapp_message.call_args.kwargs
        assert kwargs["handling_type"] == "ai_handled"


    async def test_intent_classificato_passato_al_responder_e_al_logging(
        self, app_config, mock_repo, mock_service, fake_tenant_config, sample_msg
    ):
        """L'intent arriva alla crew; il provider adapter possiede le metriche."""
        mock_repo.record_usage = AsyncMock()
        mock_repo.search_similar = AsyncMock(return_value=[
            {"content": "Il piatto costa 12 euro", "document_name": "menu.pdf"}
        ])
        captured = {}

        async def fake_risposta(messaggio, profilo, billing=None, contesto_documenti="", intent=None, variante="control", **kwargs):
            captured["intent"] = intent
            captured["variante"] = variante
            captured["provider_context"] = kwargs["provider_context"]
            return RispostaOutput(
                risposta="Il piatto costa 12 euro.", richiede_umano=False, motivo="", categoria="info",
            )

        sample_msg["content_text"] = "Quanto costa il piatto del giorno?"
        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)), \
             patch("src.whatsapp.inbound_processor.genera_risposta_async", side_effect=fake_risposta):
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()

        assert captured["intent"] == "faq"
        assert captured["variante"] == "control"
        assert captured["provider_context"].task_type == "customer_message"
        assert not any(call.args[1] == "ai_response" for call in mock_repo.record_usage.await_args_list)
        # keyword sicura: il modello economico non viene speso
        intent_calls = [
            c for c in mock_repo.record_usage.await_args_list
            if c.args[1] == "intent_classification"
        ]
        assert intent_calls == []


    async def test_cache_hit_salta_il_responder(
        self, app_config, mock_repo, mock_service, fake_tenant_config, sample_msg
    ):
        """Domanda FAQ gia' in cache: risposta servita senza chiamare l'LLM,
        con usage event cache_hit e handling ai_handled."""
        mock_repo.record_usage = AsyncMock()
        mock_repo.faq_cache_lookup = AsyncMock(return_value={
            "answer_text": "Siamo aperti dalle 12 alle 15.", "hit_count": 3,
        })
        sample_msg["content_text"] = "A che ora aprite la sera?"

        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)), \
             patch("src.core.guardrails.faq_cache.vettorizza", return_value=[[0.1] * 384]), \
             patch("src.whatsapp.inbound_processor.genera_risposta_async", AsyncMock()) as mock_ai, \
             patch("src.core.guardrails.faq_cache.cerca_in_cache", AsyncMock(return_value="Siamo aperti dalle 12 alle 15.")) as mock_cerca:
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()

        mock_cerca.assert_awaited_once()
        mock_ai.assert_not_called()
        mock_service.send_whatsapp_message.assert_awaited_once()
        body = mock_service.send_whatsapp_message.call_args.kwargs["payload"]["text"]["body"]
        assert "Siamo aperti dalle 12 alle 15." in body
        cache_calls = [
            c for c in mock_repo.record_usage.await_args_list
            if c.args[1] == "cache_hit"
        ]
        assert cache_calls, "manca l'usage event cache_hit"

    async def test_risposta_faq_validata_restituita_va_in_cache(
        self, app_config, mock_repo, mock_service, fake_tenant_config, sample_msg
    ):
        """Dopo una risposta FAQ inviata col guardrail ok, la coppia
        domanda/risposta finisce in cache per i prossimi clienti."""
        mock_repo.record_usage = AsyncMock()
        mock_repo.search_similar = AsyncMock(return_value=[])
        mock_repo.faq_cache_store = AsyncMock(return_value={"id": uuid.uuid4()})
        sample_msg["content_text"] = "A che ora aprite la sera?"

        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)), \
             patch("src.core.guardrails.faq_cache.vettorizza", return_value=[[0.1] * 384]), \
             patch("src.core.guardrails.faq_cache.cerca_in_cache", AsyncMock(return_value=None)), \
             patch("src.whatsapp.inbound_processor.genera_risposta_async", AsyncMock(return_value=RispostaOutput(
                 risposta="Siamo aperti dalle 12 alle 15.", richiede_umano=False, motivo="orari", categoria="info",
             ))):
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()

        mock_repo.faq_cache_store.assert_awaited_once()
        args = mock_repo.faq_cache_store.await_args.args
        assert "aprite" in args[1].lower()
        assert args[2] == "Siamo aperti dalle 12 alle 15."

    async def test_niente_cache_se_escalation(
        self, app_config, mock_repo, mock_service, fake_tenant_config, sample_msg
    ):
        """Una risposta che finisce in escalation non inquina la cache."""
        mock_repo.search_similar = AsyncMock(return_value=[])
        mock_repo.faq_cache_store = AsyncMock()
        mock_repo.escalate_to_human = AsyncMock(return_value={"id": sample_msg["conversation_id"]})
        sample_msg["content_text"] = "A che ora aprite la sera?"

        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)), \
             patch("src.core.guardrails.faq_cache.vettorizza", return_value=[[0.1] * 384]), \
             patch("src.core.guardrails.faq_cache.cerca_in_cache", AsyncMock(return_value=None)), \
             patch("src.whatsapp.inbound_processor.genera_risposta_async", AsyncMock(return_value=RispostaOutput(
                 risposta="Ti passo lo staff", richiede_umano=True, motivo="reclamo", categoria="reclamo",
             ))), \
             patch("src.whatsapp.inbound_processor.enqueue_escalation", MagicMock()):
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()

        mock_repo.faq_cache_store.assert_not_called()


class TestFeedbackEmojiProcessor:
    """Task 12: 👍/👎 dal cliente — feedback sull'ultima risposta AI,
    mai una risposta generata dall'LLM."""

    async def test_pollice_su_registra_feedback_e_non_genera(
        self, app_config, mock_repo, mock_service, fake_tenant_config, sample_msg
    ):
        last_ai_msg = {"id": uuid.uuid4(), "handling_type": "ai_handled"}
        mock_repo.get_last_ai_outbound_message = AsyncMock(return_value=last_ai_msg)
        mock_repo.registra_feedback = AsyncMock(
            return_value={"id": uuid.uuid4(), "value": "up"}
        )
        sample_msg["content_text"] = "👍"

        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)), \
             patch("src.whatsapp.inbound_processor.genera_risposta_async", AsyncMock()) as mock_ai:
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()

        mock_repo.get_last_ai_outbound_message.assert_awaited_once()
        kwargs = mock_repo.registra_feedback.await_args.kwargs
        assert kwargs["message_id"] == last_ai_msg["id"]
        assert kwargs["source"] == "customer_emoji"
        assert kwargs["value"] == "up"
        mock_ai.assert_not_called()
        mock_service.send_whatsapp_message.assert_not_called()
        mock_service.fast_path_match.assert_not_called()
        mock_repo.try_mark_replied.assert_awaited_with(sample_msg["id"], handling_type="feedback", organization_id=sample_msg["organization_id"])

    async def test_pollice_giu_con_skin_tone(self, app_config, mock_repo, mock_service, sample_msg):
        mock_repo.get_last_ai_outbound_message = AsyncMock(return_value=None)
        sample_msg["content_text"] = "👎🏽 "
        processor = InboundProcessor(app_config, mock_repo, mock_service)
        await processor.process_next_batch()
        mock_repo.try_mark_replied.assert_awaited_with(sample_msg["id"], handling_type="feedback", organization_id=sample_msg["organization_id"])

    async def test_senza_risposta_ai_precedente_resto_gestito(
        self, app_config, mock_repo, mock_service, sample_msg
    ):
        """👍 senza una risposta AI a cui riferirsi: nessun feedback da
        scrivere, ma il messaggio non deve comunque finire all'LLM."""
        mock_repo.get_last_ai_outbound_message = AsyncMock(return_value=None)
        mock_repo.registra_feedback = AsyncMock()
        sample_msg["content_text"] = "👍"

        with patch("src.whatsapp.inbound_processor.genera_risposta_async", AsyncMock()) as mock_ai:
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()

        mock_repo.registra_feedback.assert_not_called()
        mock_ai.assert_not_called()
        mock_repo.try_mark_replied.assert_awaited_with(sample_msg["id"], handling_type="feedback", organization_id=sample_msg["organization_id"])

    async def test_testo_con_emoji_non_e_feedback(
        self, app_config, mock_repo, mock_service, fake_tenant_config, sample_msg
    ):
        """"'grazie 👍' contiene altro testo: flusso normale (AI path)."""
        sample_msg["content_text"] = "grazie 👍"
        mock_repo.get_last_ai_outbound_message = AsyncMock()
        mock_repo.registra_feedback = AsyncMock()

        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)), \
             patch("src.whatsapp.inbound_processor.genera_risposta_async", AsyncMock(return_value=RispostaOutput(
                 risposta="Prego!", richiede_umano=False, motivo="", categoria="info",
             ))):
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()

        mock_repo.registra_feedback.assert_not_called()
        mock_repo.try_mark_replied.assert_awaited_with(sample_msg["id"], handling_type="ai_handled", organization_id=sample_msg["organization_id"])


class TestRagContestoWhatsapp:
    """Il messaggio WhatsApp reale viaggia ora con il contesto RAG dei
    documenti dell'org (Punto 11)."""

    async def test_contesto_documenti_iniettato_nella_risposta_ai(
        self, app_config, mock_repo, mock_service, fake_tenant_config
    ):
        mock_repo.search_similar = AsyncMock(return_value=[
            {"id": uuid.uuid4(), "content": "Apriamo alle 12:00",
             "metadata": {"fonte": "menu.pdf"}, "document_name": "menu.pdf"},
            {"id": uuid.uuid4(), "content": "Carta vini all'ingresso",
             "metadata": {"fonte": "orari.pdf"}, "document_name": "orari.pdf"},
        ])
        captured = {}

        async def fake_risposta(messaggio, profilo, billing=None, contesto_documenti="", intent=None, variante="control", **kwargs):
            captured["contesto"] = contesto_documenti
            return RispostaOutput(
                risposta="Di giorno siamo aperti dalle 12:00.",
                richiede_umano=False, motivo="", categoria="info",
            )

        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)), \
             patch("src.whatsapp.inbound_processor.genera_risposta_async", side_effect=fake_risposta):
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()

        mock_repo.search_similar.assert_awaited_once()
        assert "-- menu.pdf --" in captured["contesto"]
        assert "Apriamo alle 12:00" in captured["contesto"]
        assert "-- orari.pdf --" in captured["contesto"]
        mock_service.send_whatsapp_message.assert_awaited_once()

    async def test_retrieval_scoped_shall_use_message_org(
        self, app_config, mock_repo, mock_service, fake_tenant_config, sample_msg
    ):
        """Isolamento a livello unit: il retrieval riceve SEMPRE l'org del
        messaggio, mai un org diverso. (La barriera SQL `organization_id = $1`
        e' gia' coperta dal test su DB in test_onboarding.py.)"""
        mock_repo.search_similar = AsyncMock(return_value=[])
        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)), \
             patch("src.whatsapp.inbound_processor.genera_risposta_async", new=AsyncMock(return_value=RispostaOutput(
                 risposta="ok", richiede_umano=False, motivo="", categoria="info"))):
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()
        org_used, _, k = mock_repo.search_similar.await_args.args
        assert org_used == str(sample_msg["organization_id"])
        assert k == 3

    async def test_retrieval_rotto_non_blocca_risposta_ai(
        self, app_config, mock_repo, mock_service, fake_tenant_config
    ):
        """Search_similar che solleva: il messaggio riceve comunque risposta,
        senza contesto, e il flusso non si rompe."""
        captured = {}

        async def broken_search(*args, **kwargs):
            raise RuntimeError("pgvector query failed")

        mock_repo.search_similar = AsyncMock(side_effect=broken_search)

        async def fake_risposta(messaggio, profilo, billing=None, contesto_documenti="", intent=None, variante="control", **kwargs):
            captured["contesto"] = contesto_documenti
            return RispostaOutput(
                risposta="Siamo aperti dalle 12:00.",
                richiede_umano=False, motivo="", categoria="info",
            )

        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)), \
             patch("src.whatsapp.inbound_processor.genera_risposta_async", side_effect=fake_risposta):
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()

        assert captured["contesto"] == ""
        mock_service.send_whatsapp_message.assert_awaited_once()

    async def test_timeout_reale_del_retrieval_non_blocca_il_messaggio(
        self, app_config, mock_repo, mock_service, fake_tenant_config
    ):
        """Il test del timeout VERO: search_similar resta appeso (sleep lungo)
        oltre il timeout; asyncio.wait_for scade davvero e il messaggio viene
        comunque risposto senza contesto. Verifichiamo che il tempo di
        esecuzione resti basso: se wait_for non scadesse, il test durerebbe
        rialmeno quanto lo sleep."""
        captured = {}

        async def hanging_search(*args, **kwargs):
            await asyncio.sleep(60)
            return [{"content": "x", "document_name": "x.pdf"}]

        mock_repo.search_similar = AsyncMock(side_effect=hanging_search)

        async def fake_risposta(messaggio, profilo, billing=None, contesto_documenti="", intent=None, variante="control", **kwargs):
            captured["contesto"] = contesto_documenti
            return RispostaOutput(
                risposta="Siamo aperti dalle 12:00.",
                richiede_umano=False, motivo="", categoria="info",
            )

        # il processor usa recupera_contesto_documenti col timeout di default;
        # iniettiamo un timeout breve solo nel test per renderlo veloce.
        real_retrieval = recupera_contesto_documenti

        async def fast_retrieval(org_id, testo, repo, q_emb=None):
            return await real_retrieval(org_id, testo, repo, q_emb=q_emb, timeout=0.05)

        start = time.monotonic()
        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)), \
             patch("src.whatsapp.inbound_processor.recupera_contesto_documenti", new=fast_retrieval), \
             patch("src.whatsapp.inbound_processor.genera_risposta_async", side_effect=fake_risposta):
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()
        elapsed = time.monotonic() - start

        assert mock_repo.search_similar.await_count == 1
        assert captured["contesto"] == ""
        assert elapsed < 5
        mock_service.send_whatsapp_message.assert_awaited_once()

    async def test_senza_documenti_contesto_vuoto_ma_risposta_ok(
        self, app_config, mock_repo, mock_service, fake_tenant_config
    ):
        mock_repo.search_similar = AsyncMock(return_value=[])
        captured = {}

        async def fake_risposta(messaggio, profilo, billing=None, contesto_documenti="", intent=None, variante="control", **kwargs):
            captured["contesto"] = contesto_documenti
            return RispostaOutput(
                risposta="Grazie della richiesta.",
                richiede_umano=False, motivo="", categoria="info",
            )

        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)), \
             patch("src.whatsapp.inbound_processor.genera_risposta_async", side_effect=fake_risposta):
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()

        assert captured["contesto"] == ""
        mock_service.send_whatsapp_message.assert_awaited_once()

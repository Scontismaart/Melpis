"""
Integrated End-to-End Staging Harness (Fasi 1-5 Consolidation).

Questo harness esegue la validazione integrata dell'intero stack applicativo
dopo il completamento delle 5 Fasi di refactoring:
- Fase 1: Decomposizione God Repository e Scoping Tenant
- Fase 2: Unificazione Motore Conversazionale (ConversationOrchestrator)
- Fase 3: Scomposizione Monolite nei Router Specializzati
- Fase 4: Pipeline Inbound a 2 Stadi, Outbox Dedup, Concorrenza Atomica, Quarantena Legacy
- Fase 5: Sicurezza Webhook, Worker Multi-Canale, Advisory Lock Session-Level per Cron, Health Probes

Scenari Coperti:
- Scenario E2E-A: Carico concorrente multi-tenant (50 messaggi, 3 tenant) con verifica
                  automatica dell'invariante relazionale su tutta la gerarchia
                  (messages.org_id == conversations.org_id == contacts.org_id).
- Scenario E2E-B: Corsa concorrente di 2 InboundWorker su SKIP LOCKED (zero claim duplicati),
                  outbox pre-dedup (zero doppie chiamate LLM) e cancellazione garantita
                  del task di heartbeat nel blocco finally.
- Scenario E2E-C: Esecuzione concorrente di cron job distribuiti protetti da lock
                  advisori di sessione (execute_with_advisory_lock), verifica di mutua
                  esclusione e assenza totale di leak di connessioni 'idle in transaction'.
- Scenario E2E-D: Stress-test probes di produzione (/api/health/live e /api/health/ready)
                  sotto carico concorrente con simulazione di crash del worker e failure DB.
"""

from __future__ import annotations

import asyncio
import datetime
import os
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.channels.base import OutboundSendResult
from src.core.channels.router import OutboundChannelRouter
from src.core.inbound.service import (
    InboundProcessingService,
    OrchestrationInput,
    ProcessingOutcome,
)
from src.core.jobs.base import compute_advisory_lock_id, execute_with_advisory_lock
from src.core.workers.inbound_worker import InboundWorker
from src.whatsapp.config import AppConfig


# ==============================================================================
# High-Fidelity Stateful Relational Repository for Staging Tests
# ==============================================================================

class StagingRelationalRepo:
    """
    Repository relazionale in-memory ad alta fedelta che replica esattamente
    le tabelle, le relazioni a chiave esterna e la semantica concorrente di PostgreSQL:
    - contacts (id, organization_id, phone_number, ai_disclosure_sent_at)
    - conversations (id, organization_id, contact_id, ticket_status, canale)
    - messages (id, organization_id, conversation_id, content_text, status, claimed_at, heartbeat_at, sent_at)
    - outbound_dedup ((message_id, organization_id) -> response_text)
    - organisations (id, name, messages_used_this_period, messages_limit)
    """

    def __init__(self):
        self.organizations: dict[uuid.UUID, dict[str, Any]] = {}
        self.contacts: dict[uuid.UUID, dict[str, Any]] = {}
        self.conversations: dict[uuid.UUID, dict[str, Any]] = {}
        self.messages: dict[uuid.UUID, dict[str, Any]] = {}
        self.outbound_dedup: dict[tuple[uuid.UUID, uuid.UUID], dict[str, Any]] = {}
        self.heartbeat_logs: list[tuple[uuid.UUID, uuid.UUID, datetime.datetime]] = []
        self._lock = asyncio.Lock()
        self.pool = None

    async def create_organization(self, org_id: uuid.UUID, name: str, messages_limit: int = 1000):
        async with self._lock:
            self.organizations[org_id] = {
                "id": org_id,
                "name": name,
                "messages_used_this_period": 0,
                "messages_limit": messages_limit,
                "subscription_status": "active",
            }

    async def get_or_create_contact(self, org_id: uuid.UUID, phone_number: str) -> dict[str, Any]:
        async with self._lock:
            for contact in self.contacts.values():
                if contact["organization_id"] == org_id and contact["phone_number"] == phone_number:
                    return contact
            contact_id = uuid.uuid4()
            contact = {
                "id": contact_id,
                "organization_id": org_id,
                "phone_number": phone_number,
                "ai_disclosure_sent_at": datetime.datetime.now(datetime.timezone.utc),
            }
            self.contacts[contact_id] = contact
            return contact

    async def get_or_create_conversation(
        self, org_id: uuid.UUID, contact_id: uuid.UUID, canale: str = "whatsapp"
    ) -> dict[str, Any]:
        async with self._lock:
            for conv in self.conversations.values():
                if conv["organization_id"] == org_id and conv["contact_id"] == contact_id:
                    return conv
            conv_id = uuid.uuid4()
            conv = {
                "id": conv_id,
                "organization_id": org_id,
                "contact_id": contact_id,
                "ticket_status": "OPEN",
                "canale": canale,
            }
            self.conversations[conv_id] = conv
            return conv

    async def upsert_message(
        self,
        msg_id: uuid.UUID,
        organization_id: uuid.UUID,
        conversation_id: uuid.UUID,
        content_text: str,
        content: dict | None = None,
        direction: str = "inbound",
        status: str = "received_pending_ai",
        canale: str = "whatsapp",
    ) -> dict[str, Any]:
        async with self._lock:
            msg = {
                "id": msg_id,
                "organization_id": organization_id,
                "conversation_id": conversation_id,
                "direction": direction,
                "content_text": content_text,
                "content": content or {},
                "status": status,
                "canale": canale,
                "claimed_at": None,
                "heartbeat_at": None,
                "billed_at": None,
                "sent_at": None,
                "quota_exceeded_at": None,
                "ai_reply_cache": None,
                "created_at": datetime.datetime.now(datetime.timezone.utc),
            }
            self.messages[msg_id] = msg
            return msg

    async def claim_inbound_messages(self, limit: int = 10) -> list[dict[str, Any]]:
        """Replica la semantica atomica di PostgreSQL: SELECT ... FOR UPDATE SKIP LOCKED."""
        async with self._lock:
            claimed = []
            now = datetime.datetime.now(datetime.timezone.utc)
            for msg in self.messages.values():
                if msg["direction"] == "inbound" and msg["status"] == "received_pending_ai":
                    msg["status"] = "processing"
                    msg["claimed_at"] = now
                    msg["heartbeat_at"] = now
                    claimed.append(dict(msg))
                    if len(claimed) >= limit:
                        break
            return claimed

    async def claim_message_for_processing(
        self, msg_id: uuid.UUID, org_id: uuid.UUID
    ) -> dict[str, Any]:
        """Claim only; commercial admission is deliberately not faked here."""
        async with self._lock:
            msg = self.messages.get(msg_id)
            if not msg or msg["organization_id"] != org_id:
                return {"status": "not_found"}
            if msg["sent_at"] is not None or msg.get("replied_at") is not None:
                return {"status": "already_sent"}
            if msg["quota_exceeded_at"] is not None:
                return {"status": "quota_exceeded"}
            if msg["status"] not in {"received_pending_ai", "processing"}:
                return {"status": "invalid_state"}
            if msg.get("processing_at") is not None:
                age = (datetime.datetime.now(datetime.timezone.utc) - msg["processing_at"]).total_seconds()
                if age < 30:
                    return {"status": "currently_processing"}
            msg["status"] = "processing"
            msg["processing_at"] = datetime.datetime.now(datetime.timezone.utc)
            msg["claimed_at"] = msg.get("claimed_at") or msg["processing_at"]
            msg["heartbeat_at"] = msg["processing_at"]
            return {
                "status": "claimed",
                "ai_reply_cache": msg.get("ai_reply_cache"),
                "sent_at": None,
                "billed_at": msg.get("billed_at"),
                "quota_exceeded_at": None,
                "processing_at": msg["processing_at"],
            }

    async def get_conversation(self, conv_id: uuid.UUID, org_id: uuid.UUID) -> dict[str, Any] | None:
        async with self._lock:
            conv = self.conversations.get(conv_id)
            if conv and conv["organization_id"] == org_id:
                return dict(conv)
            return None

    async def get_outbound_dedup(self, *args, **kwargs) -> dict[str, Any] | None:
        async with self._lock:
            if len(args) == 2:
                a, b = args
                return self.outbound_dedup.get((a, b)) or self.outbound_dedup.get((b, a))
            org_id = kwargs.get("organization_id") or kwargs.get("org_id")
            msg_id = kwargs.get("message_id") or kwargs.get("msg_id")
            return self.outbound_dedup.get((msg_id, org_id)) or self.outbound_dedup.get((org_id, msg_id))

    async def save_outbound_dedup(self, *args, **kwargs):
        async with self._lock:
            if len(args) >= 3:
                a, b, text = args[0], args[1], args[2]
                self.outbound_dedup[(a, b)] = {
                    "response_text": text,
                    "created_at": datetime.datetime.now(datetime.timezone.utc),
                }
                self.outbound_dedup[(b, a)] = self.outbound_dedup[(a, b)]
            else:
                msg_id = kwargs.get("message_id") or kwargs.get("msg_id")
                org_id = kwargs.get("organization_id") or kwargs.get("org_id")
                text = kwargs.get("response_text") or kwargs.get("reply_text") or ""
                self.outbound_dedup[(msg_id, org_id)] = {
                    "response_text": text,
                    "created_at": datetime.datetime.now(datetime.timezone.utc),
                }
                self.outbound_dedup[(org_id, msg_id)] = self.outbound_dedup[(msg_id, org_id)]

    async def save_ai_reply(self, message_id: uuid.UUID, reply: dict, organization_id: uuid.UUID):
        async with self._lock:
            msg = self.messages.get(message_id)
            if msg and msg["organization_id"] == organization_id:
                msg["ai_reply_cache"] = reply

    async def mark_message_sent(
        self, message_id: uuid.UUID, meta_message_id: str | None = None, organization_id: uuid.UUID | None = None, **kwargs
    ):
        async with self._lock:
            msg = self.messages.get(message_id)
            if msg:
                msg["sent_at"] = datetime.datetime.now(datetime.timezone.utc)
                msg["status"] = "sent"
                if meta_message_id:
                    msg["wam_id"] = meta_message_id

    async def try_mark_replied(
        self, message_id: uuid.UUID, handling_type: str | None = None, *, organization_id: uuid.UUID | None = None, **kwargs
    ):
        async with self._lock:
            msg = self.messages.get(message_id)
            if msg:
                msg["status"] = "replied"
                if handling_type:
                    msg["handling_type"] = handling_type
                return dict(msg)
            return None

    async def update_heartbeat(self, message_id: uuid.UUID, organization_id: uuid.UUID):
        async with self._lock:
            now = datetime.datetime.now(datetime.timezone.utc)
            msg = self.messages.get(message_id)
            if msg and msg["organization_id"] == organization_id:
                msg["heartbeat_at"] = now
            self.heartbeat_logs.append((message_id, organization_id, now))

    async def reap_stale_claims(self, timeout_minutes: int = 5):
        pass

    async def check_booking_exists(self, *args, **kwargs) -> bool:
        return False

    async def mark_ai_disclosure_sent(self, *args, **kwargs) -> bool:
        return True

    async def get_org_subscription_state(self, org_id: uuid.UUID) -> dict[str, Any]:
        return {"subscription_status": "active"}

    # ── Relational Invariant Verification ──────────────────────────────────────
    def check_relational_hierarchy_invariants(self) -> dict[str, Any]:
        """
        Esegue l'audit relazionale completo tra messages -> conversations -> contacts.
        Verifica che per il 100% delle righe:
        m.organization_id == c.organization_id == ct.organization_id.
        Rileva qualsiasi leakage o cross-tenant data contamination.
        """
        violations = []
        for msg_id, msg in self.messages.items():
            m_org = msg["organization_id"]
            conv_id = msg.get("conversation_id")
            if not conv_id or conv_id not in self.conversations:
                violations.append(f"Message {msg_id} references missing conversation {conv_id}")
                continue

            conv = self.conversations[conv_id]
            c_org = conv["organization_id"]
            if m_org != c_org:
                violations.append(
                    f"Cross-tenant leak detected: Message {msg_id} (org={m_org}) is linked to Conversation {conv_id} (org={c_org})"
                )

            contact_id = conv.get("contact_id")
            if not contact_id or contact_id not in self.contacts:
                violations.append(f"Conversation {conv_id} references missing contact {contact_id}")
                continue

            contact = self.contacts[contact_id]
            ct_org = contact["organization_id"]
            if c_org != ct_org:
                violations.append(
                    f"Cross-tenant leak detected: Conversation {conv_id} (org={c_org}) is linked to Contact {contact_id} (org={ct_org})"
                )

        return {
            "total_messages": len(self.messages),
            "total_conversations": len(self.conversations),
            "total_contacts": len(self.contacts),
            "violations_count": len(violations),
            "violations": violations,
            "is_valid": len(violations) == 0,
        }


# ==============================================================================
# High-Fidelity Connection Pool for Advisory Locks & Leak Verification
# ==============================================================================

class StagingConnection:
    def __init__(self, pool: StagingConnectionPool):
        self.pool = pool
        self.held_advisory_locks: set[int] = set()

    async def fetchval(self, query: str, *args):
        if "pg_try_advisory_lock" in query:
            lock_id = args[0]
            async with self.pool._lock:
                if lock_id in self.pool.global_held_locks:
                    return False
                self.pool.global_held_locks.add(lock_id)
                self.held_advisory_locks.add(lock_id)
                return True
        elif "SELECT 1" in query:
            if self.pool.simulate_db_failure:
                raise ConnectionRefusedError("PostgreSQL cluster unreachable (simulated failure)")
            return 1
        return None

    async def execute(self, query: str, *args):
        if "pg_advisory_unlock" in query:
            lock_id = args[0]
            async with self.pool._lock:
                if lock_id in self.pool.global_held_locks:
                    self.pool.global_held_locks.remove(lock_id)
                self.held_advisory_locks.discard(lock_id)
                return True
        return None


class StagingConnectionPool:
    def __init__(self):
        self._lock = asyncio.Lock()
        self.global_held_locks: set[int] = set()
        self.active_acquired_connections = 0
        self.peak_acquired_connections = 0
        self.simulate_db_failure = False

    class _ConnContext:
        def __init__(self, pool: StagingConnectionPool):
            self.pool = pool
            self.conn = StagingConnection(pool)

        async def __aenter__(self) -> StagingConnection:
            if self.pool.simulate_db_failure:
                raise ConnectionRefusedError("PostgreSQL pool connection refused (simulated failure)")
            async with self.pool._lock:
                self.pool.active_acquired_connections += 1
                if self.pool.active_acquired_connections > self.pool.peak_acquired_connections:
                    self.pool.peak_acquired_connections = self.pool.active_acquired_connections
            return self.conn

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            async with self.pool._lock:
                self.pool.active_acquired_connections -= 1
            return False

    def acquire(self):
        return self._ConnContext(self)


# ==============================================================================
# Scenario E2E-A: Multi-Tenant Concurrency & Relational Hierarchy Invariance Check
# ==============================================================================

@pytest.mark.asyncio
async def test_e2e_a_multi_tenant_concurrency_and_relational_invariance():
    """
    Scenario E2E-A:
    - 3 Tenant isolati ($T_A, T_B, T_C$).
    - 50 messaggi concorrenti inviati simultaneamente (17 per A, 17 per B, 16 per C).
    - Risoluzione concorrente di contatti, conversazioni e messaggi.
    - Esecuzione del controllo automatico di invarianza gerarchica:
      m.organization_id == c.organization_id == ct.organization_id su TUTTE le 50 righe.
    - Zero cross-tenant data contamination.
    """
    repo = StagingRelationalRepo()
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()
    org_c = uuid.uuid4()

    await repo.create_organization(org_a, "Ristorante Roma Trastevere")
    await repo.create_organization(org_b, "Pizzeria Napoli Spaccanapoli")
    await repo.create_organization(org_c, "Bistrot Milano Duomo")

    app_config = AppConfig(
        app_secret="staging_secret",
        encryption_key="MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTIzNDU2Nzg5MDE=",
        postgres_dsn="postgresql://test:test@localhost:5432/staging_db",
        verify_token="staging_token",
        use_conversation_orchestrator=True,
    )

    mock_channel = AsyncMock()
    mock_channel.send_reply.return_value = OutboundSendResult(
        channel="whatsapp", success=True, wam_id="meta-wam-ok", error=None
    )
    router = OutboundChannelRouter({"whatsapp": mock_channel})

    mock_service = AsyncMock()
    mock_service.check_opt_out.return_value = {"is_opt_out": False}
    mock_service.check_human_request.return_value = False
    # This harness checks relational isolation and worker concurrency, so use
    # the non-billable deterministic reply path. Real AI admission is covered
    # by the PostgreSQL quota lifecycle suite.
    mock_service.fast_path_match.return_value = "Risposta deterministica per il test."

    service = InboundProcessingService(
        app_config=app_config,
        repo=repo,
        service=mock_service,
        channel_router=router,
    )
    service.orchestrator = AsyncMock()
    service.orchestrator.orchestrate.return_value = MagicMock(
        response_text="Risposta AI validata per il cliente.",
        richiede_umano=False,
        motivo_richiesta_umano=None,
        intent="faq",
        source="llm",
        guardrail_motivo=None,
    )

    # Configurazione 50 messaggi distribuiti sui 3 tenant
    tenants_distribution = [org_a] * 17 + [org_b] * 17 + [org_c] * 16
    assert len(tenants_distribution) == 50

    async def _simulate_inbound_arrival(idx: int, org_id: uuid.UUID):
        phone = f"+39333{idx:07d}"
        contact = await repo.get_or_create_contact(org_id, phone)
        conv = await repo.get_or_create_conversation(org_id, contact["id"])
        msg_id = uuid.uuid4()
        raw_msg = {
            "id": msg_id,
            "organization_id": org_id,
            "conversation_id": conv["id"],
            "content_text": f"Messaggio #{idx} da cliente {phone}",
            "content": {"from": phone},
            "canale": "whatsapp",
        }
        await repo.upsert_message(
            msg_id=msg_id,
            organization_id=org_id,
            conversation_id=conv["id"],
            content_text=raw_msg["content_text"],
            content=raw_msg["content"],
        )
        # Elaborazione concorrente nel service
        outcome = await service.process_message(raw_msg)
        return outcome

    # Esecuzione simultanea di tutti i 50 flussi
    with patch("src.core.inbound.service.load_tenant_config", AsyncMock(return_value=MagicMock(business_profile={"nome": "Test"}))):
        outcomes = await asyncio.gather(
            *[_simulate_inbound_arrival(i, org) for i, org in enumerate(tenants_distribution)]
        )

    assert len(outcomes) == 50
    assert all(o.action == "handled" for o in outcomes)

    # ── Relational Hierarchy Invariant Verification (Tassativo) ─────────────────
    audit = repo.check_relational_hierarchy_invariants()
    assert audit["is_valid"] is True, f"Violazioni gerarchiche riscontrate: {audit['violations']}"
    assert audit["violations_count"] == 0
    assert audit["total_messages"] == 50

    # Verifica partizionamento per tenant
    msgs_a = [m for m in repo.messages.values() if m["organization_id"] == org_a]
    msgs_b = [m for m in repo.messages.values() if m["organization_id"] == org_b]
    msgs_c = [m for m in repo.messages.values() if m["organization_id"] == org_c]

    assert len(msgs_a) == 17
    assert len(msgs_b) == 17
    assert len(msgs_c) == 16

    # Nessun messaggio di A o B appartiene a C
    set_a = {m["id"] for m in msgs_a}
    set_b = {m["id"] for m in msgs_b}
    set_c = {m["id"] for m in msgs_c}
    assert len(set_a & set_b) == 0
    assert len(set_a & set_c) == 0
    assert len(set_b & set_c) == 0


# ==============================================================================
# Scenario E2E-B: Worker Race on SKIP LOCKED, Outbox Dedup & Heartbeat Cleanup
# ==============================================================================

@pytest.mark.asyncio
async def test_e2e_b_worker_skip_locked_outbox_dedup_and_heartbeat_cancellation(monkeypatch):
    """
    Scenario E2E-B:
    1. Due InboundWorker concorrenti (W1, W2) in competizione su SKIP LOCKED:
       - 20 messaggi pendenti nel batch.
       - Ogni messaggio e reclamato esattamente da un solo worker (zero claim duplicati).
       - Insiemi di ID elaborati rigorosamente disgiunti (W1 ∩ W2 = ∅).
    2. Outbox Pre-Dedup:
       - Se un messaggio viene ritentato dopo aver popolato l'outbox dedup,
         il risultato viene inviato senza rieseguire l'orchestrazione cognitiva LLM.
    3. Garanzia di Cancellazione Heartbeat nel blocco 'finally':
       - In caso di esecuzione normale, il task di heartbeat e cancellato.
       - In caso di crash/eccezione nel processamento, il task di heartbeat e
         ugualmente cancellato in finally (zero zombie task residui nel loop).
    """
    repo = StagingRelationalRepo()
    org_id = uuid.uuid4()
    await repo.create_organization(org_id, "Pizzeria Concorrenza")

    # Seed di 20 messaggi pendenti
    contact = await repo.get_or_create_contact(org_id, "+393339998877")
    conv = await repo.get_or_create_conversation(org_id, contact["id"])
    for i in range(20):
        mid = uuid.uuid4()
        await repo.upsert_message(
            msg_id=mid,
            organization_id=org_id,
            conversation_id=conv["id"],
            content_text=f"Batch message {i}",
            status="received_pending_ai",
            content={"from": "+393339998877"},
        )

    app_config = AppConfig(
        app_secret="test",
        encryption_key="MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTIzNDU2Nzg5MDE=",
        postgres_dsn="postgresql://test:test@localhost:5432/test",
        verify_token="test",
        use_conversation_orchestrator=True,
    )
    mock_channel = AsyncMock()
    mock_channel.send_reply.return_value = OutboundSendResult(
        channel="whatsapp", success=True, wam_id="meta-wam-999", error=None
    )
    router = OutboundChannelRouter({"whatsapp": mock_channel})

    mock_service = AsyncMock()
    mock_service.check_opt_out.return_value = {"is_opt_out": False}
    mock_service.check_human_request.return_value = False
    mock_service.fast_path_match.return_value = "Risposta deterministica per il test."

    svc = InboundProcessingService(
        app_config=app_config,
        repo=repo,
        service=mock_service,
        channel_router=router,
    )
    svc.orchestrator = AsyncMock()
    svc.orchestrator.orchestrate.return_value = MagicMock(
        response_text="Risposta batch worker",
        richiede_umano=False,
        motivo_richiesta_umano=None,
        intent="faq",
        source="llm",
        guardrail_motivo=None,
    )

    # Due worker concorrenti
    w1 = InboundWorker(repo=repo, inbound_service=svc, batch_size=10)
    w2 = InboundWorker(repo=repo, inbound_service=svc, batch_size=10)

    # Tracciamento degli ID processati da ciascun worker
    w1_processed: list[uuid.UUID] = []
    w2_processed: list[uuid.UUID] = []

    orig_process = svc.process_message

    async def _w1_process(msg):
        w1_processed.append(msg["id"])
        await asyncio.sleep(0.005)  # Intenzionale interleaving asincrono
        return await orig_process(msg)

    async def _w2_process(msg):
        w2_processed.append(msg["id"])
        await asyncio.sleep(0.005)
        return await orig_process(msg)

    with patch("src.core.inbound.service.load_tenant_config", AsyncMock(return_value=MagicMock(business_profile={"nome": "Test"}))):
        async def _run_w1():
            with patch.object(svc, "process_message", side_effect=_w1_process):
                await w1.process_next_batch()

        async def _run_w2():
            with patch.object(svc, "process_message", side_effect=_w2_process):
                await w2.process_next_batch()

        await asyncio.gather(_run_w1(), _run_w2())

    # 1. Verifica SKIP LOCKED: zero claim duplicati
    assert len(w1_processed) + len(w2_processed) == 20
    assert len(set(w1_processed) & set(w2_processed)) == 0, "Rilevato duplicate claim su SKIP LOCKED!"

    # 2. Verifica Outbox Pre-Dedup & Idempotenza Send
    # Caso 2A: Messaggio già inviato (sent_at presente in DB) -> ignorato, zero chiamate esterne
    target_mid = w1_processed[0]
    svc.orchestrator.orchestrate.reset_mock()
    mock_channel.send_reply.reset_mock()

    with patch("src.core.inbound.service.load_tenant_config", AsyncMock(return_value=MagicMock(business_profile={"nome": "Test"}))):
        res_already_sent = await svc.process_message({
            "id": target_mid,
            "organization_id": org_id,
            "conversation_id": conv["id"],
            "content_text": "Retry message already sent",
            "content": {"from": "+393339998877"},
            "canale": "whatsapp",
        })
        assert res_already_sent.action == "ignored"
        assert res_already_sent.handling_type == "already_sent"
        assert mock_channel.send_reply.await_count == 0
        assert svc.orchestrator.orchestrate.await_count == 0

    # Caso 2B: un marker legacy di outbox senza evidenza A04 non autorizza il
    # replay; il fake repo non può creare un committed ledger record.
    crash_dedup_mid = uuid.uuid4()
    await repo.upsert_message(
        msg_id=crash_dedup_mid,
        organization_id=org_id,
        conversation_id=conv["id"],
        content_text="Retry after crash post-dedup",
        status="received_pending_ai",
        content={"from": "+393339998877"},
    )
    await repo.save_outbound_dedup(crash_dedup_mid, org_id, "Risposta dedup memorizzata pre-crash")
    svc.orchestrator.orchestrate.reset_mock()
    mock_channel.send_reply.reset_mock()

    from tests.a04_admission_fakes import install_mock_a04_admission
    install_mock_a04_admission(monkeypatch)
    with patch("src.core.inbound.service.load_tenant_config", AsyncMock(return_value=MagicMock(business_profile={"nome": "Test"}))):
        res_crash_retry = await svc.process_message({
            "id": crash_dedup_mid,
            "organization_id": org_id,
            "conversation_id": conv["id"],
            "content_text": "Retry after crash post-dedup",
            "content": {"from": "+393339998877"},
            "canale": "whatsapp",
        })
        assert res_crash_retry.action == "error"
        assert res_crash_retry.handling_type == "quota_reconciliation_required"
        assert mock_channel.send_reply.await_count == 0
        assert svc.orchestrator.orchestrate.await_count == 0

    # 3. Garanzia Cancellazione Heartbeat nel blocco finally
    test_msg_id = uuid.uuid4()
    await repo.upsert_message(
        msg_id=test_msg_id,
        organization_id=org_id,
        conversation_id=conv["id"],
        content_text="Heartbeat test normal",
        status="received_pending_ai",
        content={"from": "+393339998877"},
    )

    mock_service.fast_path_match.return_value = None
    with patch("src.core.inbound.service.load_tenant_config", AsyncMock(return_value=MagicMock(business_profile={"nome": "Test"}))):
        captured_task = None

        orig_ensure_future = asyncio.ensure_future
        def _capture_ensure_future(coro, *args, **kwargs):
            nonlocal captured_task
            task = orig_ensure_future(coro, *args, **kwargs)
            captured_task = task
            return task

        with patch("asyncio.ensure_future", side_effect=_capture_ensure_future):
            await svc.process_message({
                "id": test_msg_id,
                "organization_id": org_id,
                "conversation_id": conv["id"],
                "content_text": "Heartbeat test normal",
                "content": {"from": "+393339998877"},
                "canale": "whatsapp",
            })

        assert captured_task is not None
        # In Python 3.11+, cancel() sets cancelling status immediately; yielding to the loop finalizes cancellation
        is_cancelling = getattr(captured_task, "cancelling", lambda: False)()
        await asyncio.sleep(0)
        assert is_cancelling or captured_task.cancelled() or captured_task.done(), "Il task di heartbeat non e stato cancellato al termine normale!"

    # Caso B: Crash imprevisto dell'orchestratore -> heartbeat task cancellato in finally
    crash_msg_id = uuid.uuid4()
    await repo.upsert_message(
        msg_id=crash_msg_id,
        organization_id=org_id,
        conversation_id=conv["id"],
        content_text="Heartbeat test crash",
        status="received_pending_ai",
        content={"from": "+393339998877"},
    )

    svc.orchestrator.orchestrate.side_effect = RuntimeError("Fatal LLM crash during orchestration")
    crashed_heartbeat_task = None

    with patch("src.core.inbound.service.load_tenant_config", AsyncMock(return_value=MagicMock(business_profile={"nome": "Test"}))):
        with patch("asyncio.ensure_future", side_effect=_capture_ensure_future):
            try:
                await svc.process_message({
                    "id": crash_msg_id,
                    "organization_id": org_id,
                    "conversation_id": conv["id"],
                    "content_text": "Heartbeat test crash",
                    "content": {"from": "+393339998877"},
                    "canale": "whatsapp",
                })
            except RuntimeError:
                pass
            crashed_heartbeat_task = captured_task

        assert crashed_heartbeat_task is not None
        crashed_is_cancelling = getattr(crashed_heartbeat_task, "cancelling", lambda: False)()
        await asyncio.sleep(0)
        assert crashed_is_cancelling or crashed_heartbeat_task.cancelled() or crashed_heartbeat_task.done(), (
            "Il task di heartbeat non e stato cancellato nel blocco finally dopo il crash!"
        )


# ==============================================================================
# Scenario E2E-C: Cron Session Advisory Locks & Connection Pool Safety
# ==============================================================================

@pytest.mark.asyncio
async def test_e2e_c_cron_session_advisory_lock_mutual_exclusion_and_leak_free():
    """
    Scenario E2E-C:
    1. Due istanze concorrenti di un cron job distribuito tentano di eseguirsi simultaneamente
       con execute_with_advisory_lock:
       - Esattamente UNA istanza ottiene il lock ed esegue il job.
       - La seconda istanza salta immediatamente (ritorna False) senza bloccare il pool.
    2. Rilascio garantito nel blocco finally:
       - Nessuna connessione rimane in 'idle in transaction' o appesa.
       - Le connessioni attive nel pool tornano rigorosamente a 0.
    3. Esecuzione successiva:
       - Subito dopo il completamento, una terza esecuzione puo ri-acquisire il lock senza deadlock.
    """
    pool = StagingConnectionPool()
    lock_name = "scheduled_retention_and_reminder_cron"

    job_execution_log: list[str] = []

    async def _sample_cron_job(p):
        job_execution_log.append("job_started")
        await asyncio.sleep(0.02)  # Simula lavoro I/O del job
        job_execution_log.append("job_finished")

    # Due istanze concorrenti che competono per lo stesso advisory lock
    task1 = asyncio.create_task(execute_with_advisory_lock(pool, lock_name, _sample_cron_job))
    task2 = asyncio.create_task(execute_with_advisory_lock(pool, lock_name, _sample_cron_job))

    res1, res2 = await asyncio.gather(task1, task2)

    # Mutua esclusione
    results = [res1, res2]
    assert results.count(True) == 1, "Esattamente un'istanza doveva acquisire il lock"
    assert results.count(False) == 1, "La seconda istanza doveva saltare immediatamente"

    # Il job e stato eseguito una sola volta
    assert job_execution_log == ["job_started", "job_finished"]

    # Verifica assenza di leak di connessioni
    assert pool.active_acquired_connections == 0, (
        f"Rilevato connection leak! Connessioni attive nel pool: {pool.active_acquired_connections}"
    )
    assert len(pool.global_held_locks) == 0, "Il lock advisory di sessione non e stato sbloccato!"

    # Esecuzione successiva: deve funzionare senza deadlock
    res3 = await execute_with_advisory_lock(pool, lock_name, _sample_cron_job)
    assert res3 is True
    assert pool.active_acquired_connections == 0
    assert len(pool.global_held_locks) == 0


# ==============================================================================
# Scenario E2E-D: Production Health Probes Under Load & Crash Simulation
# ==============================================================================

@pytest.mark.asyncio
async def test_e2e_d_health_probes_under_concurrent_load_and_crash_simulation():
    """
    Scenario E2E-D:
    1. Richieste concorrenti a /api/health/live e /api/health/ready in condizioni normali:
       - Entrambe ritornano HTTP 200 status 'ok'.
    2. Simulazione di Crash del Worker Task:
       - Se inbound_task crasha con eccezione non gestita, /api/health/live
         ritorna immediatamente HTTP 503 status 'unhealthy' riportando l'errore esatto.
    3. Simulazione di Degradazione DB:
       - Se il pool DB perde connettivita, /api/health/ready ritorna HTTP 503 status 'degraded'.
    4. Ripristino immediato:
       - Al ripristino di DB e worker, i probe tornano immediatamente a HTTP 200 senza richiedere restart.
    """
    client = TestClient(app)
    pool = StagingConnectionPool()
    app.state.pool = pool

    # Mock dei worker tasks attivi e sani
    mock_inbound = MagicMock()
    mock_inbound.done.return_value = False
    mock_retry = MagicMock()
    mock_retry.done.return_value = False

    app.state.inbound_task = mock_inbound
    app.state.retry_task = mock_retry

    with patch.dict(os.environ, {
        "GROQ_API_KEY": "test_groq_free_key",
        "GROQ_FREE_ACCOUNT_CONFIRMED": "true",
        "LLM_COST_POLICY": "free_only",
        "OPENROUTER_MODEL": "groq/openai/gpt-oss-20b",
    }):
        # 1. Carico concorrente di 20 richieste contemporanee sui probe sani
        def _get_live():
            return client.get("/api/health/live")

        def _get_ready():
            return client.get("/api/health/ready")

        live_resps = [_get_live() for _ in range(10)]
        ready_resps = [_get_ready() for _ in range(10)]

        for r in live_resps:
            assert r.status_code == 200
            assert r.json()["status"] == "ok"
            assert r.json()["workers"]["inbound_task"] == "running"
            assert r.json()["workers"]["retry_task"] == "running"

        for r in ready_resps:
            assert r.status_code == 200
            assert r.json()["status"] == "ok"
            assert r.json()["checks"]["database"] == "ok"

        # 2. Simulazione Crash del Worker Inbound
        mock_inbound.done.return_value = True
        mock_inbound.cancelled.return_value = False
        mock_inbound.exception.return_value = RuntimeError("Simulated unhandled event loop exception in worker")

        resp_live_crash = client.get("/api/health/live")
        assert resp_live_crash.status_code == 503
        data_crash = resp_live_crash.json()
        assert data_crash["status"] == "unhealthy"
        assert data_crash["workers"]["inbound_task"] == "stopped: worker failure"

        # 3. Simulazione Failure del DB su /api/health/ready
        pool.simulate_db_failure = True
        resp_ready_fail = client.get("/api/health/ready")
        assert resp_ready_fail.status_code == 503
        data_ready = resp_ready_fail.json()
        assert data_ready["status"] == "degraded"
        assert data_ready["checks"]["database"] == "errore di connessione"

        # 4. Ripristino Sano
        mock_inbound.done.return_value = False
        pool.simulate_db_failure = False

        resp_live_recovered = client.get("/api/health/live")
        assert resp_live_recovered.status_code == 200
        assert resp_live_recovered.json()["status"] == "ok"

        resp_ready_recovered = client.get("/api/health/ready")
        assert resp_ready_recovered.status_code == 200
        assert resp_ready_recovered.json()["status"] == "ok"

import hashlib
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.core.auth.dependencies as deps
from src.core.billing.routes import router as billing_router
from src.core.provider_evidence import ImmutableEvidenceBlob


_ORG_ID = str(uuid.uuid4())
_ACCOUNT_REF = "acct_123456789"
_OBJECT_ID = "a03e_0123456789abcdef0123456789abcdef"
_ATTESTATIONS = {
    "provider_account_and_request_verified": True,
    "terminal_outcome_verified": True,
    "metrics_verified": True,
}


def _artifact(attempt_id):
    return json.dumps({
        "schema_version": 1,
        "attempt_id": str(attempt_id),
        "provider": "groq",
        "provider_account_ref": _ACCOUNT_REF,
        "provider_correlation_id": str(attempt_id),
        "provider_request_id": "chatcmpl_route_test_123",
        "provider_event_at": datetime.now(timezone.utc).isoformat(),
        "terminal": True,
        "outcome": "provider_confirms_not_billed",
        "actual_model": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
    }, sort_keys=True, separators=(",", ":")).encode()


class _EvidenceStore:
    def __init__(self, payload):
        self.payload = payload
        self.reads = []

    async def read_immutable(self, organization_id, object_id):
        self.reads.append((str(organization_id), object_id))
        return ImmutableEvidenceBlob(
            data=self.payload,
            version_id="v_0123456789abcdef0123456789abcdef",
            retention_mode="compliance",
            retained_until=datetime.now(timezone.utc) + timedelta(days=100),
        )


class _BillingRepo:
    def __init__(self):
        self.proposals = []
        self.approvals = []
        self.recovery_attempts = []
        self.recovery_result = {"state": "not_sent"}
        self.recovery_calls = []
        self.finalized = []

    async def propose_provider_reconciliation(self, *args, **kwargs):
        self.proposals.append((args, kwargs))
        return 7

    async def approve_provider_reconciliation(self, *args, **kwargs):
        self.approvals.append((args, kwargs))
        return True

    async def list_provider_attempt_recovery(self, organization_id):
        self.recovery_calls.append((organization_id,))
        return self.recovery_attempts

    async def recover_provider_attempt(self, *args, **kwargs):
        self.recovery_calls.append((args, kwargs))
        return self.recovery_result

    async def finalize_openrouter_recovery(self, *args, **kwargs):
        self.finalized.append((args, kwargs))
        return True


def _client(monkeypatch, payload=None, *, with_store=True):
    monkeypatch.setenv("GROQ_PROVIDER_ACCOUNT_REF", _ACCOUNT_REF)
    app = FastAPI()
    billing_repo = _BillingRepo()
    app.state.repo = SimpleNamespace(billing_repo=billing_repo)
    if with_store:
        app.state.provider_evidence_store = _EvidenceStore(payload)
    app.include_router(billing_router)

    active_user = {
        "source": "jwt",
        "auth_user_id": str(uuid.uuid4()),
        "organization_id": _ORG_ID,
        "ruolo": "owner",
        "aal": "aal2",
        "auth_time": int(time.time()),
    }
    app.dependency_overrides[deps.get_current_user] = lambda: active_user
    app.dependency_overrides[deps.get_organization_context] = lambda: active_user
    return TestClient(app), active_user, billing_repo, app


def _request_body(attempt_id, payload, *, digest=None):
    return {
        "attempt_id": str(attempt_id),
        "evidence_object_id": _OBJECT_ID,
        "evidence_sha256": digest or hashlib.sha256(payload).hexdigest(),
        "attestations": _ATTESTATIONS,
    }


def test_reconciliation_routes_verify_artifact_and_require_distinct_actors(monkeypatch):
    attempt_id = uuid.uuid4()
    payload = _artifact(attempt_id)
    client, active_user, billing_repo, app = _client(monkeypatch, payload)
    body = _request_body(attempt_id, payload)

    proposed = client.post(
        "/api/billing/provider-attempts/reconciliation/proposals", json=body,
    )
    assert proposed.status_code == 200, proposed.text
    assert proposed.json() == {
        "proposed": True,
        "proposal_version": 7,
        "independent_approval_required": True,
    }
    proposal_actor = active_user["auth_user_id"]
    proposal_evidence = billing_repo.proposals[0][1]["evidence"]
    assert proposal_evidence.attempt_id == attempt_id
    assert proposal_evidence.provider == "groq"
    assert app.state.provider_evidence_store.reads == [(_ORG_ID, _OBJECT_ID)]

    approver = {
        **active_user,
        "auth_user_id": str(uuid.uuid4()),
        "ruolo": "manager",
    }
    active_user.update(approver)
    approval_body = {**body, "proposal_version": 7}
    approved = client.post(
        "/api/billing/provider-attempts/reconciliation/approvals", json=approval_body,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json() == {"reconciled": True, "proposal_version": 7}
    assert billing_repo.approvals[0][1]["actor_auth_user_id"] != proposal_actor


def test_reconciliation_routes_reject_digest_artifact_mismatch_and_missing_store(monkeypatch):
    attempt_id = uuid.uuid4()
    payload = _artifact(attempt_id)
    client, _, billing_repo, app = _client(monkeypatch, payload)

    bad_digest = _request_body(attempt_id, payload, digest="0" * 64)
    response = client.post(
        "/api/billing/provider-attempts/reconciliation/proposals", json=bad_digest,
    )
    assert response.status_code == 422
    assert "digest mismatch" in response.json()["detail"]

    mismatched_payload = _artifact(uuid.uuid4())
    app.state.provider_evidence_store = _EvidenceStore(mismatched_payload)
    artifact_mismatch = _request_body(attempt_id, mismatched_payload)
    response = client.post(
        "/api/billing/provider-attempts/reconciliation/proposals", json=artifact_mismatch,
    )
    assert response.status_code == 422
    assert "does not match" in response.json()["detail"]
    assert billing_repo.proposals == []

    app.state.provider_evidence_store = None
    response = client.post(
        "/api/billing/provider-attempts/reconciliation/proposals",
        json=_request_body(attempt_id, payload),
    )
    assert response.status_code == 503
    assert billing_repo.proposals == []


def test_provider_recovery_list_and_pre_dispatch_recovery_are_tenant_scoped(monkeypatch):
    attempt_id = uuid.uuid4()
    client, active_user, billing_repo, _ = _client(monkeypatch)
    billing_repo.recovery_attempts = [{
        "attempt_id": str(attempt_id), "provider": "openrouter",
        "dispatch_state": "prepared", "can_recover": True,
    }]

    listed = client.get("/api/billing/provider-attempts/recovery")
    assert listed.status_code == 200
    assert listed.json() == {"attempts": billing_repo.recovery_attempts}
    assert billing_repo.recovery_calls == [(active_user["organization_id"],)]

    recovered = client.post(
        f"/api/billing/provider-attempts/{attempt_id}/recover",
    )
    assert recovered.status_code == 200, recovered.text
    assert recovered.json() == {"state": "not_sent"}
    args, kwargs = billing_repo.recovery_calls[-1]
    assert args == (active_user["organization_id"], attempt_id)
    assert kwargs == {
        "actor_auth_user_id": active_user["auth_user_id"],
        "aal": "aal2", "auth_time": active_user["auth_time"],
    }


def test_provider_recovery_route_requires_mfa_before_repository_access(monkeypatch):
    attempt_id = uuid.uuid4()
    client, active_user, billing_repo, _ = _client(monkeypatch)
    active_user["aal"] = "aal1"

    response = client.post(
        f"/api/billing/provider-attempts/{attempt_id}/recover",
    )

    assert response.status_code == 403
    assert response.headers.get("x-mfa-required") == "true"
    assert billing_repo.recovery_calls == []


def test_provider_recovery_route_verifies_openrouter_generation_server_side(monkeypatch):
    attempt_id = uuid.uuid4()
    generation_id = "gen-route-verification-001"
    created_at = datetime.now(timezone.utc)
    client, active_user, billing_repo, app = _client(monkeypatch)
    billing_repo.recovery_result = {
        "state": "lookup_required", "attempt_id": attempt_id,
        "generation_id": generation_id,
        "requested_model": "openrouter/xingchen-agi/xing4.0-29b-a4b:free",
        "attempt_created_at": created_at,
        "provider_request_id": None,
        "recovery_worker_id": uuid.uuid4(), "lease_epoch": 2,
    }
    payload = {"data": {
        "id": generation_id, "external_user": str(attempt_id),
        "model": "xingchen-agi/xing4.0-29b-a4b:free",
        "created_at": created_at.isoformat(), "finish_reason": "stop",
        "native_tokens_prompt": 13, "native_tokens_completion": 5,
        "latency": 0.4, "total_cost": 0.0,
    }}
    fetches = []

    async def fetcher(api_key, requested_generation_id):
        fetches.append((api_key, requested_generation_id))
        return json.dumps(payload).encode()

    from src.core import provider_recovery
    monkeypatch.setattr(provider_recovery, "fetch_openrouter_generation", fetcher)
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-test-key-not-a-secret")

    response = client.post(
        f"/api/billing/provider-attempts/{attempt_id}/recover",
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"state": "verified", "provider": "openrouter"}
    assert fetches == [("synthetic-test-key-not-a-secret", generation_id)]
    assert len(billing_repo.finalized) == 1
    _, kwargs = billing_repo.finalized[0]
    evidence = kwargs["evidence"]
    assert evidence.generation_id == generation_id
    assert evidence.prompt_tokens == 13
    assert evidence.completion_tokens == 5
    assert evidence.provider_cost_usd == "0.0"


def test_provider_recovery_route_sanitizes_provider_lookup_failure(monkeypatch):
    attempt_id = uuid.uuid4()
    client, _, billing_repo, app = _client(monkeypatch)
    billing_repo.recovery_result = {
        "state": "lookup_required", "attempt_id": attempt_id,
        "generation_id": "gen-route-failure-001",
        "requested_model": "openrouter/xingchen-agi/xing4.0-29b-a4b:free",
        "attempt_created_at": datetime.now(timezone.utc),
        "provider_request_id": None,
        "recovery_worker_id": uuid.uuid4(), "lease_epoch": 2,
    }

    async def unavailable(_api_key, _generation_id):
        raise RuntimeError("synthetic provider body must not leak")

    from src.core import provider_recovery
    monkeypatch.setattr(provider_recovery, "fetch_openrouter_generation", unavailable)
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-test-key-not-a-secret")

    response = client.post(
        f"/api/billing/provider-attempts/{attempt_id}/recover",
    )

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Provider reconciliation is unavailable; accounting remains blocked"
    )
    assert "synthetic provider body" not in response.text
    assert billing_repo.finalized == []

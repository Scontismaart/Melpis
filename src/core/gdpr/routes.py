import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, field_validator

from src.core.auth.audit import audit_log
from src.core.auth.dependencies import require_ruolo, require_mfa
from src.core.db.repository import CoreRepository
from src.core.gdpr import token_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/gdpr", tags=["gdpr"])


# ── Task 7: DPA template ──────────────────────────────────────

DPA_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Data Processing Agreement</title></head>
<body style="font-family:system-ui;max-width:800px;margin:2em auto;line-height:1.6">
<h1>Data Processing Agreement (DPA)</h1>
<p><strong>Last updated:</strong> July 2026</p>

<h2>1. Parties</h2>
<p><strong>Data Controller:</strong> The organization subscribing to the WhatsApp AI Responder service.</p>
<p><strong>Data Processor:</strong> WhatsApp AI Responder (the platform provider).</p>

<h2>2. Scope &amp; Purpose</h2>
<p>This DPA governs the processing of personal data by the Processor on behalf of the Controller in connection with the WhatsApp AI Responder service, including automated message handling, AI-driven responses, booking management, and customer communication.</p>

<h2>3. Categories of Data Processed</h2>
<ul>
  <li>Contact information: phone numbers, names</li>
  <li>Message content: text, timestamps, delivery status</li>
  <li>Conversation metadata: conversation history, interaction patterns</li>
  <li>Booking details: dates, times, party size, special requests</li>
  <li>Consent records: opt-in/opt-out status, timestamps</li>
</ul>

<h2>4. Sub-processors</h2>
<table border="1" cellpadding="8" cellspacing="0" style="border-collapse:collapse;width:100%">
<tr><th>Sub-processor</th><th>Service</th><th>Data Location</th></tr>
<tr><td>Meta Platforms (WhatsApp Business API)</td><td>Message delivery</td><td>USA — verify transfer mechanism</td></tr>
<tr><td>Groq / provider LLM configurato</td><td>Generazione risposte AI</td><td>Ubicazione e meccanismo di trasferimento da verificare contrattualmente</td></tr>
<tr><td>Google (Business Profile, Calendar)</td><td>Reviews, calendar sync</td><td>USA — verify transfer mechanism</td></tr>
<tr><td>Stripe</td><td>Payment processing, subscriptions, booking deposits</td><td>USA/EU per Stripe configuration</td></tr>
<tr><td>Supabase</td><td>Authentication, database hosting</td><td>Depends on the Supabase project region</td></tr>
<tr><td>Sentry</td><td>Error monitoring (active only if configured)</td><td>USA/EU per configuration</td></tr>
<tr><td>SMTP provider</td><td>Escalation and account-suspension email notifications</td><td>Per configured SMTP provider</td></tr>
<tr><td>Airtable, Softr</td><td>GDPR deletion propagation only</td><td>To be verified</td></tr>
</table>
<p>Transfer mechanisms (Standard Contractual Clauses and/or applicable adequacy decisions such as the EU-U.S. Data Privacy Framework) apply where data is transferred outside the EEA, as available for each provider at the time of signing.</p>

<h2>5. Data Retention</h2>
<p>Messages and conversation data are retained for a maximum of 90 days:</p>
<ul>
  <li>Day 60: automatic soft-delete (data invisible to the application)</li>
  <li>Day 90: permanent physical deletion</li>
</ul>
<p>This policy is currently fixed for all tenants and is not yet configurable per tenant.</p>

<h2>6. Security Measures</h2>
<ul>
  <li>Encryption at rest: WhatsApp access tokens encrypted with Fernet (AES-128)</li>
  <li>PII redaction: automatic masking of recognized personal data patterns (international phone numbers, email addresses) via regex in all application logs</li>
  <li>Access control: role-based authentication (owner, manager, staff)</li>
  <li>Soft-delete: data recoverable within 30-day grace period</li>
</ul>
<p>Note: log redaction is based on pattern recognition and is a reasonable mitigation, not an absolute guarantee: it does not cover numeric identifiers without an international prefix, nor personal data in free-text fields that do not follow a recognizable format.</p>

<h2>7. Data Subject Rights</h2>
<p>Controllers may exercise data subject rights via the GDPR API:</p>
<ul>
  <li><strong>Export:</strong> <code>GET /api/gdpr/export</code> — JSON export with 15-minute pre-signed URL</li>
  <li><strong>Deletion:</strong> <code>POST /api/gdpr/delete</code> — hard-delete with external service propagation</li>
  <li><strong>Consent:</strong> <code>GET/PUT /api/gdpr/consent-prefs</code> — manage consent preferences</li>
  <li><strong>AI Transparency:</strong> the first automated reply to each new
      customer is preceded by a disclosure that the user is interacting with
      an AI assistant, with the option to be transferred to a human (HITL)</li>
</ul>

<h2>8. Data Breach Notification</h2>
<p>Without undue delay and in any case within 48 hours of becoming aware of a
personal data breach affecting the Controller's data, the Processor notifies
the Controller, providing the information available to enable the Controller
to comply with its notification obligations under Article 33 GDPR and, where
applicable, communication to data subjects under Article 34 GDPR.</p>

<h2>9. Contact</h2>
<p>For data protection inquiries, contact the provider through the service support channels.</p>
</body>
</html>"""


@router.get("/dpa", response_class=HTMLResponse)
async def get_dpa():
    return DPA_HTML


# ── Task 8: Consent preference center ─────────────────────────

class ConsentPrefsInput(BaseModel):
    phone_number: str
    consent_status: str

    @field_validator("consent_status")
    @classmethod
    def validate_status(cls, v):
        if v not in ("granted", "withdrawn", "unknown"):
            raise ValueError("consent_status must be 'granted', 'withdrawn', or 'unknown'")
        return v


class ConsentPrefsOutput(BaseModel):
    id: str
    phone_number: str
    consent_status: str | None
    consent_updated_at: str | None


@router.get("/consent-prefs")
async def get_consent_prefs(
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager")),
):
    repo: CoreRepository = request.app.state.repo
    org_id = user["organization_id"]
    rows = await repo.get_contacts_by_org(org_id)
    result = []
    for row in rows:
        result.append(ConsentPrefsOutput(
            id=str(row["id"]),
            phone_number=row.get("phone_number", ""),
            consent_status=row.get("consent_status"),
            consent_updated_at=row["consent_updated_at"].isoformat() if row.get("consent_updated_at") else None,
        ))
    return result


@router.put("/consent-prefs")
async def update_consent_prefs(
    body: ConsentPrefsInput,
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager")),
):
    repo: CoreRepository = request.app.state.repo
    org_id = user["organization_id"]

    from src.whatsapp.repository import Repository as WRepo
    wrepo = WRepo(pool=repo.pool)
    contact = await wrepo.get_or_create_contact(org_id, body.phone_number)

    event_type = "opt_in" if body.consent_status == "granted" else "opt_out" if body.consent_status == "withdrawn" else None
    if event_type:
        await wrepo.record_consent_event(contact["id"], event_type, "manual_staff",
                                         organization_id=org_id)

    new_status = await wrepo.get_contact_consent(contact["id"], org_id)
    return {"phone_number": body.phone_number, "consent_status": new_status}


# ── Task 9: Data rights ────────────────────────────────────────

@router.get("/export")
async def gdpr_export(
    request: Request,
    user: dict = Depends(require_ruolo("owner")),
    _mfa: dict = Depends(require_mfa()),
):
    repo: CoreRepository = request.app.state.repo
    org_id = user["organization_id"]
    data = await _export_tenant_data(repo, org_id)
    token = secrets.token_urlsafe(32)
    try:
        await token_store.save_token(token, str(org_id), data)
    except token_store.TokenStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail="Export temporaneamente non disponibile") from exc
    download_url = str(request.base_url) + f"api/gdpr/download/{token}"

    await audit_log(repo, organization_id=org_id, action="gdpr.export",
                    auth_user_id=user.get("auth_user_id"),
                    details={"expires_in_minutes": 15})

    return {"download_url": download_url, "expires_in_minutes": 15}


@router.get("/download/{token}")
async def gdpr_download(token: str):
    try:
        meta = await token_store.pop_token(token)
    except token_store.TokenStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail="Export temporaneamente non disponibile") from exc
    if meta is None:
        raise HTTPException(status_code=404, detail="Link di export scaduto o non valido")
    data = meta["data"]
    return data


@router.post("/delete")
async def gdpr_delete(
    request: Request,
    user: dict = Depends(require_ruolo("owner")),
    _mfa: dict = Depends(require_mfa()),
):
    from src.core.gdpr.propagation import propagate_hard_delete

    repo: CoreRepository = request.app.state.repo
    org_id = user["organization_id"]

    propagation_results = await propagate_hard_delete(str(org_id))

    await audit_log(repo, organization_id=org_id, action="gdpr.hard_delete",
                    auth_user_id=user.get("auth_user_id"),
                    details={"propagation": propagation_results})

    await repo.delete_organization(org_id)

    return {
        "status": "deleted",
        "propagation": propagation_results,
        "message": "Organization deletion completed locally; external propagation results are reported per service.",
    }


@router.get("/retention-policy")
async def retention_policy():
    return {
        "retention_days": 60,
        "purge_after_days": 30,
        "simulator_response_days": 30,
        "simulator_history_idle_hours": 24,
    }


async def _export_tenant_data(repo: CoreRepository, org_id: str) -> dict:
    contacts = await repo.get_contacts_by_org(org_id)
    conversations = await repo.get_conversations_by_org(org_id)
    messages = await repo.get_messages_by_org(org_id)
    simulation_requests = await repo.get_simulation_requests_by_org(org_id)

    return {
        "organization_id": str(org_id),
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "contacts": [
            {"id": str(c["id"]), "phone": c.get("phone_number"), "consent_status": c.get("consent_status")}
            for c in contacts
        ],
        "conversations": [
            {"id": str(c["id"]), "created_at": c.get("created_at").isoformat() if c.get("created_at") else None}
            for c in conversations
        ],
        "messages": [
            {
                "id": str(m["id"]),
                "direction": m.get("direction"),
                "message_type": m.get("message_type"),
                "content_text": m.get("content_text"),
                "created_at": m.get("created_at").isoformat() if m.get("created_at") else None,
            }
            for m in messages
        ],
        "simulator_responses": [
            {
                "request_id": str(item["request_id"]),
                "user_id": str(item["auth_user_id"]),
                "payload_hash": item.get("payload_hash"),
                "status": item.get("status"),
                "response": item.get("response"),
                "created_at": item.get("created_at").isoformat() if item.get("created_at") else None,
                "completed_at": item.get("completed_at").isoformat() if item.get("completed_at") else None,
            }
            for item in simulation_requests
        ],
    }

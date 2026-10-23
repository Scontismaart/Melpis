"""Owner-selected Calendar authority; status never implies an unverified sync."""
from datetime import datetime, timedelta, timezone
import json
from uuid import UUID

from fastapi import HTTPException

from src.core.bookings.auto_policy import validate_auto_booking_configuration
from src.core.calendar.connection import MODES, SCOPES, authority_from, connection_lock, resources_for
from src.core.google_feature_flags import google_calendar_enabled


async def calendar_status_payload(pool, org_id):
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM google_calendar_credentials WHERE organization_id=$1", UUID(str(org_id)))
        policy = await conn.fetchrow("SELECT config FROM booking_settings WHERE organization_id=$1", UUID(str(org_id)))
        counts = await conn.fetchrow("""SELECT
            COUNT(*) FILTER(WHERE sync_status IN ('pending','processing')) AS pending_count,
            COUNT(*) FILTER(WHERE sync_status IN ('failed','blocked','pending_retry')) AS failed_count,
            COUNT(*) FILTER(WHERE sync_status='uncertain') AS uncertain_count
            FROM external_booking_sync WHERE organization_id=$1 AND provider='google'""", UUID(str(org_id)))
    mode = authority_from(dict(policy) if policy else None)
    scopes = row["granted_scopes"] if row else []
    if isinstance(scopes, str):
        scopes = json.loads(scopes)
    connected = bool(row and row["connection_status"] == "connected" and row["sync_enabled"]
                     and set(SCOPES).issubset(scopes) and row["access_token"] and row["refresh_token"])
    error = "sync_failed" if counts["failed_count"] or counts["uncertain_count"] else None
    if row and row["last_error_code"]:
        error = "provider_unavailable"  # No raw provider string or DB error is returned.
    if not google_calendar_enabled():
        status = "disabled"
    elif row and row["connection_status"] in {"revoked", "reconnect_required"}:
        status = row["connection_status"]
    elif mode == "google_authoritative" and (not connected or error or not row["last_verified_at"]):
        status = "authoritative_unavailable"
    elif error:
        status = "sync_failed"
    elif counts["pending_count"]:
        status = "syncing"
    else:
        status = "connected" if connected and row["last_verified_at"] else "not_connected"
    return {"enabled": google_calendar_enabled(), "connected": connected, "mode": mode,
            "integration_status": status, "error_code": error,
            "calendar_id": row["calendar_id"] if row else "primary",
            "resource_calendar_ids": list(resources_for(dict(row))) if row else [],
            "sync_enabled": bool(row and row["sync_enabled"]), "last_sync_at": row["last_sync_at"] if row else None,
            **dict(counts)}


async def save_calendar_settings(service, org_id, body):
    values = body.model_dump(exclude_none=True)
    if not values or (body.mode is not None and body.mode not in MODES):
        raise HTTPException(400, "Invalid Calendar settings")
    if body.mode != "internal" and not google_calendar_enabled():
        raise HTTPException(503, "Google Calendar disabled")
    org = UUID(str(org_id))
    before = await service.connection.row(org)
    candidate = dict(before or {"calendar_id": "primary"})
    for field in ("calendar_id", "resource_calendar_ids", "sync_enabled"):
        if field in values:
            candidate[field] = values[field]
    try:
        resources_for(candidate)
    except (ValueError, TypeError):
        raise HTTPException(400, "Invalid Calendar resources") from None
    needs_provider = body.mode in {"internal_calendar_sync", "google_authoritative"} or any(
        key in values for key in ("calendar_id", "resource_calendar_ids"))
    if needs_provider:
        if not before or before["connection_status"] != "connected" or not before["sync_enabled"]:
            raise HTTPException(409, "Reconnect Calendar before choosing this mode")
        now = datetime.now(timezone.utc)
        probe = await service.availability(org, now, now + timedelta(minutes=1), connection=candidate)
        if not probe.verified:
            raise HTTPException(409, "Calendar resources could not be verified")
    async with connection_lock(service.repo.pool, org) as conn, conn.transaction():
        current = await conn.fetchrow("SELECT * FROM google_calendar_credentials WHERE organization_id=$1 FOR UPDATE", org)
        if before and (not current or current["connection_epoch"] != before["connection_epoch"]):
            raise HTTPException(409, "Calendar connection changed; retry")
        organization = await conn.fetchrow("SELECT timezone,business_profile FROM organizations WHERE id=$1 FOR KEY SHARE", org)
        if not organization:
            raise HTTPException(404, "Organization not found")
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"booking-capacity|{org}")
        settings = await conn.fetchrow("SELECT * FROM booking_settings WHERE organization_id=$1 FOR UPDATE", org)
        old_mode = authority_from(dict(settings) if settings else None)
        config = settings["config"] if settings else {}
        if isinstance(config, str):
            config = json.loads(config)
        if body.mode is not None:
            config["a05"] = {**(config.get("a05") or {"enabled": False}), "authority": body.mode}
        profile = organization["business_profile"] or {}
        if isinstance(profile, str):
            profile = json.loads(profile)
        capacities = settings["capienze_orarie"] if settings else {}
        if isinstance(capacities, str):
            capacities = json.loads(capacities)
        validation = validate_auto_booking_configuration({"config": config, "capienze_orarie": capacities},
                                                         organization["timezone"], profile.get("verticale"))
        if not validation.valid:
            raise HTTPException(409, "Existing A05 policy is incompatible")
        if current and resources_for(dict(current)) != resources_for(candidate):
            active = await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM external_booking_sync
                WHERE organization_id=$1 AND provider='google' AND (external_booking_id IS NOT NULL
                    OR sync_status NOT IN ('cancelled','succeeded')))""", org)
            if active:
                raise HTTPException(409, "Reconcile existing Calendar events before changing resources")
        await conn.execute("""INSERT INTO booking_settings(organization_id,config) VALUES($1,$2::jsonb)
            ON CONFLICT(organization_id) DO UPDATE SET config=EXCLUDED.config,updated_at=NOW()""", org, json.dumps(config))
        if current:
            await conn.execute("""UPDATE google_calendar_credentials SET calendar_id=$2,
                resource_calendar_ids=$3::jsonb,sync_enabled=$4,oauth_sync_enabled=$4,updated_at=NOW() WHERE organization_id=$1""",
                org, candidate["calendar_id"], json.dumps(list(resources_for(candidate))[1:]), candidate.get("sync_enabled", True))
        # Mode changes do not opt historical bookings in. Existing jobs are frozen,
        # with uncertainty preserved, until explicitly reconciled or cancelled.
        if body.mode is not None and body.mode != old_mode:
            await conn.execute("""UPDATE external_booking_sync SET
                sync_status=CASE WHEN calendar_dispatch_state='possibly_sent' THEN 'uncertain' ELSE 'blocked' END,
                sync_error='authority_changed',calendar_fence=calendar_fence+1,calendar_lease_token=NULL,
                calendar_lease_until=NULL,updated_at=NOW() WHERE organization_id=$1 AND provider='google'
                  AND sync_status NOT IN ('succeeded','cancelled')""", org)
    return await calendar_status_payload(service.repo.pool, org)

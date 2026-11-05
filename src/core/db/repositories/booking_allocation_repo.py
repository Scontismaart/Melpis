"""Isolated, dark allocation engine for existing canonical bookings.

This repository never creates or confirms a booking and never contacts a
calendar/provider. It owns a transaction for every operation.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

from src.core.bookings.allocation_contracts import (
    AllocationRequest, derive_allocation, semantic_request_hash,
)
from src.core.bookings.catalog_contracts import (
    CatalogDraft, MinuteDuration, WeeklyAvailability, civil_stay_interval,
    validate_catalog_json,
)
from src.core.calendar.availability import local_instant
from src.core.db.repositories.booking_catalog_repo import (
    BookingCatalogRepository, CatalogUnavailable, canonical, decode,
)


class AllocationDenied(PermissionError):
    """Current principal, tenant, or erasure state does not authorize access."""


class AllocationConflict(ValueError):
    """The request key or expected generation conflicts with current state."""


class AllocationUnavailable(RuntimeError):
    """Current authoritative state cannot safely be established."""


def _uuid(value):
    try:
        return value if type(value) is UUID else UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise AllocationDenied("Invalid trusted scope") from None


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _booking_fingerprint(row):
    # Deliberately excludes customer names, phone numbers and free text.
    return _digest({key: row.get(key) for key in (
        "data", "ora", "coperti", "stato", "contact_id",
        "id_conversazione", "calendar_revision", "completata_at", "no_show_at",
    )})


class BookingAllocationRepository:
    def __init__(self, pool):
        self.pool = pool
        self.__contact_preimage = None

    async def _seal_contact_preimage(self, conn, org, request, *, mutation=None):
        """Seal a locked, already validated contact-edit preimage for this transaction."""
        if not conn.is_in_transaction() or self.__contact_preimage is not None:
            raise AllocationDenied('Contact preimage unavailable')
        operation = mutation.operation if mutation is not None else 'edit_contact'
        expected_action = 'cancel' if operation in {'cancel','reject','no_show'} else 'allocate' if operation == 'reactivate' else 'replace'
        if request.action != expected_action:
            raise AllocationDenied('Preimage action unavailable')
        # Observe the actual locked rows here. A supplied fingerprint or snapshot
        # is not authority to bypass the normal allocator's stale-state check.
        booking = await conn.fetchrow('SELECT * FROM bookings WHERE organization_id=$1 AND id=$2 FOR UPDATE', org, request.booking_id)
        allocation = await conn.fetchrow('SELECT * FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2 FOR UPDATE', org, request.booking_id)
        if (not booking or not allocation or allocation['state'] != ('cancelled' if operation == 'reactivate' else 'active')
            or allocation['allocation_revision'] != request.expected_allocation_revision
            or allocation['catalog_revision'] != request.expected_catalog_revision
            or allocation['booking_fingerprint'] != _booking_fingerprint(booking)
            or allocation['booking_calendar_revision'] != booking['calendar_revision']):
            raise AllocationConflict('Contact preimage changed')
        token = object()
        txid = await conn.fetchval('SELECT txid_current()')
        if mutation is not None:
            from src.core.bookings.booking_mutation_contract import BookingMutation
            if type(mutation) is not BookingMutation or mutation.operation not in {'reschedule', 'reschedule_stay', 'confirm', 'complete', 'cancel', 'reject', 'no_show', 'reactivate'}:
                raise AllocationDenied('Unsupported preimage command')
            if mutation.operation in {'confirm', 'complete'} and (booking['stato'] not in {'in_attesa','confermata','da_verificare'}
                or booking['completata_at'] is not None or booking['no_show_at'] is not None):
                raise AllocationConflict('Occupied booking preimage changed')
            if mutation.operation == 'reactivate' and (booking['stato'] not in {'cancellata','cancellato','rifiutata'}
                or booking['completata_at'] is not None or booking['no_show_at'] is not None):
                raise AllocationConflict('Reactivation preimage changed')
            if mutation.operation in {'cancel','reject','no_show'} and (booking['stato'] not in {'in_attesa','confermata'}
                or booking['completata_at'] is not None or booking['no_show_at'] is not None):
                raise AllocationConflict('Release preimage changed')
        stay = None
        if mutation is not None and mutation.operation == 'reschedule_stay':
            stay = await conn.fetchrow('SELECT * FROM booking_stays WHERE organization_id=$1 AND booking_id=$2 FOR UPDATE', org, request.booking_id)
            if (not stay or stay['revision'] != mutation.expected_stay_revision
                or (stay['arrival_date'], stay['departure_date']) != (allocation['arrival_date'], allocation['departure_date'])):
                raise AllocationConflict('Stay preimage changed')
        transaction_now = await conn.fetchval('SELECT now()')
        self.__contact_preimage = (token, conn, txid, org, request, dict(booking), dict(allocation), mutation, dict(stay) if stay else None, transaction_now)
        return token

    async def apply(self, organization_id: UUID, auth_user_id: UUID, request: AllocationRequest):
        return await self._run(organization_id, auth_user_id, request, observe=False)

    async def observe(self, organization_id: UUID, auth_user_id: UUID, request: AllocationRequest):
        return await self._run(organization_id, auth_user_id, request, observe=True)

    async def _run(self, organization_id, auth_user_id, request, *, observe):
        org, auth = _uuid(organization_id), _uuid(auth_user_id)
        if not isinstance(request, AllocationRequest):
            raise ValueError("AllocationRequest required")
        async with self.pool.acquire() as conn:
            async with conn.transaction(isolation="read_committed"):
                return await self._run_on_connection(conn, org, auth, request, observe=observe)

    async def _apply_on_connection(self, conn, organization_id, auth_user_id, request, *, mutation_hash=None, contact_preimage=None):
        """Apply inside an existing caller-owned transaction; no commit or provider calls."""
        if not conn.is_in_transaction():
            raise RuntimeError("Allocation requires an active transaction")
        if not isinstance(request, AllocationRequest):
            raise ValueError("AllocationRequest required")
        return await self._run_on_connection(conn, _uuid(organization_id), _uuid(auth_user_id), request, observe=False, mutation_hash=mutation_hash, contact_preimage=contact_preimage)

    async def _apply_worker_on_connection(self, conn, organization_id, message_id, inbox_id, operation_id, service_digest, request):
        """Apply a verified create inside the lifecycle owner's transaction."""
        if not conn.is_in_transaction():
            raise RuntimeError("Allocation requires an active transaction")
        org, message, inbox, operation = map(_uuid, (organization_id, message_id, inbox_id, operation_id))
        if (type(request) is not AllocationRequest or request.action != 'allocate'
                or request.expected_allocation_revision != 0
                or type(service_digest) is not str or len(service_digest) != 64
                or any(c not in '0123456789abcdef' for c in service_digest)):
            raise AllocationDenied('Invalid worker allocation scope')
        # Match SQL079's actual caller check even for replay, where no insert
        # trigger fires. Lock the evidence until this transaction ends.
        source = await conn.fetchrow("""SELECT o.message_id FROM inbound_worker_operations o
            WHERE o.organization_id=$1 AND o.message_id=$2 AND o.inbox_id=$3 AND o.operation_id=$4
              AND COALESCE(NULLIF(current_setting('role'), 'none'), session_user) =
                  pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid='public.inbound_worker_operations'::regclass))
            FOR KEY SHARE""", org, message, inbox, operation)
        if not source:
            raise AllocationDenied('Worker provenance unavailable')
        worker = (message, inbox, operation, service_digest)
        return await self._run_on_connection(conn, org, None, request, observe=False, worker=worker)

    async def _run_on_connection(self, conn, org, auth, request, *, observe, mutation_hash=None, contact_preimage=None, worker=None):
        orgrow = await conn.fetchrow(
            "SELECT id,timezone,business_profile FROM organizations WHERE id=$1 FOR SHARE", org)
        if not orgrow:
            raise AllocationDenied("Organization unavailable")
        await conn.execute(
            "SELECT pg_advisory_xact_lock_shared(hashtextextended($1::text,606))", str(org))
        if await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM booking_stay_purge_fences WHERE organization_id=$1 AND booking_id=$2)
            OR EXISTS(SELECT 1 FROM booking_stay_tombstones WHERE organization_id=$1 AND request_id=$3)''', org, request.booking_id, request.request_id):
            raise AllocationDenied('Purged booking or request cannot be replayed')
        actor = await self._actor(conn, org, auth) if worker is None else None
        booking = await conn.fetchrow(
            "SELECT * FROM bookings WHERE organization_id=$1 AND id=$2", org, request.booking_id)
        if not booking:
            raise AllocationDenied("Booking is outside the organization")
        conversation_id, subject = await self._lineage(conn, org, booking)
        if worker is not None:
            source = await conn.fetchrow("""SELECT conversation_id,c24_original_subject_id FROM messages
                WHERE organization_id=$1 AND id=$2 AND direction='inbound' AND deleted_at IS NULL FOR SHARE""", org, worker[0])
            if (not source or not conversation_id or subject is None
                    or source['conversation_id'] != conversation_id or source['c24_original_subject_id'] != subject
                    or booking.get('source_message_id') != str(worker[0])):
                raise AllocationDenied('Worker booking lineage unavailable')
        if conversation_id:
            conv = await conn.fetchrow(
                "SELECT id,contact_id,deleted_at FROM conversations WHERE organization_id=$1 AND id=$2 FOR UPDATE",
                org, conversation_id)
            if not conv or conv["deleted_at"] is not None or conv["contact_id"] != subject:
                raise AllocationDenied("Booking lineage is unavailable")
        await self._gdpr(conn, org, subject)
        # Shared with the catalog publisher's exclusive capacity lock.
        await conn.fetchval("SELECT pg_advisory_xact_lock_shared(hashtext($1))",
                            f"booking-capacity|{org}")
        config_row = await conn.fetchrow(
            "SELECT config FROM booking_settings WHERE organization_id=$1 FOR SHARE", org)
        current, sector, authority = await self._catalog(conn, org, orgrow, config_row)
        if current.sector != sector or current.timezone != orgrow["timezone"]:
            raise AllocationUnavailable("Catalog scope changed")

        # Final parent lock follows catalog serialization; re-read all lineage.
        booking = await conn.fetchrow(
            "SELECT * FROM bookings WHERE organization_id=$1 AND id=$2 FOR UPDATE", org, request.booking_id)
        if not booking:
            raise AllocationDenied("Booking is unavailable")
        conversation_id, subject = await self._lineage(conn, org, booking)
        if worker is not None and (booking.get('source_message_id') != str(worker[0])
                or conversation_id != source['conversation_id'] or subject != source['c24_original_subject_id']):
            raise AllocationDenied('Worker booking lineage changed')
        if conversation_id:
            conv = await conn.fetchrow(
                "SELECT id,contact_id,deleted_at FROM conversations WHERE organization_id=$1 AND id=$2",
                org, conversation_id)
            if not conv or conv["deleted_at"] is not None or conv["contact_id"] != subject:
                raise AllocationDenied("Booking lineage changed")
        await self._gdpr(conn, org, subject)
        fingerprint = _booking_fingerprint(booking)

        if not observe:
            digest = (semantic_request_hash(request, organization_id=org, actor_user_id=actor) if worker is None else
                      _digest({'version': 2, 'organization_id': str(org), 'service_digest': worker[3],
                               'request': request.model_dump(mode='json')}))
            # Serialize one request UUID across booking IDs before receipt lookup.
            await conn.fetchval("SELECT pg_advisory_xact_lock(hashtextextended($1,723))",
                                f"allocation-request|{org}|{request.request_id}")
            previous = await conn.fetchrow(
                """SELECT booking_id,actor_user_id,COALESCE(to_jsonb(r)->>'actor_kind','human') AS actor_kind,
                          (to_jsonb(r)->>'service_message_id')::uuid AS service_message_id,
                          (to_jsonb(r)->>'service_inbox_id')::uuid AS service_inbox_id,
                          (to_jsonb(r)->>'service_operation_id')::uuid AS service_operation_id,action,payload_hash,outcome,
                          applied_allocation_revision,catalog_revision,mutation_hash
                     FROM booking_allocation_requests r WHERE organization_id=$1 AND request_id=$2""",
                org, request.request_id)
            if previous:
                if authority not in {"internal", "internal_calendar_sync"}:
                    raise AllocationUnavailable("Current authority is not supported")
                if (previous["booking_id"] != request.booking_id or previous["actor_user_id"] != actor
                        or previous["action"] != request.action or previous["payload_hash"] != digest
                        or previous["mutation_hash"] != mutation_hash
                        or previous['actor_kind'] != ('human' if worker is None else 'inbound_worker')
                        or (previous['service_message_id'], previous['service_inbox_id'], previous['service_operation_id'])
                           != ((None, None, None) if worker is None else worker[:3])):
                    raise AllocationConflict("Request identifier was used for a different operation")
                current_set = await conn.fetchrow(
                    "SELECT allocation_revision,state FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2",
                    org, request.booking_id)
                return self._result(previous["outcome"], previous["applied_allocation_revision"],
                    current_set, previous["catalog_revision"], current.revision, True)

        old = await conn.fetchrow(
            "SELECT * FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2 FOR UPDATE",
            org, request.booking_id)
        old_claims = await conn.fetch(
            "SELECT resource_id,resource_kind,exclusive,quantity,starts_at,ends_at FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2 ORDER BY resource_id",
            org, request.booking_id)
        stale = bool(old and old["booking_fingerprint"] != fingerprint)
        occupied_transition = False
        sealed_release = False
        if contact_preimage is not None:
            sealed, self.__contact_preimage = self.__contact_preimage, None
            if not sealed:
                raise AllocationDenied('Contact preimage unavailable')
            token, original_conn, original_txid, original_org, original_request, original_booking, original_set, mutation, original_stay, transaction_now = sealed
            occupied_transition = bool(mutation and mutation.operation in {'confirm', 'complete', 'reactivate'})
            sealed_release = bool(mutation and mutation.operation in {'cancel','reject','no_show'})
            if occupied_transition or sealed_release:
                keys = ('data','ora','coperti','contact_id','id_conversazione','nome_cliente','telefono','note')
            else:
                keys = ('contact_id', 'id_conversazione', 'completata_at', 'no_show_at', 'nome_cliente', 'telefono', 'note') if mutation else ('data', 'ora', 'coperti', 'stato', 'contact_id', 'id_conversazione', 'completata_at', 'no_show_at')
            prior_revision = original_booking['calendar_revision'] or 0
            if (token is not contact_preimage or conn is not original_conn or org != original_org
                or request != original_request or request.action != ('cancel' if sealed_release else 'allocate' if mutation and mutation.operation == 'reactivate' else 'replace') or not conn.is_in_transaction()
                or await conn.fetchval('SELECT txid_current()') != original_txid
                or not old or old['state'] != ('cancelled' if mutation and mutation.operation == 'reactivate' else 'active')
                or old['allocation_revision'] != original_set['allocation_revision']
                or old['catalog_revision'] != original_set['catalog_revision']
                or old['booking_calendar_revision'] != original_booking['calendar_revision']
                or old['booking_fingerprint'] != _booking_fingerprint(original_booking)
                or any(booking[k] != original_booking[k] for k in keys)
                or (occupied_transition and (booking['stato'] != {'confirm':'confermata','complete':'completata','reactivate':'in_attesa'}[mutation.operation]
                    or booking['completata_at'] != (transaction_now if mutation.operation == 'complete' else None)
                    or booking['no_show_at'] is not None))
                or (sealed_release and (booking['stato'] != {'cancel':'cancellata','reject':'rifiutata','no_show':'no_show'}[mutation.operation]
                    or booking['completata_at'] is not None
                    or booking['no_show_at'] != (transaction_now if mutation.operation == 'no_show' else None)))
                or (mutation and not occupied_transition and not sealed_release and (booking['data'] != date.fromisoformat(mutation.data)
                    or booking['ora'] != time.fromisoformat(mutation.ora)
                    or booking['coperti'] != mutation.coperti or booking['stato'] != 'in_attesa'
                    or (mutation.operation == 'reschedule' and (booking['data'] != request.appointment_date
                        or booking['ora'] != request.appointment_time
                        or (request.party_size is not None and booking['coperti'] != request.party_size)))
                    or (mutation.operation == 'reschedule_stay' and (str(request.arrival_date) != mutation.arrival_date
                        or str(request.departure_date) != mutation.departure_date))))
                or (booking['calendar_revision'] or 0) not in (prior_revision, prior_revision + 1)):
                raise AllocationConflict('Contact preimage changed')
            if original_stay is not None:
                stay = await conn.fetchrow('SELECT * FROM booking_stays WHERE organization_id=$1 AND booking_id=$2 FOR UPDATE', org, request.booking_id)
                if (not stay or stay['revision'] != original_stay['revision'] + 1
                    or (stay['arrival_date'], stay['departure_date']) != (request.arrival_date, request.departure_date)
                    or any(stay[k] != original_stay[k] for k in ('original_subject_id', 'original_conversation_id', 'source_message_id'))):
                    raise AllocationConflict('Stay postimage changed')
            stale = False
        ineligible = (booking["stato"] not in {"in_attesa", "confermata"}
                      or booking.get("completata_at") is not None
                      or booking.get("no_show_at") is not None)
        if occupied_transition:
            ineligible = False
        if not observe and request.expected_catalog_revision != current.revision:
            raise AllocationConflict("Published catalog revision changed")
        if not observe and request.expected_allocation_revision != (old["allocation_revision"] if old else 0):
            raise AllocationConflict("Allocation revision changed")
        new_bounds, new_claims = None, ()
        # A change of authority must not silently release local capacity.
        # Cancellation is an admission operation too; no provider protocol
        # is implemented by this disconnected repository.
        if authority not in {"internal", "internal_calendar_sync"} or current.sector not in {"ristorante", "parrucchiere", "centro_estetico", "hotel_bnb", "studio_medico_dentista"}:
            if not observe:
                await self._receipt(conn, org, request, actor, digest, "unknown", None, current.revision, mutation_hash, worker)
            return self._result("unknown", None, old, current.revision, current.revision, False)
        if request.action != "cancel":
            if current is None or current.sector != sector or current.timezone != orgrow["timezone"]:
                raise AllocationUnavailable("Catalog authority is unavailable")
            try:
                new_bounds = derive_allocation(request, current)
                new_claims = new_bounds[2]
            except (ValueError, TypeError, OverflowError):
                if not observe:
                    raise AllocationUnavailable("Allocation proposal is invalid for the published catalog") from None
                return self._result("unknown", None, old, current.revision,
                                    current.revision, False)

        if (stale or ineligible) and request.action != "cancel":
            if not observe:
                await self._take_locks(conn, org, request, old_claims, new_claims)
                await self._receipt(conn, org, request, actor, digest, "unknown", None, current.revision, mutation_hash, worker)
            return self._result("unknown" if observe else "unknown", None, old,
                                current.revision, current.revision, False)

        if not observe and request.expected_catalog_revision != current.revision:
            raise AllocationConflict("Published catalog revision changed")
        if not observe and request.expected_allocation_revision != (old["allocation_revision"] if old else 0):
            raise AllocationConflict("Allocation revision changed")
        if request.action == "allocate" and old and old["state"] == "active":
            raise AllocationConflict("Booking already has an active allocation")
        if request.action == "replace" and (not old or old["state"] != "active"):
            raise AllocationUnavailable("No active allocation can be replaced")

        await self._take_locks(conn, org, request, old_claims, new_claims)
        if not observe:
            # Re-read after waiting; READ COMMITTED gives a new statement snapshot.
            old = await conn.fetchrow(
                "SELECT * FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2",
                org, request.booking_id)
            old_claims = await conn.fetch(
                "SELECT resource_id,resource_kind,exclusive,quantity,starts_at,ends_at FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2 ORDER BY resource_id",
                org, request.booking_id)
            if not observe and request.expected_allocation_revision != (old["allocation_revision"] if old else 0):
                raise AllocationConflict("Allocation revision changed while waiting")

        if observe and request.action == "cancel":
            return self._result("unknown", None, old, current.revision,
                                current.revision, False)
        if request.action == "cancel":
            if not old or old["state"] != "active":
                outcome = "unknown"
                applied = None
            else:
                outcome = "cancelled"
                applied = old["allocation_revision"] + 1
                if not observe:
                    await conn.execute("DELETE FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2", org, request.booking_id)
                    if sealed_release:
                        await conn.execute("""UPDATE booking_allocation_sets SET state='cancelled',allocation_revision=$3,
                            booking_fingerprint=$4,booking_calendar_revision=$5,updated_at=NOW()
                            WHERE organization_id=$1 AND booking_id=$2""", org, request.booking_id, applied,
                            fingerprint, booking.get('calendar_revision') or 0)
                    else:
                        await conn.execute(
                            "UPDATE booking_allocation_sets SET state='cancelled',allocation_revision=$3,updated_at=NOW() WHERE organization_id=$1 AND booking_id=$2",
                            org, request.booking_id, applied)
        else:
            if request.action == "replace" and old and old["state"] == "active":
                current_resources = {r.resource_id: r for r in current.resources}
                if any(claim["resource_id"] not in current_resources
                       or current_resources[claim["resource_id"]].kind != claim["resource_kind"]
                       or current_resources[claim["resource_id"]].exclusive != claim["exclusive"]
                       for claim in old_claims):
                    status = "unknown"
                else:
                    status = await self._availability(conn, org, current, new_bounds, new_claims,
                                                      request.booking_id, old_claims, old, fingerprint)
            else:
                status = await self._availability(conn, org, current, new_bounds, new_claims,
                                                  request.booking_id, old_claims, old, fingerprint)
            if observe:
                return self._result(status, None, old, current.revision, current.revision, False)
            if status != "available" or old and old["state"] == "active" and stale:
                outcome, applied = ("unavailable" if status == "unavailable" else "unknown"), None
            elif old and old["state"] == "active" and any(c["resource_kind"] != kind or c["exclusive"] != exclusive
                  for c in old_claims for rid, kind, exclusive, _q in new_claims if c["resource_id"] == rid):
                outcome, applied = "unknown", None
            else:
                outcome = "allocated" if request.action == "allocate" else "replaced"
                applied = (old["allocation_revision"] + 1) if old else 1
                if not observe:
                    start, end, _ = new_bounds
                    temporal = "civil_nights" if request.arrival_date is not None else "minutes"
                    await conn.execute("DELETE FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2", org, request.booking_id)
                    await conn.execute("""INSERT INTO booking_allocation_sets
                        (organization_id,booking_id,allocation_revision,state,catalog_revision,service_id,timezone,temporal_kind,starts_at,ends_at,arrival_date,departure_date,booking_fingerprint,booking_calendar_revision)
                        VALUES($1,$2,$3,'active',$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                        ON CONFLICT(organization_id,booking_id) DO UPDATE SET
                          allocation_revision=EXCLUDED.allocation_revision,state='active',catalog_revision=EXCLUDED.catalog_revision,
                          service_id=EXCLUDED.service_id,timezone=EXCLUDED.timezone,temporal_kind=EXCLUDED.temporal_kind,
                          starts_at=EXCLUDED.starts_at,ends_at=EXCLUDED.ends_at,arrival_date=EXCLUDED.arrival_date,
                          departure_date=EXCLUDED.departure_date,booking_fingerprint=EXCLUDED.booking_fingerprint,
                          booking_calendar_revision=EXCLUDED.booking_calendar_revision,updated_at=NOW()""",
                        org, request.booking_id, applied, current.revision, request.service_id, current.timezone,
                        temporal, start, end, request.arrival_date, request.departure_date, fingerprint,
                        booking.get("calendar_revision") or 0)
                    for rid, kind, exclusive, quantity in new_claims:
                        await conn.execute("""INSERT INTO booking_resource_allocations
                            (organization_id,booking_id,allocation_revision,catalog_revision,state,resource_id,resource_kind,exclusive,quantity,starts_at,ends_at)
                            VALUES($1,$2,$3,$4,'active',$5,$6,$7,$8,$9,$10)""",
                            org, request.booking_id, applied, current.revision, rid, kind, exclusive,
                            quantity, start, end)
        if not observe:
            await self._receipt(conn, org, request, actor, digest, outcome, applied, current.revision, mutation_hash, worker)
        current_set = old
        if not observe and applied is not None:
            current_set = {"allocation_revision": applied, "state": "cancelled" if outcome == "cancelled" else "active"}
        return self._result(outcome, applied, current_set,
                            current.revision,
                            current.revision, False)

    @staticmethod
    async def _actor(conn, org, auth):
        access = await conn.fetchrow(
            "SELECT disabled_at FROM auth_access_lifecycle WHERE auth_user_id=$1 FOR SHARE", auth)
        if not access or access["disabled_at"] is not None:
            raise AllocationDenied("Account access is inactive")
        row = await conn.fetchrow("""SELECT up.id,om.ruolo FROM organization_memberships om
            JOIN user_profiles up ON up.id=om.user_id WHERE om.organization_id=$1
              AND up.auth_user_id=$2 AND om.joined_at IS NOT NULL
              AND om.ruolo IN ('owner','manager') FOR SHARE OF om,up""", org, auth)
        if not row:
            raise AllocationDenied("Active owner or manager membership required")
        return _uuid(row["id"])

    @staticmethod
    async def _lineage(conn, org, booking):
        direct = booking.get("contact_id")
        text_id = booking.get("id_conversazione")
        conv_id = None
        if text_id:
            try:
                conv_id = UUID(str(text_id))
            except (TypeError, ValueError):
                raise AllocationDenied("Malformed conversation lineage") from None
            conv = await conn.fetchrow("SELECT contact_id,deleted_at FROM conversations WHERE organization_id=$1 AND id=$2", org, conv_id)
            if not conv or conv["deleted_at"] is not None or conv["contact_id"] is None:
                raise AllocationDenied("Conversation lineage unavailable")
            if direct is not None and direct != conv["contact_id"]:
                raise AllocationDenied("Contradictory booking lineage")
            direct = conv["contact_id"]
        if direct is not None:
            live = await conn.fetchval("SELECT EXISTS(SELECT 1 FROM contacts WHERE organization_id=$1 AND id=$2 AND deleted_at IS NULL)", org, direct)
            if not live:
                raise AllocationDenied("Contact lineage unavailable")
        return conv_id, direct

    @staticmethod
    async def _gdpr(conn, org, subject):
        rows = await conn.fetch("SELECT scope,subject_id FROM gdpr_erasure_barriers WHERE organization_id=$1", org)
        if any(row["scope"] == "organization" or subject is None or row["subject_id"] == subject for row in rows):
            raise AllocationDenied("Erasure barrier rejects allocation access")

    async def _catalog(self, conn, org, orgrow, settings):
        business = decode(orgrow["business_profile"])
        sector = business.get("verticale") if isinstance(business, dict) else None
        config = settings["config"] if settings else {}
        raw_config = decode(config)
        if not isinstance(raw_config, dict):
            raise AllocationUnavailable("Current booking settings are malformed")
        a05 = raw_config.get("a05", {})
        if not isinstance(a05, dict):
            raise AllocationUnavailable("Current booking authority is malformed")
        authority = a05.get("authority", "internal")
        if not isinstance(authority, str):
            raise AllocationUnavailable("Current booking authority is malformed")
        helper = BookingCatalogRepository.__new__(BookingCatalogRepository)
        try:
            state = helper._state(config, org)
            await helper._verify_evidence(conn, org, state)
            if not state["published"]:
                raise CatalogUnavailable("No current published catalog")
            draft = validate_catalog_json(canonical(state["published"]), organization_id=org).catalog
            return draft, sector, authority
        except (CatalogUnavailable, ValueError, TypeError, KeyError):
            raise AllocationUnavailable("Current published catalog is unhealthy") from None

    async def _take_locks(self, conn, org, request, old_claims, new_claims):
        await conn.fetchval("SELECT pg_advisory_xact_lock(hashtextextended($1,723))",
                            f"allocation-request|{org}|{request.request_id}")
        ids = sorted({c["resource_id"] for c in old_claims} | {c[0] for c in new_claims})
        for resource_id in ids:
            await conn.fetchval("SELECT pg_advisory_xact_lock(hashtextextended($1,725))",
                                f"allocation-resource|{org}|{resource_id}")

    async def _observe_candidate_locked(self, conn, org, catalog, candidate):
        """Read diagnostic capacity inside the caller's authenticated transaction.

        No booking ID, receipt, writer hook or independent commit is created.
        Resource locks use the same namespace/order as allocation writers.
        """
        from src.core.bookings.allocation_contracts import derive_candidate
        if not conn.is_in_transaction():
            raise AllocationUnavailable("Scoped observation transaction required")
        start, end, claims = derive_candidate(candidate, catalog)
        for resource_id in sorted({claim[0] for claim in claims}):
            await conn.fetchval("SELECT pg_advisory_xact_lock(hashtextextended($1,725))",
                                f"allocation-resource|{org}|{resource_id}")
        return await self._availability(conn, org, catalog, (start,end,None), claims, None, (), None, None)

    async def _availability(self, conn, org, catalog, bounds, claims, booking_id, old_claims, old, fingerprint):
        start, end, _ = bounds
        fits = self._weekly_fits(catalog.availability, start, end, catalog.timezone)
        if fits is None:
            return "unknown"
        if not fits:
            return "unavailable"
        for rid, kind, exclusive, quantity in claims:
            resource = next((x for x in catalog.resources if x.resource_id == rid), None)
            if resource is None or resource.kind != kind or resource.exclusive != exclusive:
                return "unknown"
            # Kind/exclusivity changes cannot erase the meaning of existing claims.
            changed = await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM booking_resource_allocations
                WHERE organization_id=$1 AND resource_id=$2 AND (resource_kind<>$3 OR exclusive<>$4))""",
                org, rid, kind, exclusive)
            if changed:
                return "unknown"
            rows = await conn.fetch("""SELECT a.booking_id,a.starts_at,a.ends_at,a.quantity,
                    s.booking_fingerprint,b.data,b.ora,b.coperti,b.stato,b.contact_id,b.id_conversazione,b.calendar_revision,b.completata_at,b.no_show_at
                FROM booking_resource_allocations a JOIN booking_allocation_sets s
                  ON s.organization_id=a.organization_id AND s.booking_id=a.booking_id
                JOIN bookings b ON b.organization_id=a.organization_id AND b.id=a.booking_id
                WHERE a.organization_id=$1 AND s.organization_id=$1 AND b.organization_id=$1
                  AND a.resource_id=$2 AND a.starts_at<$4 AND a.ends_at>$3
                  AND ($5::uuid IS NULL OR a.booking_id<>$5) ORDER BY a.starts_at,a.ends_at""", org, rid, start, end, booking_id)
            if any(row["booking_fingerprint"] != _booking_fingerprint(row) for row in rows):
                return "unknown"
            if exclusive and rows:
                return "unavailable"
            events = [(start, quantity), (end, -quantity)]
            for row in rows:
                left, right = max(start, row["starts_at"]), min(end, row["ends_at"])
                if left < right:
                    events.extend(((left, row["quantity"]), (right, -row["quantity"])))
            events.sort(key=lambda item: item[0])
            active = peak = 0
            i = 0
            while i < len(events):
                instant = events[i][0]
                delta = 0
                while i < len(events) and events[i][0] == instant:
                    delta += events[i][1]
                    i += 1
                active += delta
                peak = max(peak, active)
            if peak > resource.capacity:
                return "unavailable"
        return "available"

    @staticmethod
    def _weekly_fits(availability: WeeklyAvailability, start, end, timezone_name):
        """Check complete civil coverage without iterating over an unbounded stay."""
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(timezone_name)
        local_start, local_end = start.astimezone(zone), end.astimezone(zone)
        first, last = local_start.date(), local_end.date()
        closed = set(availability.closed_dates)
        days = {d.weekday: tuple(sorted(d.windows, key=lambda w: w.start_minute)) for d in availability.days}
        full = {wd for wd, windows in days.items() if windows and windows[0].start_minute == 0 and windows[-1].end_minute == 1440 and all(a.end_minute == b.start_minute for a,b in zip(windows,windows[1:]))}
        span = (last - first).days
        # Every whole intervening week must have a full-day window every day.
        if span > 14 and len(days) != 7:
            return None
        if span > 14 and len(full) != 7:
            return False
        if span > 14:
            if any(first <= d < last for d in closed):
                return False
            dates = {first, last}
        else:
            dates = {first + timedelta(days=i) for i in range(span + 1)}
        for day in dates:
            day_start = local_start if day == first else datetime.combine(day, time.min, zone)
            day_end = local_end if day == last else datetime.combine(day + timedelta(days=1), time.min, zone)
            if day_start >= day_end:
                continue
            if day in closed:
                return False
            windows = days.get(day.weekday())
            if windows is None:
                return None
            day_start = local_start if day == first else datetime.combine(day, time.min, zone)
            day_end = local_end if day == last else datetime.combine(day + timedelta(days=1), time.min, zone)
            if day_start >= day_end:
                continue
            covered_until = day_start
            for window in windows:
                try:
                    def boundary(minutes):
                        target = datetime.combine(day, time.min) + timedelta(minutes=minutes)
                        return local_instant(target.date().isoformat(), target.time().isoformat(), timezone_name).astimezone(timezone.utc)
                    left, right = boundary(window.start_minute), boundary(window.end_minute)
                except ValueError:
                    return None
                left, right = max(left, day_start.astimezone(timezone.utc)), min(right, day_end.astimezone(timezone.utc))
                if left > covered_until.astimezone(timezone.utc):
                    return False
                if right > covered_until.astimezone(timezone.utc):
                    covered_until = right
                if covered_until >= day_end.astimezone(timezone.utc):
                    break
            if covered_until < day_end.astimezone(timezone.utc):
                return False
        return True

    @staticmethod
    async def _receipt(conn, org, request, actor, digest, outcome, applied, catalog_revision, mutation_hash=None, worker=None):
        if worker is None:
            await conn.execute("""INSERT INTO booking_allocation_requests
                (organization_id,request_id,booking_id,actor_user_id,action,payload_hash,outcome,applied_allocation_revision,catalog_revision,mutation_hash)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""", org, request.request_id, request.booking_id,
                actor, request.action, digest, outcome, applied, catalog_revision, mutation_hash)
        else:
            await conn.execute("""INSERT INTO booking_allocation_requests
                (organization_id,request_id,booking_id,actor_user_id,actor_kind,service_message_id,service_inbox_id,service_operation_id,action,payload_hash,outcome,applied_allocation_revision,catalog_revision,mutation_hash)
                VALUES($1,$2,$3,NULL,'inbound_worker',$4,$5,$6,$7,$8,$9,$10,$11,$12)""",
                org, request.request_id, request.booking_id, *worker[:3], request.action, digest,
                outcome, applied, catalog_revision, mutation_hash)

    @staticmethod
    def _result(outcome, applied, current, original_catalog_revision, current_catalog_revision, replayed):
        return {
            "outcome": outcome,
            "applied_allocation_revision": applied,
            "current_allocation_revision": current["allocation_revision"] if current else 0,
            "current_state": current["state"] if current else "none",
            "catalog_revision": original_catalog_revision,
            "current_catalog_revision": current_catalog_revision,
            "replayed": replayed,
            "runtime_enabled": False,
            "confirmation_authorized": False,
        }

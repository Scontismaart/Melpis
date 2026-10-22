"""Server-only intake checks for durable GDPR tombstones."""
import base64
import hashlib
import hmac
import os
import re
from copy import deepcopy
from uuid import UUID


class ErasureBlocked(RuntimeError):
    pass


def identifier_fingerprint(organization_id, identifier):
    try:
        key = base64.urlsafe_b64decode(os.environ['ENCRYPTION_KEY'].encode('ascii'))
        if len(key) != 32:
            raise ValueError
    except (KeyError, ValueError, UnicodeError) as exc:
        raise ErasureBlocked('GDPR tombstone key unavailable') from exc
    normalized = re.sub(r'[^0-9]', '', identifier)
    if not normalized:
        normalized = identifier.strip().casefold()
    digest = hmac.new(key, f'gdpr-contact-v1:{organization_id}:{normalized}'.encode(), hashlib.sha256).hexdigest()
    return digest, hashlib.sha256(key).hexdigest()[:16]


async def check_contact_intake(conn, organization_id, identifier):
    """Called in the SAME transaction as insert/upsert, before any identity read."""
    org = UUID(str(organization_id))
    await conn.execute('SELECT pg_advisory_xact_lock_shared(hashtextextended($1::text,606))', str(org))
    barriers = await conn.fetch(
        'SELECT scope,identifier_digest,key_id FROM gdpr_erasure_barriers WHERE organization_id=$1', org,
    )
    if not barriers:
        return
    digest, key_id = identifier_fingerprint(org, identifier)
    if any(b['scope'] == 'organization' or b['key_id'] != key_id
           or hmac.compare_digest(b['identifier_digest'] or '', digest) for b in barriers):
        # A key rotation cannot silently invalidate existing suppression hashes.
        raise ErasureBlocked('GDPR erasure barrier rejects intake')


async def filter_webhook_payload(conn, organization_id, payload):
    """Suppress erased senders BEFORE their raw webhook is durably accepted.

    Filter individual messages so a mixed Meta batch retains other subjects.
    This does no HTTP and never waits for generation. Unknown tombstone keys
    fail closed until an explicitly reviewed key migration is completed.
    """
    org = UUID(str(organization_id))
    await conn.execute('SELECT pg_advisory_xact_lock_shared(hashtextextended($1::text,606))', str(org))
    barriers = await conn.fetch(
        'SELECT scope,identifier_digest,key_id FROM gdpr_erasure_barriers WHERE organization_id=$1', org,
    )
    if not barriers:
        return payload
    if any(b['scope'] == 'organization' for b in barriers):
        return None

    def allowed(identifier):
        digest, key_id = identifier_fingerprint(org, str(identifier))
        if any(b['key_id'] != key_id for b in barriers):
            raise ErasureBlocked('GDPR suppression key migration required')
        return all(not hmac.compare_digest(b['identifier_digest'], digest) for b in barriers)

    clean = deepcopy(payload)
    meaningful = False
    for entry in clean.get('entry', []):
        if 'messaging' in entry:
            entry['messaging'] = [m for m in entry['messaging'] if allowed(
                m.get('recipient' if m.get('message', {}).get('is_echo') else 'sender', {}).get('id', '')
            )]
            meaningful |= bool(entry['messaging'])
        for change in entry.get('changes', []):
            value = change.get('value', {})
            if 'messages' in value:
                value['messages'] = [m for m in value['messages'] if allowed(m.get('from', ''))]
                meaningful |= bool(value['messages'])
            if 'contacts' in value:
                value['contacts'] = [c for c in value['contacts'] if allowed(c.get('wa_id', ''))]
            if 'statuses' in value:
                statuses = []
                for status in value['statuses']:
                    if status.get('recipient_id') and not allowed(status['recipient_id']):
                        continue
                    erased = await conn.fetchval(
                        '''SELECT EXISTS(SELECT 1 FROM messages m JOIN conversations c
                             ON c.id=m.conversation_id AND c.organization_id=$1
                           JOIN gdpr_erasure_barriers b ON b.subject_id=c.contact_id AND b.organization_id=$1
                           WHERE m.organization_id=$1 AND m.wam_id=$2)''', org, status.get('id'),
                    )
                    if not erased:
                        statuses.append(status)
                value['statuses'] = statuses
                meaningful |= bool(statuses)
            if change.get('field') == 'message_template_status_update':
                meaningful = True
    return clean if meaningful else None

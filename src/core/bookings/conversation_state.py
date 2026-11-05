"""Dark administrative conversation state. Data/provenance never grant authority.

No route, worker, provider, booking or quota writer imports this module.
Persistence and the extra canonical-response metadata require C05/A06 approval.
"""
from __future__ import annotations

from datetime import date, time, timedelta, timezone
import hashlib
import hmac
import json
import re
import secrets
import unicodedata
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field, PrivateAttr, ValidationError, ValidationInfo, model_validator

from src.core.bookings.catalog_contracts import (
    CatalogDraft, CivilNightDuration, Contract, MinuteDuration, NonNegative,
    Positive, ScopedCatalog, civil_stay_interval,
)
from src.core.calendar.availability import local_instant
from src.models.schemas import RispostaOutput

SlotKey = Literal[
    'customer_name', 'contact_phone', 'contact_email', 'service_id', 'resource_id',
    'appointment_date', 'appointment_time', 'party_size', 'arrival_date',
    'departure_date', 'guest_count', 'room_count', 'stay_nights',
]
Intent = Literal['booking', 'modify', 'cancel', 'faq', 'price', 'hours',
                 'availability', 'handoff', 'unknown']
PRESENCE = frozenset({'customer_name', 'contact_phone', 'contact_email'})
DATES = frozenset({'appointment_date', 'arrival_date', 'departure_date'})
COUNTS = frozenset({'party_size', 'guest_count', 'room_count', 'stay_nights'})
REFERENCE = frozenset({'service_id', 'resource_id'})
_KEYS = PRESENCE | DATES | COUNTS | REFERENCE | {'appointment_time'}
# Process-local integrity capability, never serialized, logged or configured as
# an account credential. It protects the existing A04 model-copy/dump boundary.
_RESPONSE_SEAL_KEY = secrets.token_bytes(32)
_MEDICAL = re.compile(r'\b(dolor\w*|pain|bleed\w*|sangu\w*|urgent\w*|emergen\w*|'
                      r'diagnos\w*|farmac\w*|medicin\w*|symptom\w*|sintom\w*|terapi\w*|'
                      r'male|allerg\w*|febbr\w*|fever|respir\w*|breath\w*|soccorso)\b', re.I)
_ADMINISTRATIVE_MEDICAL_WORDS = frozenset('''
    vorrei voglio desidero prenotare prenotazione un una il la lo le i gli per
    di del della da alle alla al con senza e o ma invece non piu solo grazie
    buongiorno buonasera salve ciao visita appuntamento controllo amministrativo
    amministrativa standard orario data ora giorno cambiare spostare annullare
    cancellare confermo confermare mio mia nome telefono email numero appuntamenti
    lunedi martedi mercoledi giovedi venerdi sabato domenica oggi domani
    please i would like to book an a the appointment administrative visit check
    at on for with and or instead change move cancel my name phone email date time
    hello thanks thank you just only monday tuesday wednesday thursday friday
    saturday sunday today tomorrow
    je voudrais un une le la les rendez vous administratif administrative pour
    avec et ou changer annuler mon nom telephone merci bonjour
    quiero reservar cita administrativa administrativo para una uno el los las
    cambiar cancelar mi nombre telefono gracias hola
'''.split())


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class StateConflict(ValueError):
    pass


class SlotCandidate(Contract):
    field: SlotKey
    value: str | int | bool = Field(repr=False)

    @model_validator(mode='after')
    def administrative_value(self):
        value, key = self.value, self.field
        if key in PRESENCE:
            valid = type(value) is bool
        elif key in COUNTS:
            valid = type(value) is int and 0 < value <= 1_000_000
        elif key in DATES:
            valid = type(value) is str and bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}', value))
            if valid:
                try:
                    valid = date.fromisoformat(value).isoformat() == value
                except ValueError:
                    valid = False
        elif key == 'appointment_time':
            valid = type(value) is str and bool(re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value))
        else:
            valid = type(value) is str and bool(re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', value))
        if not valid:
            raise ValueError('Invalid administrative slot')
        return self


class ConversationProposal(Contract):
    intent: Intent
    slots: tuple[SlotCandidate, ...] = Field(default=(), max_length=13)
    clear: tuple[SlotKey, ...] = Field(default=(), max_length=13)
    ambiguous: tuple[SlotKey, ...] = Field(default=(), max_length=13)

    @model_validator(mode='after')
    def unique_operations(self):
        keys = [s.field for s in self.slots]
        if (len(set(keys)) != len(keys) or len(set(self.clear)) != len(self.clear)
                or len(set(self.ambiguous)) != len(self.ambiguous)
                or set(keys) & (set(self.clear) | set(self.ambiguous))
                or set(self.clear) & set(self.ambiguous)):
            raise ValueError('Conflicting slot operations')
        return self


def _bounded_json(payload):
    if type(payload) is not str or len(payload.encode('utf-8')) > 65536:
        raise ValueError('Administrative payload exceeds technical bound')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate administrative key')
            result[key] = value
        return result
    try:
        raw = json.loads(payload, object_pairs_hook=unique,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite value')))
        def depth(value, level=0):
            if level > 16:
                raise ValueError('Administrative nesting exceeds technical bound')
            if isinstance(value, dict):
                for child in value.values(): depth(child, level + 1)
            elif isinstance(value, list):
                for child in value: depth(child, level + 1)
        depth(raw)
        return raw
    except (RecursionError, json.JSONDecodeError):
        raise ValueError('Invalid administrative JSON') from None


def parse_proposal_json(payload: str) -> ConversationProposal:
    _bounded_json(payload)
    try:
        return ConversationProposal.model_validate_json(payload)
    except ValidationError:
        raise ValueError('Invalid administrative proposal') from None


def validate_proposal_catalog(proposal, catalog):
    allowed = set().union(*(set(f.key for f in s.fields) for s in catalog.services))
    allowed |= {'service_id', 'resource_id'}
    if catalog.sector == 'hotel_bnb': allowed |= {'stay_nights'}
    for key in [s.field for s in proposal.slots] + list(proposal.clear) + list(proposal.ambiguous):
        if key not in allowed:
            raise ValueError('Slot outside published administrative catalog')
    services = {s.service_id: s for s in catalog.services}
    resources = {r.resource_id for r in catalog.resources}
    for slot in proposal.slots:
        if slot.field == 'service_id' and slot.value not in services:
            raise ValueError('Unresolved service reference')
        if slot.field == 'resource_id' and slot.value not in resources:
            raise ValueError('Unresolved resource reference')


class StructuredConversationResponse(RispostaOutput):
    """Inactive subtype; existing A04 guardrail/commit retains these extra fields.

    Construction must use parse_structured_response_json with a server catalog.
    It never changes the operational RispostaOutput schema or its callers.
    """
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True, hide_input_in_errors=True)
    booking_state_proposal: ConversationProposal
    booking_catalog_revision: Positive
    _catalog_seal: str | None = PrivateAttr(default=None)
    _catalog_organization_id: UUID | None = PrivateAttr(default=None)

    def _protected_digest(self):
        if (type(self.booking_state_proposal) is not ConversationProposal
                or type(self.booking_catalog_revision) is not int
                or type(self._catalog_organization_id) is not UUID):
            raise ValueError('Unsealed structured response')
        body = canonical({'organization_id':str(self._catalog_organization_id),
                          'catalog':self.booking_catalog_revision,
                          'proposal':self.booking_state_proposal.model_dump(mode='json')})
        return hmac.new(_RESPONSE_SEAL_KEY, body.encode(), hashlib.sha256).hexdigest()

    def _assert_sealed(self):
        if type(self._catalog_seal) is not str or not hmac.compare_digest(self._catalog_seal, self._protected_digest()):
            raise ValueError('Structured response changed after catalog validation')

    def assert_catalog_organization(self, organization_id: UUID):
        self._assert_sealed()
        if self._catalog_organization_id != organization_id:
            raise ValueError('Structured response belongs to a different organization')

    def model_copy(self, *, update=None, deep=False):
        self._assert_sealed()
        copied = super().model_copy(update=update, deep=deep)
        copied._assert_sealed()
        return copied

    def model_dump(self, *args, **kwargs):
        self._assert_sealed()
        return super().model_dump(*args, **kwargs)

    def model_dump_json(self, *args, **kwargs):
        self._assert_sealed()
        return super().model_dump_json(*args, **kwargs)

    @model_validator(mode='after')
    def server_catalog_required(self, info: ValidationInfo):
        context = info.context
        if not isinstance(context, dict) or not isinstance(context.get('catalog'), ScopedCatalog):
            raise ValueError('Server catalog context required')
        current = context['catalog'].for_organization(context.get('organization_id'))
        if self.booking_catalog_revision != current.revision:
            raise ValueError('Canonical proposal catalog revision mismatch')
        # Revalidate even preconstructed nested models: model_copy can bypass
        # Pydantic field validators before reaching this issuance boundary.
        normalized = parse_proposal_json(canonical(self.booking_state_proposal.model_dump(mode='json')))
        validate_proposal_catalog(normalized, current)
        object.__setattr__(self, 'booking_state_proposal', normalized)
        self.__pydantic_private__['_catalog_organization_id'] = context['organization_id']
        self.__pydantic_private__['_catalog_seal'] = self._protected_digest()
        return self


def parse_structured_response_json(payload, *, catalog: ScopedCatalog, organization_id: UUID):
    current = catalog.for_organization(organization_id)
    raw = _bounded_json(payload)
    if not isinstance(raw, dict) or set(raw) - (set(RispostaOutput.model_fields) | {'booking_state_proposal'}):
        raise ValueError('Invalid structured response envelope')
    proposal = parse_proposal_json(canonical(raw.pop('booking_state_proposal', None)))
    validate_proposal_catalog(proposal, current)
    try:
        base = RispostaOutput.model_validate(raw)
        return StructuredConversationResponse.model_validate(
            {**base.model_dump(), 'booking_state_proposal':proposal,
             'booking_catalog_revision':current.revision},
            context={'catalog':catalog, 'organization_id':organization_id})
    except ValidationError:
        raise ValueError('Invalid guarded response envelope') from None


class SlotState(Contract):
    field: SlotKey
    value: str | int | bool = Field(repr=False)
    origin: Literal['ai_extracted', 'customer_provided', 'backend_derived']
    source_message_id: UUID
    source_revision: Positive
    catalog_revision: Positive
    derived_from: tuple[SlotKey, ...] = ()

    @model_validator(mode='after')
    def typed_value(self):
        SlotCandidate(field=self.field, value=self.value)
        return self


class Superseded(Contract):
    field: SlotKey
    source_message_id: UUID
    source_revision: Positive
    value_digest: str = Field(pattern=r'^[a-f0-9]{64}$')


class DerivedState(Contract):
    duration_minutes: Positive | None = None
    resource_ids: tuple[str, ...] = ()
    stay_nights: Positive | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    origin: Literal['published_catalog'] = 'published_catalog'


class Confirmation(Contract):
    content_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    source_message_id: UUID
    prompt_message_id: UUID
    prompt_body_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    prompt_revision: Positive
    template_version: Literal[1] = 1


class ConversationState(Contract):
    version: Literal[1] = 1
    organization_id: UUID
    conversation_id: UUID
    revision: NonNegative = 0
    catalog_revision: Positive
    intent: Intent = 'unknown'
    slots: tuple[SlotState, ...] = ()
    missing_fields: tuple[SlotKey, ...] = ()
    ambiguous_fields: tuple[SlotKey, ...] = ()
    superseded: tuple[Superseded, ...] = Field(default=(), max_length=64)
    derived: DerivedState = DerivedState()
    handoff_reason: Literal['medical_scope', 'outside_scope', 'catalog_review'] | None = None
    confirmation: Confirmation | None = None
    last_source_message_id: UUID | None = None
    last_payload_hash: str | None = None
    runtime_enabled: Literal[False] = False
    confirmation_authorized: Literal[False] = False

    @property
    def details_confirmed(self):
        return bool(self.confirmation and self.confirmation.content_digest == content_digest(self))


def state_digest(state):
    return digest(state.model_dump(mode='json'))


def content_digest(state):
    return digest({'org': str(state.organization_id), 'conversation': str(state.conversation_id),
        'catalog_revision': state.catalog_revision, 'intent': state.intent,
        'slots': [s.model_dump(mode='json') for s in state.slots],
        'ambiguous': state.ambiguous_fields, 'handoff': state.handoff_reason})


def new_state(organization_id, conversation_id, catalog):
    current = catalog.for_organization(organization_id)
    if type(conversation_id) is not UUID:
        raise ValueError('Trusted conversation UUID required')
    return ConversationState(organization_id=organization_id, conversation_id=conversation_id,
                             catalog_revision=current.revision)


def explicit_customer_fields(proposal, source_text, catalog):
    """Narrow literal evidence only; natural-language AI extraction stays AI origin."""
    result = set()
    for slot in proposal.slots:
        key, value = slot.field, slot.value
        if key in DATES | {'appointment_time'} and re.search(r'(?<![\w\d])' + re.escape(str(value)) + r'(?![\w\d])', source_text):
            result.add(key)
        # Labeled administrative values are exact, not semantic/model assertions.
        elif re.search(r'\b' + re.escape(key) + r'\s*[:=]\s*' + re.escape(str(value).lower()) + r'\b', source_text.lower()):
            result.add(key)
    return frozenset(result)


def reduce_proposal(state, proposal, *, source_message_id, catalog, expected_revision,
                    explicit_fields=frozenset(), medical_handoff=False):
    current = catalog.for_organization(state.organization_id)
    if state.catalog_revision != current.revision:
        raise StateConflict('Published catalog changed; explicit re-proposal required')
    if type(source_message_id) is not UUID or type(expected_revision) is not int:
        raise ValueError('Trusted source and revision required')
    validate_proposal_catalog(proposal, current)
    payload_hash = digest(proposal.model_dump(mode='json'))
    if state.last_source_message_id == source_message_id:
        if state.last_payload_hash != payload_hash:
            raise StateConflict('Source was reduced with another proposal')
        return state
    if expected_revision != state.revision:
        raise StateConflict('Conversation state revision changed')
    revision = state.revision + 1
    slots = {s.field: s for s in state.slots}
    history = list(state.superseded)
    ambiguous = set(state.ambiguous_fields)
    def remove(key):
        old = slots.pop(key, None)
        if old:
            history.append(Superseded(field=key, source_message_id=old.source_message_id,
                source_revision=old.source_revision, value_digest=digest(old.value)))
    changed = set(proposal.clear) | {s.field for s in proposal.slots} | set(proposal.ambiguous)
    for key in set(proposal.clear) | set(proposal.ambiguous): remove(key)
    for key in proposal.clear: ambiguous.discard(key)
    ambiguous |= set(proposal.ambiguous)
    for candidate in proposal.slots:
        if candidate.field in slots and slots[candidate.field].value == candidate.value:
            # Explicit customer evidence can promote a derived/model value even
            # when the scalar is unchanged. Its old dependencies no longer apply.
            previous = slots[candidate.field]
            if candidate.field not in explicit_fields or previous.origin == 'customer_provided':
                ambiguous.discard(candidate.field)
                continue
        remove(candidate.field)
        slots[candidate.field] = SlotState(field=candidate.field, value=candidate.value,
            origin='customer_provided' if candidate.field in explicit_fields else 'ai_extracted',
            source_message_id=source_message_id, source_revision=revision,
            catalog_revision=current.revision)
        ambiguous.discard(candidate.field)
    # Only dependent derived dates are invalidated; explicit independent dates survive.
    for key, slot in list(slots.items()):
        if slot.derived_from and changed.intersection(slot.derived_from): remove(key)
    if 'departure_date' in changed and 'stay_nights' not in changed:
        remove('stay_nights')
    services = {s.service_id: s for s in current.services}
    selected = slots.get('service_id')
    service = services.get(selected.value) if selected else None
    if selected and service is None:
        remove('service_id'); ambiguous.add('service_id')
    valid_resources = {r.resource_id for r in service.resources} if service else set()
    if 'resource_id' in slots and slots['resource_id'].value not in valid_resources:
        if 'resource_id' in changed:
            # Keep the customer's known catalog preference. A conflicting
            # service binding needs clarification, never an invented operator.
            ambiguous.add('service_id')
        else:
            remove('resource_id'); ambiguous.add('resource_id')
    if current.sector == 'hotel_bnb' and 'arrival_date' in slots and 'stay_nights' in slots and 'departure_date' not in slots:
        try:
            departure = date.fromisoformat(slots['arrival_date'].value) + timedelta(days=slots['stay_nights'].value)
            slots['departure_date'] = SlotState(field='departure_date', value=departure.isoformat(),
                origin='backend_derived', source_message_id=source_message_id,
                source_revision=revision, catalog_revision=current.revision,
                derived_from=('arrival_date', 'stay_nights'))
        except (ValueError, OverflowError): ambiguous.add('departure_date')
    required = {f.key for f in service.fields if f.required} if service else {'service_id'}
    if service:
        resources = {r.resource_id: r for r in current.resources}
        if any(resources[r.resource_id].kind == 'seat_pool' for r in service.resources): required.add('party_size')
        if any(resources[r.resource_id].kind == 'room' and not resources[r.resource_id].exclusive for r in service.resources): required.add('room_count')
    values = {k: s.value for k,s in slots.items()}
    derived = DerivedState(resource_ids=tuple(sorted(valid_resources)))
    if service and isinstance(service.duration, MinuteDuration):
        derived = derived.model_copy(update={'duration_minutes': service.duration.minutes})
        if 'appointment_date' in values and 'appointment_time' in values:
            try:
                start = local_instant(values['appointment_date'], values['appointment_time'], current.timezone).astimezone(timezone.utc)
                end = start + timedelta(minutes=service.duration.minutes + service.duration.buffer_after)
                start -= timedelta(minutes=service.duration.buffer_before)
                derived = derived.model_copy(update={'starts_at':start.isoformat(), 'ends_at':end.isoformat()})
            except (ValueError, OverflowError): ambiguous.add('appointment_time')
    elif service and isinstance(service.duration, CivilNightDuration) and {'arrival_date','departure_date'} <= values.keys():
        try:
            arrival, departure = date.fromisoformat(values['arrival_date']), date.fromisoformat(values['departure_date'])
            start,end = civil_stay_interval(arrival, departure, service.duration, current.timezone)
            derived = derived.model_copy(update={'stay_nights':(departure-arrival).days,
                'starts_at':start.astimezone(timezone.utc).isoformat(), 'ends_at':end.astimezone(timezone.utc).isoformat()})
        except (ValueError, OverflowError): ambiguous.add('departure_date')
    reason = state.handoff_reason if state.handoff_reason in {'medical_scope', 'outside_scope'} else None
    if medical_handoff: reason = 'medical_scope'
    elif proposal.intent == 'handoff': reason = reason or 'outside_scope'
    elif service and (service.handoff.staff_review_required or
            service.handoff.party_size_above is not None and values.get('party_size',0)>service.handoff.party_size_above):
        reason = reason or 'catalog_review'
    missing = {k for k in required if k not in slots or slots[k].value is False} | ambiguous
    if proposal.intent in {'faq','price','hours','cancel','handoff','unknown'}: missing = set()
    return ConversationState(organization_id=state.organization_id, conversation_id=state.conversation_id,
        revision=revision, catalog_revision=current.revision, intent=proposal.intent,
        slots=tuple(slots[k] for k in sorted(slots)), missing_fields=tuple(sorted(missing)),
        ambiguous_fields=tuple(sorted(ambiguous)), superseded=tuple(history[-64:]), derived=derived,
        handoff_reason=reason, last_source_message_id=source_message_id, last_payload_hash=payload_hash)


def medical_source_requires_handoff(text, catalog):
    """Positive administrative boundary, never a claim of clinical classification.

    A deny-list alone cannot prove safety. Unknown vocabulary in this dark
    medical path goes to staff; catalog free-text cannot extend the allowlist.
    """
    if catalog.sector != 'studio_medico_dentista':
        return False
    normalized = ''.join(c for c in unicodedata.normalize('NFKD', text.lower())
                         if not unicodedata.combining(c))
    if _MEDICAL.search(normalized):
        return True
    scrubbed = re.sub(r'\b\d{4}-\d{2}-\d{2}\b|\b(?:[01]\d|2[0-3]):[0-5]\d\b', ' ', normalized)
    words = re.findall(r'[^\W\d_]+', scrubbed, re.UNICODE)
    if any(word not in _ADMINISTRATIVE_MEDICAL_WORDS for word in words):
        return True
    if not words:
        return not normalized.strip() or bool(scrubbed.strip())
    return False


def render_confirmation_prompt(state, prompt_message_id):
    if type(prompt_message_id) is not UUID or prompt_message_id.version != 4:
        raise ValueError('Random version4 prompt UUID required')
    labels = {'service_id':'Servizio', 'resource_id':'Risorsa richiesta',
        'appointment_date':'Data', 'appointment_time':'Ora', 'party_size':'Persone',
        'arrival_date':'Arrivo', 'departure_date':'Partenza', 'guest_count':'Ospiti',
        'room_count':'Camere', 'stay_nights':'Notti', 'customer_name':'Nome fornito',
        'contact_phone':'Telefono fornito', 'contact_email':'Email fornita'}
    lines = ['Verifica i dati: nessuna prenotazione e ancora confermata.']
    for slot in state.slots:
        value = ('presente' if slot.value else 'non presente') if type(slot.value) is bool else str(slot.value)
        lines.append(f'{labels[slot.field]}: {value}')
    lines.append(f'MELPIS-CONFIRM-v1 {state_digest(state)} {prompt_message_id}')
    return '\n'.join(lines)


def confirm_details(state, *, source_message_id, prompt_message_id, prompt_body_hash, expected_revision):
    """Only repository-verified sent-prompt evidence may reach this data reducer."""
    if expected_revision != state.revision:
        raise StateConflict('Confirmation revision changed')
    if state.missing_fields or state.ambiguous_fields or state.handoff_reason or state.intent not in {'booking','modify','availability'}:
        raise StateConflict('Details are not eligible for customer confirmation')
    confirmation = Confirmation(content_digest=content_digest(state), source_message_id=source_message_id,
        prompt_message_id=prompt_message_id, prompt_body_hash=prompt_body_hash, prompt_revision=state.revision)
    return state.model_copy(update={'revision':state.revision+1, 'confirmation':confirmation,
                                   'last_source_message_id':source_message_id, 'last_payload_hash':prompt_body_hash})

"""C2.1 configuration contracts only; deliberately NOT imported by booking runtime.

Validation establishes shape and local references, never authorization, provider
availability or customer intent. No database, network, persistence or AI calls.
Display names remain untrusted data. No custom field labels/prompts/expressions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
import json
from types import MappingProxyType
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.core.calendar.availability import CalendarAvailability, local_instant


Sector = Literal[
    "ristorante", "parrucchiere", "centro_estetico", "hotel_bnb",
    "studio_medico_dentista",
]
Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Positive = Annotated[int, Field(strict=True, gt=0)]
NonNegative = Annotated[int, Field(strict=True, ge=0)]
FieldKey = Literal[
    "customer_name", "contact_phone", "contact_email", "appointment_date",
    "appointment_time", "party_size", "service_id", "resource_id",
    "arrival_date", "departure_date", "guest_count", "room_count",
]
FieldType = Literal["text", "phone", "email", "date", "time", "positive_integer", "reference"]
FIELD_TYPES = MappingProxyType({
    "customer_name": "text", "contact_phone": "phone", "contact_email": "email",
    "appointment_date": "date", "appointment_time": "time",
    "party_size": "positive_integer", "service_id": "reference",
    "resource_id": "reference", "arrival_date": "date", "departure_date": "date",
    "guest_count": "positive_integer", "room_count": "positive_integer",
})
_CONTACT = frozenset({"customer_name", "contact_phone", "contact_email"})
_APPOINTMENT = _CONTACT | {"appointment_date", "appointment_time", "service_id", "resource_id"}
_STAY = _CONTACT | {"arrival_date", "departure_date", "guest_count", "room_count", "service_id", "resource_id"}


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)


class AdministrativeField(Contract):
    key: FieldKey
    value_type: FieldType
    required: bool

    @model_validator(mode="after")
    def fixed_type(self):
        if FIELD_TYPES[self.key] != self.value_type:
            raise ValueError("Administrative field type does not match its fixed definition")
        return self


class MinuteWindow(Contract):
    """Half-open local interval, minute resolution; 1440 denotes end of day."""
    start_minute: Annotated[int, Field(strict=True, ge=0, lt=1440)]
    end_minute: Annotated[int, Field(strict=True, gt=0, le=1440)]

    @model_validator(mode="after")
    def ordered(self):
        if self.start_minute >= self.end_minute:
            raise ValueError("Invalid local interval; split overnight windows into civil days")
        return self


class DayAvailability(Contract):
    weekday: Annotated[int, Field(strict=True, ge=0, le=6)]
    windows: tuple[MinuteWindow, ...]

    @model_validator(mode="after")
    def non_overlapping(self):
        ordered = sorted(self.windows, key=lambda item: item.start_minute)
        if any(a.end_minute > b.start_minute for a, b in zip(ordered, ordered[1:])):
            raise ValueError("Overlapping opening windows")
        return self


class WeeklyAvailability(Contract):
    days: tuple[DayAvailability, ...]
    closed_dates: tuple[date, ...] = ()

    @model_validator(mode="after")
    def unique_days(self):
        if len({day.weekday for day in self.days}) != len(self.days):
            raise ValueError("Duplicate weekday")
        if len(set(self.closed_dates)) != len(self.closed_dates):
            raise ValueError("Duplicate closure date")
        return self

    def windows_for(self, weekday: int) -> tuple[MinuteWindow, ...] | None:
        """None means unknown; empty tuple means explicitly closed, never free."""
        if type(weekday) is not int or weekday not in range(7):
            raise ValueError("Invalid weekday")
        return next((day.windows for day in self.days if day.weekday == weekday), None)


class MinuteDuration(Contract):
    kind: Literal["minutes"]
    minutes: Positive
    buffer_before: NonNegative = 0
    buffer_after: NonNegative = 0


class CivilNightDuration(Contract):
    kind: Literal["civil_nights"]
    minimum_nights: Positive
    check_in: time
    check_out: time

    @field_validator("check_in", "check_out")
    @classmethod
    def local_minutes(cls, value):
        if value.tzinfo is not None or value.second or value.microsecond:
            raise ValueError("Check-in/out must be local times at minute resolution")
        return value


Duration = Annotated[MinuteDuration | CivilNightDuration, Field(discriminator="kind")]


class Resource(Contract):
    resource_id: Identifier
    kind: Literal["operator", "space", "table", "seat_pool", "room", "equipment"]
    capacity: Positive
    exclusive: bool

    @model_validator(mode="after")
    def exclusive_capacity(self):
        if self.exclusive and self.capacity != 1:
            raise ValueError("An exclusive resource has capacity one")
        return self


class ResourceRequirement(Contract):
    resource_id: Identifier
    quantity: Positive


class AdditionalHandoff(Contract):
    """Additional staff review only; cannot switch mandatory safety rules off."""
    staff_review_required: bool = False
    party_size_above: Positive | None = None


class CatalogService(Contract):
    service_id: Identifier
    name: Annotated[str, Field(min_length=1, max_length=120)]
    duration: Duration
    resources: Annotated[tuple[ResourceRequirement, ...], Field(min_length=1)]
    fields: tuple[AdministrativeField, ...]
    handoff: AdditionalHandoff = AdditionalHandoff()

    @field_validator("name")
    @classmethod
    def display_name(cls, value):
        if not value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("Invalid display name")
        return value

    @model_validator(mode="after")
    def unique_entries(self):
        if len({item.resource_id for item in self.resources}) != len(self.resources):
            raise ValueError("Duplicate resource requirement")
        if len({item.key for item in self.fields}) != len(self.fields):
            raise ValueError("Duplicate administrative field")
        return self


class CatalogDraft(Contract):
    """No tenant, authorization, enable flag, provider token or clinical fields."""
    version: Literal[2]
    revision: Positive
    sector: Sector
    timezone: str
    availability: WeeklyAvailability
    resources: tuple[Resource, ...]
    services: tuple[CatalogService, ...]

    @field_validator("version", mode="before")
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int or value != 2:
            raise ValueError("Unsupported catalog version")
        return value

    @field_validator("timezone")
    @classmethod
    def iana_timezone(cls, value):
        if value in {"localtime", "posixrules"} or value.startswith(("posix/", "right/")):
            raise ValueError("Invalid IANA timezone")
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Invalid IANA timezone") from None
        return value

    @model_validator(mode="after")
    def references_and_sector(self):
        resources = {item.resource_id: item for item in self.resources}
        if len(resources) != len(self.resources):
            raise ValueError("Duplicate resource identifier")
        if len({item.service_id for item in self.services}) != len(self.services):
            raise ValueError("Duplicate service identifier")
        if not self.services:
            raise ValueError("Catalog requires a service")
        for service in self.services:
            stay = isinstance(service.duration, CivilNightDuration)
            if stay != (self.sector == "hotel_bnb"):
                raise ValueError("Duration is incompatible with sector")
            allowed = _STAY if stay else _APPOINTMENT
            if self.sector == "ristorante":
                allowed = allowed | {"party_size"}
            if any(item.key not in allowed for item in service.fields):
                raise ValueError("Administrative field is not allowed for this sector")
            required = {item.key for item in service.fields if item.required}
            minimum = {"customer_name", "arrival_date", "departure_date"} if stay else {
                "customer_name", "appointment_date", "appointment_time",
            }
            if not minimum <= required:
                raise ValueError("Required administrative identity/time fields are missing")
            for requirement in service.resources:
                resource = resources.get(requirement.resource_id)
                if resource is None or requirement.quantity > resource.capacity:
                    raise ValueError("Resource requirement is absent or exceeds capacity")
        return self


@dataclass(frozen=True)
class ScopedCatalog:
    """Binding to trusted server context is NOT an authorization decision."""
    organization_id: UUID
    catalog: CatalogDraft

    def for_organization(self, organization_id: UUID) -> CatalogDraft:
        if type(organization_id) is not UUID or organization_id != self.organization_id:
            raise ValueError("Catalog organization mismatch")
        return self.catalog


def validate_catalog_json(payload: str, *, organization_id: UUID) -> ScopedCatalog:
    """Pure JSON schema validation; caller must first authenticate/authorize scope.

    JSON is accepted deliberately: strict Pydantic permits JSON arrays/dates but
    does not coerce booleans/strings into integers. No client tenant override.
    """
    if type(organization_id) is not UUID:
        raise ValueError("Trusted organization UUID required")
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate configuration key")
            result[key] = value
        return result
    json.loads(payload, object_pairs_hook=unique_keys)
    return ScopedCatalog(organization_id, CatalogDraft.model_validate_json(payload))


@dataclass(frozen=True)
class AvailabilityObservation:
    """Evidence envelope, never a grant to confirm or a replacement for A05.

    The future admission engine must verify freshness, provenance and complete
    interval/resource coverage server-side. A client-created envelope is no proof.
    """
    organization_id: UUID
    catalog_revision: int
    starts_at: datetime
    ends_at: datetime
    evidence: CalendarAvailability

    def __post_init__(self):
        if type(self.organization_id) is not UUID or type(self.catalog_revision) is not int or self.catalog_revision <= 0:
            raise ValueError("Invalid observation scope/revision")
        if (not isinstance(self.starts_at, datetime) or not isinstance(self.ends_at, datetime)
                or self.starts_at.utcoffset() is None or self.ends_at.utcoffset() is None
                or self.starts_at.astimezone(timezone.utc) >= self.ends_at.astimezone(timezone.utc)):
            raise ValueError("Invalid observation interval")
        if not isinstance(self.evidence, CalendarAvailability):
            raise ValueError("Expected typed calendar evidence")


def civil_stay_interval(
    arrival: date, departure: date, duration: CivilNightDuration, timezone_name: str,
) -> tuple[datetime, datetime]:
    """Civil nights are dates, not multiples of 24 hours. Reject DST guesses."""
    if type(arrival) is not date or type(departure) is not date:
        raise ValueError("Civil dates required")
    if (departure - arrival).days < duration.minimum_nights:
        raise ValueError("Stay is shorter than configured minimum")
    start = local_instant(arrival.isoformat(), duration.check_in.isoformat(), timezone_name)
    end = local_instant(departure.isoformat(), duration.check_out.isoformat(), timezone_name)
    if end <= start:
        raise ValueError("Invalid stay interval")
    return start, end

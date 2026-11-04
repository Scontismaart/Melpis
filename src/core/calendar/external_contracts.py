"""Dark contracts for future adapters; no scheduling or provider calls.

CalendarQueue remains the sole Calendar outbox. A remote operation and local
booking cannot be committed atomically. UNKNOWN requires provider-specific
reconciliation; timeouts and expired leases never imply no dispatch.
"""
from dataclasses import dataclass
from enum import Enum
from uuid import UUID


class ExternalMode(str, Enum):
    INTERNAL_ONLY = "internal_only"
    OPTIONAL = "optional"
    AUTHORITATIVE = "authoritative"


class ExternalOutcome(str, Enum):
    NOT_SENT = "not_sent"
    UNKNOWN = "unknown"
    VERIFIED = "verified"


@dataclass(frozen=True, slots=True)
class ExternalOperation:
    organization_id: UUID
    booking_id: UUID
    connection_epoch: UUID
    desired_revision: int
    mode: ExternalMode

    def __post_init__(self):
        if not all(type(value) is UUID for value in (self.organization_id, self.booking_id, self.connection_epoch)):
            raise ValueError("Trusted UUID scope required")
        if type(self.desired_revision) is not int or self.desired_revision < 1 or not isinstance(self.mode, ExternalMode):
            raise ValueError("Versioned operation contract required")

    def permits_dispatch(self, *, current_epoch: UUID, current_revision: int, outcome: ExternalOutcome) -> bool:
        return (self.mode is not ExternalMode.INTERNAL_ONLY and type(current_epoch) is UUID
                and type(current_revision) is int and self.connection_epoch == current_epoch
                and self.desired_revision == current_revision and outcome is ExternalOutcome.NOT_SENT)

    @staticmethod
    def requires_reconciliation(outcome: ExternalOutcome) -> bool:
        return outcome is ExternalOutcome.UNKNOWN

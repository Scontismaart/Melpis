from uuid import uuid4
import pytest
from src.core.calendar.external_contracts import ExternalMode, ExternalOperation, ExternalOutcome


def test_operations_are_revision_and_connection_fenced():
    org, booking, epoch = uuid4(), uuid4(), uuid4()
    operation = ExternalOperation(org, booking, epoch, 3, ExternalMode.AUTHORITATIVE)
    assert operation.permits_dispatch(current_epoch=epoch, current_revision=3, outcome=ExternalOutcome.NOT_SENT)
    assert not operation.permits_dispatch(current_epoch=uuid4(), current_revision=3, outcome=ExternalOutcome.NOT_SENT)
    assert not operation.permits_dispatch(current_epoch=epoch, current_revision=4, outcome=ExternalOutcome.NOT_SENT)
    assert not operation.permits_dispatch(current_epoch=epoch, current_revision=3, outcome=ExternalOutcome.UNKNOWN)
    assert operation.requires_reconciliation(ExternalOutcome.UNKNOWN)
    assert not ExternalOperation(org, booking, epoch, 3, ExternalMode.INTERNAL_ONLY).permits_dispatch(
        current_epoch=epoch, current_revision=3, outcome=ExternalOutcome.NOT_SENT)
    with pytest.raises(ValueError): ExternalOperation(org, booking, epoch, True, ExternalMode.OPTIONAL)

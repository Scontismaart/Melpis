from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.core.gdpr.independent_tip import IndependentTipStore
from src.core.gdpr.journal_publication import LocalJournalStore, SegmentKeyEpoch, publish_segment
from src.core.gdpr.restore_checkpoint import RestoreBlocked
from src.core.gdpr.restore_journal import RestoreJournalInvalid
from src.core.gdpr.source_journal_pages import capture_erasure_journal_delta_paged, publish_source_erasure_batch


class Source:
    def __init__(self, rows):
        self.rows = rows

    def is_in_transaction(self):
        return True

    async def execute(self, query, *args):
        assert "pg_advisory_xact_lock" in query

    async def fetchval(self, query, *args):
        if query == "SHOW transaction_isolation":
            return "read committed"
        if "transaction_read_only" in query:
            return True
        assert "gdpr_operation_steps" in query
        return False

    async def fetch(self, query):
        assert "public.organizations" in query
        return [{"id": org} for org in self.rows]

    async def cursor(self, query, org, *, prefetch):
        assert "ORDER BY e.first_id" in query
        assert prefetch == 128
        for row in self.rows[org]:
            yield row


def entry(org, event):
    record = dict(organization_id=str(org), operation_id=str(uuid4()), scope="organization",
                  subject_id=None, identifier_digest=None, key_id=None)
    row = dict(operation_id=UUID(record["operation_id"]), scope=record["scope"], subject_id=None,
               identifier_digest=None, key_id=None, kind="erase", operation_status="partial",
               operation_scope=record["scope"], operation_subject=None, local_status="confirmed",
               first_id=event, event_count=1)
    return record, row


@pytest.mark.asyncio
async def test_paged_source_reconciles_prior_and_reports_exact_unpublished_backlog(tmp_path):
    org, other = UUID(int=1), UUID(int=2)
    first, first_row = entry(org, 1)
    second, second_row = entry(other, 2)
    third, third_row = entry(org, 3)
    fourth, fourth_row = entry(other, 4)
    installation = uuid4()
    store = LocalJournalStore(tmp_path)
    authority = IndependentTipStore.initialize(tmp_path / "authority.sqlite", installation)
    signer, key = Ed25519PrivateKey.generate(), Fernet.generate_key()
    anchor = authority.current()
    tip = publish_segment(store, installation, anchor, [first], signer=signer, encryption_key=key)
    authority.advance(store, anchor, tip, trusted_public_key=signer.public_key(), encryption_key=key)
    trust = dict(expected_organizations=[org, other], trusted_public_key=signer.public_key(),
                 journal_encryption_key=key, max_entries=2, max_segments=1)
    rows = {org: [first_row, third_row], other: [second_row, fourth_row]}
    assert await capture_erasure_journal_delta_paged(Source(rows), authority, store, **trust) == (
        tip, (second, third), 1)

    del rows[org][0]
    with pytest.raises(RestoreBlocked, match="Published erasures do not match"):
        await capture_erasure_journal_delta_paged(Source(rows), authority, store, **trust)
    rows[org].insert(0, dict(first_row, key_id="unexpected"))
    with pytest.raises(RestoreBlocked, match="Source history disagrees"):
        await capture_erasure_journal_delta_paged(Source(rows), authority, store, **trust)
    rows[org][0] = first_row
    rows[other][-1] = dict(fourth_row, local_status="pending")
    with pytest.raises(RestoreBlocked, match="Source erasure lineage is incomplete"):
        await capture_erasure_journal_delta_paged(Source(rows), authority, store, **trust)
    rows[other][-1] = dict(fourth_row, key_id="invalid")
    with pytest.raises(RestoreBlocked, match="Unpublished source history cannot be signed"):
        await capture_erasure_journal_delta_paged(Source(rows), authority, store, **trust)


@pytest.mark.asyncio
async def test_source_publisher_retries_immutable_segment_and_follows_rotated_epoch(tmp_path):
    class InterruptedStore(LocalJournalStore):
        interrupt = True

        def put_once(self, name, ciphertext):
            super().put_once(name, ciphertext)
            if self.interrupt:
                raise RestoreJournalInvalid("synthetic_upload_response_lost")

    org = uuid4()
    source = Source({org: [entry(org, event)[1] for event in range(1, 4)]})
    authority = IndependentTipStore.initialize(tmp_path / "authority.sqlite", uuid4())
    store = InterruptedStore(tmp_path)
    signer, key = Ed25519PrivateKey.generate(), Fernet.generate_key()
    genesis = authority.current()
    params = dict(expected_organizations=[org], signer=signer, journal_encryption_key=key, max_entries=2)
    with pytest.raises(RestoreJournalInvalid, match="synthetic_upload_response_lost"):
        await publish_source_erasure_batch(source, authority, store, **params)
    assert authority.current() == genesis
    candidate, = tmp_path.glob("*.fernet")
    original_ciphertext = candidate.read_bytes()
    store.interrupt = False
    first, published, remaining = await publish_source_erasure_batch(source, authority, store, **params)
    assert (first.sequence, published, remaining) == (2, 2, 1)
    assert candidate.read_bytes() == original_ciphertext

    next_signer, next_key = Ed25519PrivateKey.generate(), Fernet.generate_key()
    authority.approve_segment_epoch(first, next_signer.public_key(), next_key)
    epochs = (SegmentKeyEpoch(genesis, signer.public_key(), key),
              SegmentKeyEpoch(first, next_signer.public_key(), next_key))
    with pytest.raises(RestoreBlocked, match="does not match the active epoch"):
        await publish_source_erasure_batch(source, authority, store, key_epochs=epochs, **params)
    assert authority.current() == first
    assert list(tmp_path.glob("*.fernet")) == [candidate]

    params.update(signer=next_signer, journal_encryption_key=next_key, key_epochs=epochs)
    # The write path uses its own store; independent authority reads must see
    # the persisted successor through the configured read path.
    reader = LocalJournalStore(tmp_path)
    final, published, remaining = await publish_source_erasure_batch(
        source, authority, store, reader=reader, **params,
    )
    assert (final.sequence, published, remaining) == (3, 1, 0)
    assert await publish_source_erasure_batch(source, authority, store, reader=reader, **params) == (final, 0, 0)

# Independent GDPR recovery journal — operational proposal

This proposal covers Melpis Staging and the scoped beta retention and restore
workflow. Provisioning a signing authority, Cloudflare resources or live
deletion schedules requires separate authorization. No production access is
required.

## Evidence and trust

Existing local recovery artifacts contain encrypted database backups and
DPAPI-protected backup encryption keys. Existing signed restore tests generate
temporary Ed25519, HMAC and encryption keys. The reviewed local configuration
and runbooks provide no evidence of a deployed independent journal signer or
current journal-tip store.

Use a trusted recovery operator outside the application and the PostgreSQL
backup. Provision distinct credentials for each environment and purpose:

- Ed25519 private signing key for the existing erasure-journal segment
  format in `src/core/gdpr/restore_journal.py`. Retain its public verification
  key in the independently trusted recovery configuration.
- A separate HMAC verification/signing key for the cumulative
  `c24-restore-v3` checkpoint, and a separate Fernet key for encrypting the
  checkpoint before storage. Preserve the current checkpoint digest, sequence,
  backup identity and installation identity in protected independent records.
- Keep existing C01 DPAPI backup keys and any existing age backup identities
  dedicated to backup encryption. Protect signing and encryption credentials
  outside the repository, application container, CI logs and restored database.

The operator must retain an encrypted recovery copy of the independent trust
anchors and keys away from the database. Access, rotation and loss recovery
must be tested before enabling any retention execution.

## Storage proposal

Use a private Cloudflare R2 Standard bucket in an already authorized account,
subject to verification of the actual configuration, applicable contracts and
remaining free quota. An existing suitable EU-jurisdiction bucket may be reused;
otherwise obtain approval before creation. A regional location hint alone is
insufficient evidence of EU jurisdiction. Disable public object delivery.

The existing S3-compatible backup tooling is the preferred transport. Use
separate bucket-scoped object credentials for the journal publisher and the
read-only restore verifier; keep bucket-policy administration separate.
Use immutable installation/sequence/digest object names and conditional
create-once writes. Bucket locks may enforce protection against overwrites and
deletion for an approved recovery horizon. The lock administrator remains a
trusted role. Keep the authoritative latest tip outside R2 and the database so
an older but valid artifact cannot establish currency by itself.

Relevant provider references:

- [R2 authentication and bucket-scoped permissions](https://developers.cloudflare.com/r2/api/tokens/)
- [R2 jurisdictional restrictions](https://developers.cloudflare.com/r2/reference/data-location/)
- [R2 conditional writes and consistency](https://developers.cloudflare.com/r2/reference/consistency/)
- [R2 bucket locks](https://developers.cloudflare.com/r2/buckets/bucket-locks/)
- [R2 object lifecycles](https://developers.cloudflare.com/r2/buckets/object-lifecycles/)

## Publication and recovery order

1. The trusted operator obtains current, verified receipt lineage, erasure
   journal evidence, stay-purge evidence, and effective policy/holds from the
   authoritative installation. Unknown historical conclusions are never filled
   by estimation.
2. Authenticate the complete current checkpoint and journal segments, encrypt
   the artifacts and retain them independently. For R2, create an immutable
   object, read it back and verify the ciphertext and authenticated payload.
   Treat ambiguous writes with create-once retry and readback verification.
3. Advance the independently trusted tip only after the retained artifact and
   its complete preceding history are verified. Bind receipt-prune evidence
   using `retain_receipt_prune_checkpoint` and
   `bind_retained_receipt_prune_checkpoint` with the trusted local recovery
   copy. SQL103–104 continue to recheck current policy, holds, source and tenant
   lineage before expiry. Missing, stale, or incomplete evidence defers the
   target without blocking subsequent scheduler targets.
4. On restore, keep the database quarantined. Authenticate the encrypted
   backup, its independently pinned checkpoint and the latest complete
   independent journal. Reconcile newer policy/hold decisions and replay
   required erasure and retention operations. The verifier returns evidence
   with traffic authorization false. A separate authorized operational gate
   controls reopening.

The current v3 checkpoint has bounded cumulative size (10,000 receipt entries
and a 2 MiB payload limit). Establish monitored beta capacity and a signed
rotation/compaction protocol tied to verified backup inventory before these
bounds are approached. No recovery artifact may expire while an authorized
backup still needs it. The applicable backup policy is the approved ordinary
30-day maximum with its separately documented exceptions; it does not assign
an arbitrary new retention period to security evidence. Other unverified
historical records and broader external-copy erasure remain separate gates.

## Cost and activation

R2 Standard currently includes 10 GB-month of storage, one million Class A
requests and ten million Class B requests per month; egress is free. For an
illustrative five tenants with one maximum-2-MiB checkpoint each day and a
30-day rolling history, the checkpoint payload portion alone is approximately
300 MiB and 150 writes per month. Add ciphertext overhead, erasure segments,
retries, verification reads and existing account storage. Confirm available
quota and billing settings before enabling any remote publisher; the free
tier is not a guaranteed cost cap.

Source: [Cloudflare R2 pricing](https://developers.cloudflare.com/r2/pricing/).

Activation prerequisites are explicit approval, verified bucket jurisdiction,
private access and scoped credentials, key custody and recovery, approved
backup/journal retention, signed publication/readback drill, restore/replay
drill, and measured beta capacity. This document is a proposal; it does not
establish a live signer, bucket, credential, or journal.

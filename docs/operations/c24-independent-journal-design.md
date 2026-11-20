# Independent GDPR recovery journal — operational proposal

This proposal covers Melpis Staging and the scoped beta retention and restore
workflow. Provisioning a signing authority, Cloudflare resources or live
deletion schedules requires separate authorization. No production access is
required.

## Local implementation and commissioning boundary

The operator-side candidate lives in `journal_publication.py`,
`independent_tip.py`, `journal_custody.py`, `restore_policy_state.py` and
`restore_coordinator.py`. These modules are not routed from the application.
The PostgreSQL drill uses a local encrypted object store and a separate
authority directory. The R2 adapter is tested against the S3 client contract,
including a lost response followed by authenticated readback.

- Commission one protected `IndependentTipStore` per installation. On POSIX
  use operator-owned mode-0700 directories. On Windows configure equivalent
  ACLs. Keep the SQLite authority off the application host and outside the
  provider bucket and PostgreSQL backups. Use the operator medium with its
  independently attested latest journal tip and checkpoint/policy pins.
  Never reinitialize a missing authority or trust that an old SQLite copy is
  current just because its contents are internally consistent. After each
  publication batch, `seal_for_recovery` produces a consistent encrypted
  authority snapshot and a separate exact recovery pin covering all tips.
  Deliver the latest pin and wrapping key through the operator's independent
  custody path. `recover` verifies them before creating an authority file in
  an empty protected directory on the replacement host. An old sealed copy
  paired with the independently current pin is rejected. An old pin and its
  matching ciphertext cannot establish currency. Retire or isolate the
  previous authority before enabling writes from the replacement.
- Generate distinct Ed25519, HMAC and Fernet keys under independent operator
  control. `JournalKeys` encapsulates the segment and checkpoint keys with
  secret-safe representations. `seal_keys` encrypts the portable bundle with a
  separate recovery key, and `recover_keys` needs that separately held key and
  the expected installation ID. Policy-state authentication/encryption keys
  and backup keys have their own custody. Keep each key generation until all
  recoverable backups that use it are retired.
- Bind each backup manifest to its independently verified identity,
  installation, tenant inventory and exact segment anchor. On the authoritative
  source, use `capture_policy_state` in an owner-controlled read-only
  transaction. Retain it via `publish_policy`. During a frozen source window
  `capture_erasure_journal_delta` compares all source tenant IDs and committed
  local barriers with the retained signed history, rejects gaps or conflicting
  lineage and returns the ordered entries pending publication. Publish verified erasure
  evidence and authenticated v3 checkpoints through
  `publish_segment`/`publish_checkpoint`, then advance only with
  `IndependentTipStore.advance`/`advance_checkpoint`. Each advancement
  verifies the retained object and compares the previous independent pin.
  Publication never uses an object listing as its authority.
- The EU adapter targets only `melpis-staging-gdpr-journal`. Supply a
  TLS-verifying S3 client with the EU account endpoint, region `auto`,
  retry/time-out limits and a bucket-scoped identity. The publisher needs
  Object Read & Write for create-once and readback; the verifier needs Object
  Read only. Bucket administration uses a separate identity. The current
  adapter performs no bucket creation, listing, deletion or credential
  provisioning. Confirm the actual ACL, public delivery state, Standard class,
  usage headroom and protection against object replacement before GO.
- On an isolated restore, use an operator-only single-connection pool with
  PUBLIC and other principal connections disallowed. Freeze publication and
  policy administration for the bounded final reconciliation window, recheck
  the independent latest pins, and run `replay_primary_published_erasures`.
  It reconciles current policy/hold evidence per tenant before local replay,
  requires verified contact fingerprint matching, checks complete replay
  against the supplied backup inventory and pins, and reapplies checkpointed
  receipt-prune work using the current policy and hold checks. Traffic remains
  disabled.
  A hold conflict, missing evidence, conflicting operation or incomplete
  replay is a quarantined failure. Separate checks cover provider copies and
  retention cases outside the current C2.4 contract.

The local rehearsal `python -m scripts.drill_a06_authority_portability`
creates synthetic authority and key artifacts on Windows and recovers them
inside a Linux container with networking disabled. The receiver has only
the sealed artifacts and independently supplied synthetic keys and pin. It
rejects an outdated authority and reads the authenticated journal from the
new authority. The operational drill still has to recover using the real
independently held authority and keys from another host. There is no automatic compaction,
mixed-key journal replay or journal deletion in this candidate. Before reaching
the format bounds, measure the remaining history, enumerate every recoverable
backup and plan the rotation against its verified recovery requirements.
If evidence or its current authority cannot be recovered, keep traffic closed
and obtain the controller's disposition. No deletion rule may rely on a
guessed backup age or an older unverified tip.

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

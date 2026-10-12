# Phase 5 execution policy

- CRM AI tools are disabled in server source (`CRM_AI_TOOLS_ENABLED = False`).
  Environment, client settings and prompt instructions cannot enable them.
  Code remains available for a future caller/record/action authorization design.
  Responder agents and every fallback receive no executable tools.
- A blocked response loses its booking payload. Human escalation prevents booking.
  Live booking requires tenant and message identity, uses the trusted sender, and
  passes through BookingService validation and capacity checks. No TypeError retry.
- Booking responses use persisted state: confirmed, pending, or controlled failure.
  External sync failures require staff intervention, persisted for replay.
  Message-scoped replay resolves an existing booking before capacity checks.
- Inbound and authenticated simulator retain atomic quota reservations. Simulator
  additionally permits at most 20 requests per tenant per minute. Intent is
  heuristic-only in live AI paths; no separate unaccounted classifier call.
- Input is limited to 12,000 characters; responder history to 12 turns of 2,000
  characters each; retrieved context to 12,000 characters. Provider output is
  capped at 1,000 tokens; agent iterations at 3 and agent/provider retries at 1.
  Model attempts are capped at 3; asynchronous attempts share the provider deadline.
- Every attempted provider generation, including failures, reaches the existing
  tenant accounting batch. Unknown usage blocks subsequent AI and current booking.
  Successful attempts are not also counted as an aggregate response event.
  Shadow execution records usage even though it cannot mutate bookings.
- Provider errors expose coarse exception types only. Business profile validation
  does not log raw profile contents. No real provider is needed by regression tests.

No migration, production deployment or Phase 6 work is part of this change.
Merge is permitted only after the required GitHub gates pass and security review approves.

## Historical JWT dependency review (2026-10-06)

`python-jose==3.5.0` has a residual DER public key/HMAC algorithm-confusion flaw
([upstream report](https://github.com/mpdavis/python-jose/issues/414),
[CVE-2026-85394](https://osv.dev/vulnerability/CVE-2026-85394)). No newer PyPI
release was available at that review. The production entry point permitted only
RS256/ES256 in all decode paths, including expired-token refresh; never HS256.
Sixteen regressions constructed genuine DER/HS256 forgeries and required
application rejection. The audit exception matched only that exact version and
advisory. This is historical evidence, not a claim that the library flaw was fixed.

## JWT dependency migration (2026-10-12)

The dedicated migration replaces the runtime dependency with pinned
`PyJWT[crypto]==2.15.1` and removes the python-jose audit exception. The
production verifier binds RSA keys to RS256 and P-256 EC keys to ES256, requires
issuer, audience, expiration and other required claims, and checks signatures
on normal and refresh paths. Expired refresh ignores only the expiration
comparison after a second complete signed validation.

The verifier also requires canonical, unpadded base64url JWT segments. PyJWT
2.15.1 accepts padded encodings for compatibility, while Melpis revocation keys
off the raw serialized token; rejecting alternate spellings preserves exact
token denylist behavior. GitHub Dependabot alert #16 remains pending reevaluation
against the default branch after this PR is merged.

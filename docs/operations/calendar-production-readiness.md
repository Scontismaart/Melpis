# Google Calendar production gate

Google Calendar is disabled by default (`GOOGLE_CALENDAR_ENABLED=false`; the
release preflight requires this explicitly). Production may enable it
only after the operator supplies all of the following assertions and
configuration:

- `GOOGLE_CALENDAR_ENABLED=true`
- `GOOGLE_CALENDAR_OPERATIONAL_APPROVED=true`
- a non-placeholder `GOOGLE_CALENDAR_CERTIFICATION_REF`
- `GOOGLE_CALENDAR_SCHEMA_VERSION=064`
- `GOOGLE_CALENDAR_WORKER_ENABLED=true`
- non-placeholder `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`
- a valid Fernet `ENCRYPTION_KEY`
- `GOOGLE_REDIRECT_URI` using the registered Calendar callback path, HTTPS,
  and the same origin as `PUBLIC_APP_URL`

The checks are local configuration validation. The approval flag and
certification reference are operator assertions; they do not demonstrate that
the C03 certification work actually passed. The schema version is also an
operator assertion and does not prove migration 064 is installed. A separate
runtime database check must verify the actual schema before Calendar becomes
operational. Google Business Profile remains disabled in production.

Neither startup validation nor release preflight contacts Google or reads
tenant credentials.

# C04 signed billing webhook verification — 2026-10-25

## Scope and isolation

Executed the signed webhook, event-ordering, and webhook-handler billing tests against the dedicated synthetic PostgreSQL database `melpis_c04_offline_20261025` on the authorized `melpis-cycle-pg-20261025` container at `127.0.0.1:55495`. The database was absent before this run and was created specifically for this verification. Test setup applied the repository schema and migrations and reset test tables between cases.

No other database or container was used or modified. The container was left running. No real Stripe/Meta/provider call, `.env` loading, or telemetry setup was performed.

## Test result

Python 3.12.5, with pytest plugin autoload disabled and the asyncio plugin explicitly enabled:

- `tests/core/billing/test_signed_webhook_entitlement.py`
- `tests/core/billing/test_webhook_ordering.py`
- `tests/core/billing/test_webhook_handler.py`

Result: **98 passed, 0 failed, 2 warnings, 215.15 seconds**. The two warnings are the existing Stripe SDK deprecation warning at `src/core/billing/routes.py:441` during the signed replay test. That broad PostgreSQL run did not install a suite-wide outbound-socket guard, so it is not evidence of network isolation.

The separate offline billing suite recorded **75 passed**. That run used synthetic events and a non-loopback socket guard, without a database or provider connection. The offline and PostgreSQL counts are reported separately because the selected tests overlap; they are not an additive total.

The new signed-route case verifies a synthetic, HMAC-signed `invoice.paid` event activates only its mapped tenant, preserves another tenant's billing snapshot, and remains idempotent on replay. It blocks non-loopback socket connections. After adding the guard tests, the signed route plus seven parameterized DSN cases passed: **8 passed, 0 failed, 2 warnings, 2.84 seconds**.

## Boundaries

- These results cover only the listed billing tests and synthetic database.
- The signed test accepts only the exact GitHub Actions PostgreSQL fixture DSN when both `CI=true` and `GITHUB_ACTIONS=true`; outside GitHub Actions it accepts only the dedicated loopback C04 database DSN on port 55495. It rejects arbitrary CI, staging, and production DSNs.
- The B02 document quota PostgreSQL suites were not included in this C04 run.

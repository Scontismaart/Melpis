# C24 Calendar refresh fencing — draft

Migration 072 and the associated runtime are draft only. No remote migration,
provider request, authority change, worker activation, or deployment is implied.

Before a refresh HTTP call, the existing credential row stores an opaque dispatch
ID and moves to `reconnect_required`. That committed state means the call may
have reached the provider. Cancellation, process loss, transport timeout and
other ambiguous errors leave it unresolved; elapsed time never permits retry.
An explicit owner OAuth reconnect creates a new connection epoch and credentials.
A successful refresh can clear the marker only with a matching tenant, epoch,
credential version and dispatch ID. Refresh errors and logs do not include tokens.
Disconnect invalidates the epoch and clears the local credentials; it is not a
claim of remote token revocation.

CalendarQueue remains the existing provider outbox. The separate typed adapter
contracts are inert; UNKNOWN needs provider-specific reconciliation. No local
transaction can atomically commit a remote operation and a booking. Synthetic
thread and isolated PostgreSQL tests must pass before a separately reviewed
migration and rollout. Restored database state requires the existing restore
fence, including reconciliation of provider effects after the backup point.

# C2.4 Calendar refresh fencing — draft

The refresh fence uses the credential version and connection epoch introduced
in SQL064. The proposed SQL072 column is unnecessary. No provider request,
remote migration, worker activation or deployment is implied.

Before dispatch, the credential row increments its version and commits
`reconnect_required` with `refresh_outcome_unknown`. Cancellation, process loss,
timeout and other ambiguous errors retain the marker. Elapsed time never
authorizes a second exchange. Explicit OAuth reconnect creates a new epoch.

Success and definitive revocation use the same tenant, epoch, claimed version
and pending-state comparison. The Calendar resource client receives an
access-only credential and cannot refresh through the Google SDK. Disconnect
fences the local credentials; this does not certify remote token revocation.

The existing Calendar outbox remains authoritative for synchronization.
UNKNOWN operations require reconciliation. No local transaction atomically
commits a Google event and a booking. Drain older refresh workers before
enabling the updated runtime: their already dispatched requests are not
retroactively fenced. Restores remain quarantined until provider effects
after the backup point have been reconciled.

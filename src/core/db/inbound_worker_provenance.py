"""Private, tenant-bound evidence produced by the trusted ingestion transaction.

This records provenance only; it grants no booking or worker authority.
"""


async def record_receipt(conn, row, provider_message_id):
    # The receipt is still locked under the live ingestion lease. The provider ID
    # comes from the parsed verified payload, never from an API actor field.
    if not conn.is_in_transaction():
        raise RuntimeError("Provenance requires a transaction")
    match = await conn.fetchrow("""
        SELECT i.id AS inbox_id, m.id AS message_id
          FROM meta_webhook_inbox i JOIN messages m
            ON m.organization_id=i.organization_id AND m.wam_id=$4
         WHERE i.id=$1 AND i.organization_id=$2 AND m.organization_id=$2 AND i.lease_token=$3
           AND i.status='processing' AND i.lease_until > clock_timestamp() AND m.direction='inbound'
           AND m.deleted_at IS NULL
           AND ((i.channel='whatsapp' AND jsonb_path_exists(
                  i.payload, '$.entry[*].changes[*].value.messages[*] ? (@.id == $mid)',
                  jsonb_build_object('mid', to_jsonb($4::text))))
             OR (i.channel='instagram' AND left($4::text, 3)='ig:' AND jsonb_path_exists(
                  i.payload, '$.entry[*].messaging[*].message ? (@.mid == $mid)',
                  jsonb_build_object('mid', to_jsonb(substr($4::text, 4))))))
        FOR SHARE OF i, m
    """, row["id"], row["organization_id"], row["lease_token"], provider_message_id)
    if match:
        await conn.execute("""
            INSERT INTO inbound_worker_receipts (organization_id, inbox_id, message_id)
            VALUES ($1, $2, $3) ON CONFLICT DO NOTHING
        """, row["organization_id"], match["inbox_id"], match["message_id"])


async def record_committed_operation(conn, operation):
    from src.core.billing.quota_reservations import QuotaOperation

    if type(operation) is not QuotaOperation or operation.channel != "inbound":
        raise ValueError("Expected an inbound quota operation")
    if not conn.is_in_transaction():
        raise RuntimeError("Provenance requires a transaction")
    # Called after the A04 ledger and response have committed in this same
    # transaction. Legacy messages without an ingestion receipt get no evidence.
    matches = await conn.fetch("""
        SELECT r.inbox_id, r.message_id, e.id AS operation_id
          FROM inbound_worker_receipts r
          JOIN meta_webhook_inbox i ON i.id=r.inbox_id AND i.organization_id=r.organization_id
          JOIN messages m ON m.id=r.message_id AND m.organization_id=r.organization_id
          JOIN usage_events e ON e.id=$3 AND e.organization_id=r.organization_id
         WHERE r.organization_id=$1 AND i.organization_id=$1 AND m.organization_id=$1
           AND e.organization_id=$1 AND r.message_id=$2
           AND i.status='completed' AND m.direction='inbound' AND m.deleted_at IS NULL
           AND m.billed_at IS NOT NULL AND m.ai_reply_cache IS NOT NULL
           AND e.event_type='system.quota_reservation' AND e.metadata->>'state'='committed'
           AND e.metadata->>'channel'='inbound'
        FOR SHARE OF r, i, m, e
    """, operation.organization_id, operation.source_id, operation.id)
    for match in matches:
        await conn.execute("""
            INSERT INTO inbound_worker_operations
                (organization_id, inbox_id, message_id, operation_id)
            VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING
        """, operation.organization_id, match["inbox_id"], match["message_id"], match["operation_id"])

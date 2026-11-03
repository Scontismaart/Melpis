"""Detect the additive SQL070 boundary without reading optional data."""


async def conversation_schema(conn):
    present = await conn.fetchval("""SELECT ARRAY[
        to_regclass('public.booking_conversation_states') IS NOT NULL,
        to_regclass('public.booking_conversation_requests') IS NOT NULL,
        to_regclass('public.booking_conversation_revocations') IS NOT NULL,
        EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid='public.messages'::regclass
               AND attname='c24_original_subject_id' AND NOT attisdropped),
        EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid='public.simulation_requests'::regclass
               AND attname='c24_subject_id' AND NOT attisdropped),
        EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid='public.simulation_requests'::regclass
               AND attname='c24_conversation_id' AND NOT attisdropped),
        EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid='public.simulation_requests'::regclass
               AND attname='c24_source_message_id' AND NOT attisdropped)]""")
    return 'ready' if all(present) else 'partial' if any(present) else 'absent'

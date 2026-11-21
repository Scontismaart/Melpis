from unittest.mock import MagicMock

import pytest


def test_retention_has_no_legacy_global_repository_entry_points():
    from src.core.db.repositories.conversation_repo import ConversationRepository
    from src.core.db.repositories.message_repo import MessageRepository
    from src.core.db.repository import CoreRepository
    from src.whatsapp.repository import Repository

    for repository in (ConversationRepository, MessageRepository, CoreRepository, Repository):
        for method in (
            "cleanup_empty_conversations",
            "delete_expired_messages",
            "purge_simulation_requests",
            "purge_soft_deleted_messages",
        ):
            assert not hasattr(repository, method), f"{repository.__name__}.{method}"


@pytest.mark.asyncio
async def test_legacy_inbox_purge_cannot_remove_replay_evidence():
    from src.core.db.repositories.webhook_inbox_repo import WebhookInboxRepository

    pool = MagicMock()
    with pytest.raises(RuntimeError, match="scoped C2.4"):
        await WebhookInboxRepository(pool).purge_completed()
    pool.acquire.assert_not_called()

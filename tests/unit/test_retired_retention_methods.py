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

from src.core.conversation_store import ConversationStore


def test_conversation_history_expires_after_idle_ttl(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("src.core.conversation_store.time.monotonic", lambda: now[0])
    store = ConversationStore(ttl_seconds=10)
    store.aggiungi("tenant:session", "Ciao", "Salve")

    now[0] = 109.0
    assert store.recupera_cronologia("tenant:session") == [("Ciao", "Salve")]

    now[0] = 120.0
    assert store.recupera_cronologia("tenant:session") == []
    assert "tenant:session" not in store._tentativi_prenotazione


def test_conversation_history_has_a_hard_memory_bound(monkeypatch):
    now = [1.0]
    monkeypatch.setattr("src.core.conversation_store.time.monotonic", lambda: now[0])
    store = ConversationStore(max_conversations=1)
    store.aggiungi("first", "Ciao", "Salve")
    now[0] += 1
    store.aggiungi("second", "Hello", "Hi")

    assert store.recupera_cronologia("first") == []
    assert store.recupera_cronologia("second") == [("Hello", "Hi")]


def test_erasure_clears_tenant_simulator_cache_without_touching_other_scopes():
    store = ConversationStore()
    store.aggiungi("org-a:staff-a:conversation", "synthetic", "response")
    store.aggiungi("org-b:staff-b:conversation", "other", "response")
    store.aggiungi("demo:session", "anonymous", "response")

    assert store.erase_organization("org-a") == 1
    assert store.recupera_cronologia("org-a:staff-a:conversation") == []
    assert store.recupera_cronologia("org-b:staff-b:conversation") == [("other", "response")]
    assert store.recupera_cronologia("demo:session") == [("anonymous", "response")]

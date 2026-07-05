from mercury_tools.config import Settings
from mercury_tools.db.supabase import SupabaseRagStore


def test_audit_event_redacts_sensitive_output(monkeypatch) -> None:
    captured = {}
    store = SupabaseRagStore(
        Settings(
            supabase_url="https://example.supabase.co",
            supabase_service_role_key="service-role-key",
            openai_api_key="",
        )
    )

    def fake_request(method, path, **kwargs):
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = kwargs["json"][0]
        return [{"id": "audit-1", **captured["payload"]}]

    monkeypatch.setattr(store, "_request", fake_request)

    event = store.record_audit_event(
        {
            "tool_name": "search_knowledge",
            "input": {"access_token": "raw-token"},
            "output_summary": {
                "email": "user@example.com",
                "tax_id": "1234567890123",
            },
            "metadata": {"client_secret": "secret-value"},
        }
    )

    serialized = str(captured["payload"])
    assert captured["method"] == "POST"
    assert captured["path"] == "mcp_audit_events"
    assert "raw-token" not in serialized
    assert "user@example.com" not in serialized
    assert "1234567890123" not in serialized
    assert "secret-value" not in serialized
    assert event["tool_name"] == "search_knowledge"

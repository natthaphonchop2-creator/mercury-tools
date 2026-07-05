from mercury_tools.safety.redaction import redact_json, redact_text


def test_redaction_removes_tokens_emails_and_tax_ids() -> None:
    token = "bearer " + "sk-" + "secretkey123456"
    text = f"client_secret=abc123 email user@example.com tax 1234567890123 {token}"
    redacted = redact_text(text)

    assert "abc123" not in redacted
    assert "user@example.com" not in redacted
    assert "1234567890123" not in redacted
    assert token not in redacted


def test_redaction_masks_secret_keys_in_json() -> None:
    payload = redact_json({"access_token": "secret", "nested": {"email": "a@example.com"}})

    assert payload["access_token"] == "[REDACTED]"
    assert payload["nested"]["email"] == "[REDACTED_EMAIL]"

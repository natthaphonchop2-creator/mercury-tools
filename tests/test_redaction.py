from mercury_tools.safety.redaction import redact_json, redact_text


def test_redaction_removes_tokens_emails_and_tax_ids() -> None:
    token = "bearer " + "sk-" + "secretkey123456"
    mercury_token = "mc_" + "a" * 24 + "." + "b" * 24
    text = f"client_secret=abc123 email user@example.com tax 1234567890123 {token} {mercury_token}"
    redacted = redact_text(text)

    assert "abc123" not in redacted
    assert "user@example.com" not in redacted
    assert "1234567890123" not in redacted
    assert token not in redacted
    assert mercury_token not in redacted


def test_redaction_masks_secret_keys_in_json() -> None:
    payload = redact_json({"access_token": "secret", "nested": {"email": "a@example.com"}})

    assert payload["access_token"] == "[REDACTED]"
    assert payload["nested"]["email"] == "[REDACTED_EMAIL]"


def test_redaction_keeps_connector_schema_field_names() -> None:
    payload = redact_json(
        {
            "required_secret_fields": ["client_id", "client_secret"],
            "token_url": "https://openapi.flowaccount.com/v1/token",
            "access_token": "raw-token",
            "client_secret": "raw-value",
        }
    )

    assert payload["required_secret_fields"] == ["client_id", "client_secret"]
    assert payload["token_url"] == "https://openapi.flowaccount.com/v1/token"
    assert payload["access_token"] == "[REDACTED]"
    assert payload["client_secret"] == "[REDACTED]"

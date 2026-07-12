from base64 import b64encode, urlsafe_b64encode
from urllib.parse import quote, quote_plus

import pytest

from mercury_tools.safety import redaction
from mercury_tools.safety.redaction import (
    redact_credential_text,
    redact_json,
    redact_text,
)


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


@pytest.mark.parametrize(
    "value",
    [
        "Demo Books hidden-secret",
        "Demo Books hidden%252Dsecret",
        "Demo Books aGlkZGVuLXNlY3JldA==",
        "Demo Books aGlkZGVuLXNlY3JldA",
        "Demo Books Basic dmlzaWJsZS1jbGllbnQ6aGlkZGVuLXNlY3JldA==",
        "Demo Books Basic dmlzaWJsZS1jbGllbnQ6aGlkZGVuLXNlY3JldA",
    ],
)
def test_credential_redaction_fails_closed_for_reversible_variants(value: str) -> None:
    assert redact_credential_text(value, ("visible-client", "hidden-secret")) == "[REDACTED]"


def test_credential_redaction_handles_standard_and_urlsafe_base64_variants() -> None:
    credential = "~~~"
    standard = b64encode(credential.encode()).decode()
    urlsafe = urlsafe_b64encode(credential.encode()).decode()

    for value in (standard, standard.rstrip("="), urlsafe, urlsafe.rstrip("=")):
        assert redact_credential_text(f"Demo Books {value}", (credential,)) == "[REDACTED]"


def test_credential_redaction_handles_basic_pairs_with_repeated_values() -> None:
    basic_pair = b64encode(b"same:same").decode()

    assert redact_credential_text(
        f"Demo Books Basic {basic_pair}",
        ("same", "same"),
    ) == "[REDACTED]"


def test_credential_redaction_handles_repeated_mixed_reversible_transformations() -> None:
    credential = "marker:/+?"
    standard = b64encode(credential.encode()).decode()
    double_standard = b64encode(standard.encode()).decode()
    urlsafe = urlsafe_b64encode(credential.encode()).decode().rstrip("=")
    triple_urlsafe = urlsafe_b64encode(
        urlsafe_b64encode(urlsafe.encode()).decode().rstrip("=").encode()
    ).decode().rstrip("=")
    url_then_base64 = b64encode(quote_plus(credential, safe="").encode()).decode()
    base64_then_url = quote(b64encode(credential.encode()).decode(), safe="")

    for representation in (
        double_standard,
        triple_urlsafe,
        url_then_base64,
        base64_then_url,
    ):
        assert redact_credential_text(
            f"Demo Books {representation}",
            (credential,),
        ) == "[REDACTED]"


@pytest.mark.parametrize(
    ("bound_name", "bound_value"),
    [
        ("_MAX_REPRESENTATIONS", 1),
        ("_MAX_REPRESENTATION_BYTES", 4),
    ],
)
def test_credential_redaction_fails_closed_when_sensitive_bounds_are_exceeded(
    monkeypatch: pytest.MonkeyPatch,
    bound_name: str,
    bound_value: int,
) -> None:
    monkeypatch.setattr(redaction, bound_name, bound_value, raising=False)

    assert redact_credential_text("Demo Books", ("secret",)) == "[REDACTED]"


def test_credential_redaction_keeps_safe_text() -> None:
    assert redact_credential_text("Demo Books", ("visible-client", "hidden-secret")) == "Demo Books"

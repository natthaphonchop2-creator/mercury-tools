from base64 import b64encode, urlsafe_b64encode
from urllib.parse import quote, quote_plus

import pytest

from mercury_tools.safety import redaction
from mercury_tools.safety.redaction import (
    redact_absolute_paths,
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
        "Cookie: a=secret; b=secret2",
        "Set-Cookie: session=secret; HttpOnly; Path=/",
        "cookie=a=secret; SameSite=Lax; Secure",
    ],
)
def test_text_redaction_removes_complete_semicolon_cookie_header(value: str) -> None:
    redacted = redact_text(value)

    assert redacted.casefold() in {"cookie=[redacted]", "set-cookie=[redacted]"}
    assert "secret" not in redacted
    assert ";" not in redacted


def test_text_redaction_only_preserves_whole_cookie_placeholder_values() -> None:
    assert redact_text("Cookie: session=<cookie>") == "Cookie: session=<cookie>"

    mixed = redact_text("Cookie: session=<cookie>; private=secret")

    assert mixed == "Cookie=[REDACTED]"
    assert "secret" not in mixed


def test_json_redaction_removes_multi_pair_cookies_and_preserves_safe_placeholder() -> None:
    payload = redact_json(
        {
            "cookie": "a=secret; b=secret2",
            "set-cookie": "session=secret; HttpOnly; Path=/",
            "nested": [
                {"cookie": "session=<cookie>"},
                {"cookie": "session=<cookie>; private=secret"},
            ],
        }
    )

    assert payload == {
        "cookie": "[REDACTED]",
        "set-cookie": "[REDACTED]",
        "nested": [
            {"cookie": "session=<cookie>"},
            {"cookie": "[REDACTED]"},
        ],
    }


@pytest.mark.parametrize(
    "value",
    [
        "Authorization%3A%20Bearer%20opaque-secret",
        "Authorization%253A%2520Bearer%2520opaque-secret",
        "Cookie%3A%20session%3Dopaque-secret%3B%20other%3Dreal-secret",
        "%7B%22client_secret%22%3A%22opaque-secret%22%7D",
        "%257B%2522authorization%2522%253A%2522opaque-secret%2522%257D",
    ],
)
def test_text_redaction_removes_bounded_percent_encoded_sensitive_representations(
    value: str,
) -> None:
    redacted = redact_text(value)

    assert redacted == "[REDACTED]"
    assert "opaque-secret" not in redacted
    assert "real-secret" not in redacted


def test_json_redaction_recognizes_nested_header_descriptor_shapes() -> None:
    payload = redact_json(
        {
            "headers": [
                {"name": "Authorization", "value": "Bearer opaque-auth"},
                {
                    "name": "Cookie",
                    "value": "safe=<cookie>; private=opaque-cookie",
                },
                {
                    "name": "Set-Cookie",
                    "values": ["sid=opaque-session; HttpOnly; Path=/"],
                },
                {"name": "Authorization", "value": "Bearer <token>"},
                {"name": "Cookie", "value": "session=<cookie>"},
                {"name": "Accept", "value": "application/json"},
            ]
        }
    )

    assert payload == {
        "headers": [
            {"name": "Authorization", "value": "[REDACTED]"},
            {"name": "Cookie", "value": "[REDACTED]"},
            {"name": "Set-Cookie", "values": "[REDACTED]"},
            {"name": "Authorization", "value": "Bearer <token>"},
            {"name": "Cookie", "value": "session=<cookie>"},
            {"name": "Accept", "value": "application/json"},
        ]
    }


@pytest.mark.parametrize(
    "value",
    [
        "%2FUsers%2Falice%2Frepo%2Fsecret.env",
        "%252Fopt%252Fapp%252Fsecret",
        "C%3A%5CUsers%5CAlice%5Csecret",
        "%2fhome%2fAlice%2fMiXeD-secret",
        "//Users/alice/repo/secret.env",
        "%2F%2FUsers%2Falice%2Frepo%2Fsecret.env",
        "%252F%252Fopt%252Fapp%252Fsecret",
        "file:///Users/alice/repo/secret.env",
        "file%253A%252F%252F%252Fopt%252Fapp%252Fsecret",
        r"\\server\share\secret.env",
        "%5C%5Cserver%5Cshare%5Csecret.env",
    ],
)
def test_absolute_path_redaction_removes_bounded_percent_encoded_paths(
    value: str,
) -> None:
    redacted = redact_absolute_paths(value)

    assert redacted == "[REDACTED_PATH]"
    assert "secret" not in redacted.casefold()


@pytest.mark.parametrize(
    "value",
    [
        "https://example.test/docs%2Fvat",
        "https://example.test/C%3A%5Cdocs%5Cpublic",
        "mercury://wiki/vat-input-tax",
        "/api/items/{item_id}",
        "docs%2Fvat",
        "docs%2525252Fvat",
    ],
)
def test_absolute_path_redaction_preserves_safe_public_paths(value: str) -> None:
    assert redact_absolute_paths(value) == value


@pytest.mark.parametrize(
    ("bound_name", "bound_value", "value"),
    [
        ("_MAX_PATH_DECODE_DEPTH", 1, "%252Fopt%252Fapp%252Fsecret"),
        (
            "_MAX_ENCODED_PATH_TOKEN_BYTES",
            8,
            "%2FUsers%2Falice%2Frepo%2Fsecret.env",
        ),
    ],
)
def test_absolute_path_redaction_fails_closed_at_decode_bounds(
    monkeypatch: pytest.MonkeyPatch,
    bound_name: str,
    bound_value: int,
    value: str,
) -> None:
    monkeypatch.setattr(redaction, bound_name, bound_value)

    assert redact_absolute_paths(value) == "[REDACTED_PATH]"


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

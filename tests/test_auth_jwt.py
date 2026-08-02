from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from mercury_tools.auth.models import MercuryAuthError
from mercury_tools.auth.supabase_jwt import SupabaseJwtValidator

ISSUER = "https://vbnlkqvauqwnjbxngkas.supabase.co/auth/v1"
AUDIENCE = "https://mercury-tools-mcp.onrender.com/mcp"
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"
SUBJECT = UUID("12345678-1234-5678-9234-567812345678")
CLIENT_ID = "mercury-public-client"


def _keypair(kid: str) -> tuple[rsa.RSAPrivateKey, dict[str, str]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    return private_key, {**public_jwk, "kid": kid, "alg": "RS256", "use": "sig"}


def _token(
    private_key: rsa.RSAPrivateKey,
    *,
    kid: str = "key-1",
    omit_claims: frozenset[str] = frozenset(),
    **overrides: object,
) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": now + timedelta(minutes=5),
        "nbf": now - timedelta(seconds=5),
        "sub": str(SUBJECT),
        "client_id": CLIENT_ID,
        "scope": "openid email profile",
        "jti": "token-id-1",
    }
    claims.update(overrides)
    for claim in omit_claims:
        claims.pop(claim, None)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


def _validator(
    transport: httpx.MockTransport,
    *,
    monotonic: list[float] | None = None,
) -> tuple[SupabaseJwtValidator, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=transport)
    validator = SupabaseJwtValidator(
        issuer=ISSUER,
        audience=AUDIENCE,
        jwks_url=JWKS_URL,
        http_client=client,
        algorithms=("RS256",),
        max_cache_seconds=300,
        monotonic=(lambda: monotonic[0]) if monotonic is not None else None,
    )
    return validator, client


@pytest.mark.asyncio
async def test_valid_asymmetric_token_resolves_mercury_principal() -> None:
    private_key, public_jwk = _keypair("key-1")

    async def jwks(request: httpx.Request) -> httpx.Response:
        assert request.url == JWKS_URL
        return httpx.Response(
            200,
            json={"keys": [public_jwk]},
            headers={"Cache-Control": "public, max-age=120"},
        )

    validator, client = _validator(httpx.MockTransport(jwks))
    try:
        principal = await validator.resolve(_token(private_key))
    finally:
        await client.aclose()

    assert principal.subject == SUBJECT
    assert principal.client_id == CLIENT_ID
    assert principal.scopes == frozenset({"openid", "email", "profile"})
    assert principal.token_id == "token-id-1"
    assert "eyJ" not in repr(principal)


@pytest.mark.asyncio
async def test_token_without_nbf_is_compatible_with_supabase_default() -> None:
    private_key, public_jwk = _keypair("key-1")

    def jwks(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"keys": [public_jwk]})

    validator, client = _validator(httpx.MockTransport(jwks))
    try:
        principal = await validator.resolve(
            _token(private_key, omit_claims=frozenset({"nbf"}))
        )
    finally:
        await client.aclose()

    assert principal.subject == SUBJECT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("claim_overrides", "signing_key", "kid"),
    [
        ({"iss": "https://attacker.example/auth/v1"}, "trusted", "key-1"),
        ({"aud": "https://attacker.example/mcp"}, "trusted", "key-1"),
        ({"aud": [AUDIENCE, "https://attacker.example/mcp"]}, "trusted", "key-1"),
        ({"exp": datetime.now(UTC) - timedelta(seconds=1)}, "trusted", "key-1"),
        ({"nbf": datetime.now(UTC) + timedelta(minutes=5)}, "trusted", "key-1"),
        ({"sub": None}, "trusted", "key-1"),
        ({"client_id": None}, "trusted", "key-1"),
        ({}, "attacker", "key-1"),
    ],
)
async def test_invalid_token_claims_or_signature_fail_closed(
    claim_overrides: dict[str, object],
    signing_key: str,
    kid: str,
) -> None:
    trusted_private, public_jwk = _keypair("key-1")
    attacker_private, _ = _keypair("attacker")

    def jwks(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"keys": [public_jwk]})

    validator, client = _validator(httpx.MockTransport(jwks))
    token = _token(
        trusted_private if signing_key == "trusted" else attacker_private,
        kid=kid,
        **claim_overrides,
    )
    try:
        with pytest.raises(MercuryAuthError) as exc_info:
            await validator.resolve(token)
    finally:
        await client.aclose()

    assert exc_info.value.code == "mercury_token_invalid"
    assert str(exc_info.value) == "mercury_token_invalid"
    assert token not in repr(exc_info.value)


@pytest.mark.asyncio
async def test_unknown_kid_triggers_one_bounded_jwks_refresh() -> None:
    old_private, old_jwk = _keypair("key-old")
    new_private, new_jwk = _keypair("key-new")
    calls = 0

    def jwks(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        keys = [old_jwk] if calls == 1 else [old_jwk, new_jwk]
        return httpx.Response(
            200,
            json={"keys": keys},
            headers={"Cache-Control": "max-age=3600"},
        )

    validator, client = _validator(httpx.MockTransport(jwks))
    try:
        await validator.resolve(_token(old_private, kid="key-old"))
        principal = await validator.resolve(_token(new_private, kid="key-new"))
    finally:
        await client.aclose()

    assert principal.subject == SUBJECT
    assert calls == 2


@pytest.mark.asyncio
async def test_repeated_unknown_kid_does_not_refresh_jwks_per_request() -> None:
    trusted_private, trusted_jwk = _keypair("key-trusted")
    unknown_private, _ = _keypair("key-unknown")
    calls = 0

    def jwks(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"keys": [trusted_jwk]},
            headers={"Cache-Control": "max-age=300"},
        )

    validator, client = _validator(httpx.MockTransport(jwks))
    unknown_token = _token(unknown_private, kid="key-unknown")
    try:
        await validator.resolve(_token(trusted_private, kid="key-trusted"))
        for _ in range(2):
            with pytest.raises(MercuryAuthError):
                await validator.resolve(unknown_token)
    finally:
        await client.aclose()

    assert calls == 2


@pytest.mark.asyncio
async def test_concurrent_unknown_kid_burst_causes_one_forced_refresh() -> None:
    trusted_private, trusted_jwk = _keypair("key-trusted")
    unknown_private, _ = _keypair("key-unknown")
    calls = 0

    async def jwks(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return httpx.Response(
            200,
            json={"keys": [trusted_jwk]},
            headers={"Cache-Control": "max-age=300"},
        )

    validator, client = _validator(httpx.MockTransport(jwks))
    unknown_token = _token(unknown_private, kid="key-unknown")
    try:
        await validator.resolve(_token(trusted_private, kid="key-trusted"))
        results = await asyncio.gather(
            *(validator.resolve(unknown_token) for _ in range(20)),
            return_exceptions=True,
        )
    finally:
        await client.aclose()

    assert all(
        isinstance(result, MercuryAuthError)
        and result.code == "mercury_token_invalid"
        for result in results
    )
    assert calls == 2


@pytest.mark.asyncio
async def test_jwks_cache_honors_http_lifetime_with_hard_maximum() -> None:
    private_key, public_jwk = _keypair("key-1")
    calls = 0
    monotonic = [100.0]

    def jwks(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"keys": [public_jwk]},
            headers={"Cache-Control": "public, max-age=3600"},
        )

    validator, client = _validator(httpx.MockTransport(jwks), monotonic=monotonic)
    try:
        await validator.resolve(_token(private_key))
        monotonic[0] += 299
        await validator.resolve(_token(private_key))
        monotonic[0] += 2
        await validator.resolve(_token(private_key))
    finally:
        await client.aclose()

    assert calls == 2


@pytest.mark.asyncio
async def test_v1_never_accepts_legacy_mc_token() -> None:
    def jwks(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("legacy tokens must be rejected before JWKS fetch")

    validator, client = _validator(httpx.MockTransport(jwks))
    try:
        with pytest.raises(MercuryAuthError) as exc_info:
            await validator.resolve("mc_legacy-token")
    finally:
        await client.aclose()

    assert exc_info.value.code == "mercury_token_invalid"

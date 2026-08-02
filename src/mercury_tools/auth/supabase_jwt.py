"""Asymmetric Supabase JWT validation for Mercury V1."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import httpx
import jwt
from pydantic import ValidationError

from mercury_tools.auth.models import MercuryAuthError, MercuryPrincipal
from mercury_tools.config import Settings

ASYMMETRIC_JWT_ALGORITHMS = frozenset(
    {
        "EdDSA",
        "ES256",
        "ES384",
        "ES512",
        "PS256",
        "PS384",
        "PS512",
        "RS256",
        "RS384",
        "RS512",
    }
)
DEFAULT_JWKS_CACHE_SECONDS = 60
DEFAULT_MAX_JWKS_CACHE_SECONDS = 300
UNKNOWN_KID_REFRESH_COOLDOWN_SECONDS = 5
_MAX_AGE_RE = re.compile(r"(?:^|,)\s*max-age\s*=\s*\"?(\d+)\"?", re.IGNORECASE)
_JWK_TYPES_BY_ALGORITHM = {
    "EdDSA": frozenset({"OKP"}),
    "ES256": frozenset({"EC"}),
    "ES384": frozenset({"EC"}),
    "ES512": frozenset({"EC"}),
    "PS256": frozenset({"RSA"}),
    "PS384": frozenset({"RSA"}),
    "PS512": frozenset({"RSA"}),
    "RS256": frozenset({"RSA"}),
    "RS384": frozenset({"RSA"}),
    "RS512": frozenset({"RSA"}),
}


class SupabaseJwtValidator:
    """Resolve Mercury principals from Supabase-signed asymmetric JWTs."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        http_client: httpx.AsyncClient | None = None,
        algorithms: Sequence[str] = ("RS256", "ES256"),
        max_cache_seconds: int = DEFAULT_MAX_JWKS_CACHE_SECONDS,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        configured_algorithms = tuple(dict.fromkeys(algorithms))
        if (
            not configured_algorithms
            or any(algorithm not in ASYMMETRIC_JWT_ALGORITHMS for algorithm in algorithms)
        ):
            raise ValueError("mercury_jwt_algorithm_invalid")
        if max_cache_seconds < 0:
            raise ValueError("mercury_jwks_cache_invalid")

        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self.jwks_url = jwks_url
        self.algorithms = configured_algorithms
        self.max_cache_seconds = max_cache_seconds
        self._http_client = http_client
        self._monotonic = monotonic or time.monotonic
        self._keys: dict[str, jwt.PyJWK] = {}
        self._cache_expires_at = 0.0
        self._last_unknown_kid_refresh_at = float("-inf")
        self._refresh_lock = asyncio.Lock()

    async def resolve(self, bearer_token: str) -> MercuryPrincipal:
        if (
            not isinstance(bearer_token, str)
            or not bearer_token
            or bearer_token.startswith("mc_")
        ):
            raise MercuryAuthError("mercury_token_invalid")

        try:
            header = jwt.get_unverified_header(bearer_token)
            algorithm = header.get("alg")
            kid = header.get("kid")
            if (
                not isinstance(algorithm, str)
                or algorithm not in self.algorithms
                or not isinstance(kid, str)
                or not kid
                or len(kid) > 512
            ):
                raise MercuryAuthError("mercury_token_invalid")

            key = await self._signing_key(kid)
            if key.algorithm_name != algorithm:
                raise MercuryAuthError("mercury_token_invalid")
            claims = jwt.decode(
                bearer_token,
                key=key.key,
                algorithms=[algorithm],
                audience=self.audience,
                issuer=self.issuer,
                options={
                    "require": ["iss", "aud", "exp", "sub", "client_id"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iss": True,
                    "verify_aud": True,
                },
            )
            if claims.get("aud") != self.audience:
                raise MercuryAuthError("mercury_token_invalid")
            return _principal_from_claims(claims)
        except MercuryAuthError:
            raise
        except (jwt.PyJWTError, TypeError, ValueError, ValidationError):
            raise MercuryAuthError("mercury_token_invalid") from None

    async def _signing_key(self, kid: str) -> jwt.PyJWK:
        if self._monotonic() >= self._cache_expires_at:
            await self._refresh_keys()
        key = self._keys.get(kid)
        if key is not None:
            return key

        await self._refresh_keys(for_unknown_kid=True)
        key = self._keys.get(kid)
        if key is None:
            raise MercuryAuthError("mercury_token_invalid")
        return key

    async def _refresh_keys(self, *, for_unknown_kid: bool = False) -> None:
        async with self._refresh_lock:
            now = self._monotonic()
            if for_unknown_kid:
                if (
                    now - self._last_unknown_kid_refresh_at
                    < UNKNOWN_KID_REFRESH_COOLDOWN_SECONDS
                ):
                    return
                self._last_unknown_kid_refresh_at = now
            elif now < self._cache_expires_at:
                return
            try:
                response = await self._get_jwks()
                response.raise_for_status()
                payload = response.json()
                keys = _parse_jwks(payload, algorithms=self.algorithms)
                cache_seconds = _cache_seconds(
                    response.headers.get("cache-control", ""),
                    hard_maximum=self.max_cache_seconds,
                )
            except (
                httpx.HTTPError,
                jwt.PyJWTError,
                TypeError,
                ValueError,
            ):
                raise MercuryAuthError("mercury_token_invalid") from None

            self._keys = keys
            self._cache_expires_at = self._monotonic() + cache_seconds

    async def _get_jwks(self) -> httpx.Response:
        if self._http_client is not None:
            return await self._http_client.get(
                self.jwks_url,
                headers={"Accept": "application/json"},
            )
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(5.0),
        ) as client:
            return await client.get(
                self.jwks_url,
                headers={"Accept": "application/json"},
            )


def validator_from_settings(settings: Settings) -> SupabaseJwtValidator:
    return SupabaseJwtValidator(
        issuer=settings.supabase_auth_issuer,
        audience=settings.supabase_jwt_audience,
        jwks_url=settings.supabase_jwks_url,
    )


def authorization_server_metadata_url(issuer: str) -> str:
    parsed = urlsplit(issuer.rstrip("/"))
    path = parsed.path.rstrip("/")
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            f"/.well-known/oauth-authorization-server{path}",
            "",
            "",
        )
    )


def _parse_jwks(
    payload: Any,
    *,
    algorithms: Sequence[str],
) -> dict[str, jwt.PyJWK]:
    if not isinstance(payload, Mapping):
        raise ValueError("mercury_jwks_invalid")
    raw_keys = payload.get("keys")
    if not isinstance(raw_keys, list) or not raw_keys:
        raise ValueError("mercury_jwks_invalid")

    keys: dict[str, jwt.PyJWK] = {}
    for raw_key in raw_keys:
        if not isinstance(raw_key, Mapping):
            raise ValueError("mercury_jwks_invalid")
        kid = raw_key.get("kid")
        algorithm = raw_key.get("alg")
        key_type = raw_key.get("kty")
        if (
            not isinstance(kid, str)
            or not kid
            or len(kid) > 512
            or algorithm not in algorithms
            or key_type not in _JWK_TYPES_BY_ALGORITHM[algorithm]
        ):
            continue
        keys[kid] = jwt.PyJWK.from_dict(dict(raw_key), algorithm=algorithm)
    if not keys:
        raise ValueError("mercury_jwks_invalid")
    return keys


def _cache_seconds(cache_control: str, *, hard_maximum: int) -> int:
    if "no-store" in cache_control.lower():
        return 0
    match = _MAX_AGE_RE.search(cache_control)
    if match is None:
        return min(DEFAULT_JWKS_CACHE_SECONDS, hard_maximum)
    return min(int(match.group(1)), hard_maximum)


def _principal_from_claims(claims: Mapping[str, Any]) -> MercuryPrincipal:
    subject = claims.get("sub")
    client_id = claims.get("client_id")
    scope = claims.get("scope", "")
    token_id = claims.get("jti")
    if (
        not isinstance(subject, str)
        or not isinstance(client_id, str)
        or not client_id
        or not isinstance(scope, str)
        or (token_id is not None and not isinstance(token_id, str))
    ):
        raise MercuryAuthError("mercury_token_invalid")
    try:
        return MercuryPrincipal(
            subject=UUID(subject),
            client_id=client_id,
            scopes=frozenset(item for item in scope.split(" ") if item),
            token_id=token_id,
        )
    except (ValueError, ValidationError):
        raise MercuryAuthError("mercury_token_invalid") from None


__all__ = [
    "ASYMMETRIC_JWT_ALGORITHMS",
    "SupabaseJwtValidator",
    "authorization_server_metadata_url",
    "validator_from_settings",
]

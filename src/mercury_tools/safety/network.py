from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit

_METADATA_HOSTS = {
    "instance-data.ec2.internal",
    "metadata",
    "metadata.google.internal",
    "metadata.goog",
}
_NUMERIC_COMPONENT = re.compile(r"^(?:0x[0-9a-f]+|0[0-7]+|[0-9]+)$", re.IGNORECASE)
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")


class NetworkPolicyError(ValueError):
    """A remote specification target failed the import network policy."""


@dataclass(frozen=True)
class ResolvedTarget:
    url: str
    hostname: str
    port: int
    addresses: tuple[str, ...]

    @property
    def origin(self) -> str:
        parsed = urlsplit(self.url)
        default_port = 443 if parsed.scheme.casefold() == "https" else 80
        port = "" if self.port == default_port else f":{self.port}"
        host = f"[{self.hostname}]" if ":" in self.hostname else self.hostname
        return f"{parsed.scheme.casefold()}://{host}{port}"

    @property
    def base_path(self) -> str:
        path = urlsplit(self.url).path.rstrip("/")
        return path or ""

    def verify_peer(self, address: str) -> None:
        try:
            normalized = str(ipaddress.ip_address(address))
        except ValueError:
            raise NetworkPolicyError("remote_peer_unverified") from None
        if normalized not in self.addresses:
            raise NetworkPolicyError("remote_peer_address_mismatch")


class NetworkPolicy:
    """Resolve HTTPS targets and reject every non-global address class."""

    def resolve_https_target(self, url: str) -> ResolvedTarget:
        return self.validate_base_url(url, allow_private_network=False)

    def validate_base_url(
        self,
        url: str,
        allow_private_network: bool = False,
    ) -> ResolvedTarget:
        """Validate and freshly resolve one configured connector base URL."""

        parsed = _parse_url(url, allow_query=False)
        target = self._resolve(url, parsed, allow_private_network=allow_private_network)
        if parsed.scheme.casefold() != "https" and not _all_local_addresses(target.addresses):
            raise NetworkPolicyError("https_required")
        return target

    def validate_request_url(
        self,
        url: str,
        *,
        allowed_hosts: set[str] | frozenset[str],
        allow_private_network: bool = False,
    ) -> ResolvedTarget:
        """Validate and freshly resolve one concrete API or token request URL."""

        parsed = _parse_url(url, allow_query=True)
        hostname = _normalized_hostname(parsed)
        normalized_allowed = {
            host.rstrip(".").casefold()
            for host in allowed_hosts
            if isinstance(host, str) and host.rstrip(".")
        }
        if hostname not in normalized_allowed:
            raise NetworkPolicyError("request_host_not_trusted")
        target = self._resolve(url, parsed, allow_private_network=allow_private_network)
        if parsed.scheme.casefold() != "https" and not _all_local_addresses(target.addresses):
            raise NetworkPolicyError("https_required")
        return target

    def _resolve(
        self,
        url: str,
        parsed: SplitResult,
        *,
        allow_private_network: bool,
    ) -> ResolvedTarget:
        try:
            hostname = parsed.hostname
            port = parsed.port or (443 if parsed.scheme.casefold() == "https" else 80)
        except ValueError:
            raise NetworkPolicyError("invalid_remote_url") from None
        if not hostname:
            raise NetworkPolicyError("remote_hostname_required")

        hostname = hostname.rstrip(".").casefold()
        if hostname in _METADATA_HOSTS:
            raise NetworkPolicyError("metadata_host_forbidden")
        direct = _parse_direct_ip(hostname)
        if direct is not None:
            addresses = (str(direct),)
        else:
            if _is_numeric_alias(hostname):
                raise NetworkPolicyError("numeric_host_alias_forbidden")
            try:
                answers = socket.getaddrinfo(
                    hostname,
                    port,
                    family=socket.AF_UNSPEC,
                    type=socket.SOCK_STREAM,
                )
            except OSError:
                raise NetworkPolicyError("remote_dns_resolution_failed") from None
            addresses = tuple(
                sorted(
                    {
                        str(ipaddress.ip_address(answer[4][0]))
                        for answer in answers
                        if answer[0] in {socket.AF_INET, socket.AF_INET6}
                    }
                )
            )
            if not addresses:
                raise NetworkPolicyError("remote_dns_resolution_empty")

        for address in addresses:
            if not _is_allowed_address(
                ipaddress.ip_address(address),
                allow_private_network=allow_private_network,
            ):
                raise NetworkPolicyError("unsafe_resolved_address")
        return ResolvedTarget(url=url, hostname=hostname, port=port, addresses=addresses)


ValidatedTarget = ResolvedTarget


def _parse_url(url: str, *, allow_query: bool) -> SplitResult:
    if not isinstance(url, str) or not url:
        raise NetworkPolicyError("invalid_remote_url")
    if (
        "\\" in url
        or _INVALID_PERCENT_ESCAPE.search(url) is not None
        or any(character.isspace() or ord(character) <= 0x20 for character in url)
    ):
        raise NetworkPolicyError("invalid_remote_url")
    try:
        parsed = urlsplit(url)
        _ = parsed.port
    except ValueError:
        raise NetworkPolicyError("invalid_remote_url") from None
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise NetworkPolicyError("https_required")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise NetworkPolicyError("remote_userinfo_forbidden")
    if "#" in url or (not allow_query and "?" in url):
        raise NetworkPolicyError("remote_query_or_fragment_forbidden")
    _normalized_hostname(parsed)
    return parsed


def _normalized_hostname(parsed: SplitResult) -> str:
    hostname = parsed.hostname
    if not hostname:
        raise NetworkPolicyError("remote_hostname_required")
    hostname = hostname.rstrip(".").casefold()
    if hostname in _METADATA_HOSTS:
        raise NetworkPolicyError("metadata_host_forbidden")
    return hostname


def _parse_direct_ip(hostname: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(hostname)
    except ValueError:
        return None


def _is_numeric_alias(hostname: str) -> bool:
    parts = hostname.split(".")
    return bool(parts) and all(_NUMERIC_COMPONENT.fullmatch(part) for part in parts)


def _is_global_unicast(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    mapped = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
    candidates = (address, mapped) if mapped is not None else (address,)
    return all(
        candidate.is_global
        and not candidate.is_multicast
        and not candidate.is_unspecified
        and not candidate.is_reserved
        and not candidate.is_loopback
        and not candidate.is_link_local
        and not candidate.is_private
        for candidate in candidates
    )


def _is_allowed_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    *,
    allow_private_network: bool,
) -> bool:
    mapped = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
    candidates = (address, mapped) if mapped is not None else (address,)
    for candidate in candidates:
        if (
            candidate.is_multicast
            or candidate.is_unspecified
            or candidate.is_reserved
            or candidate.is_link_local
        ):
            return False
        if not allow_private_network and not _is_global_unicast(candidate):
            return False
    return True


def _all_local_addresses(addresses: tuple[str, ...]) -> bool:
    if not addresses:
        return False
    parsed = tuple(ipaddress.ip_address(address) for address in addresses)
    return all(address.is_private or address.is_loopback for address in parsed)

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

_METADATA_HOSTS = {
    "instance-data.ec2.internal",
    "metadata",
    "metadata.google.internal",
    "metadata.goog",
}
_NUMERIC_COMPONENT = re.compile(r"^(?:0x[0-9a-f]+|0[0-7]+|[0-9]+)$", re.IGNORECASE)
_SENSITIVE_QUERY_KEY = re.compile(
    r"(?:authorization|credential|secret|token|password|api[_-]?key)",
    re.IGNORECASE,
)


class NetworkPolicyError(ValueError):
    """A remote specification target failed the import network policy."""


@dataclass(frozen=True)
class ResolvedTarget:
    url: str
    hostname: str
    port: int
    addresses: tuple[str, ...]

    def verify_peer(self, address: str) -> None:
        normalized = str(ipaddress.ip_address(address))
        if normalized not in self.addresses:
            raise NetworkPolicyError("remote_peer_address_mismatch")


class NetworkPolicy:
    """Resolve HTTPS targets and reject every non-global address class."""

    def resolve_https_target(self, url: str) -> ResolvedTarget:
        try:
            parsed = urlsplit(url)
            hostname = parsed.hostname
            port = parsed.port or 443
        except ValueError:
            raise NetworkPolicyError("invalid_remote_url") from None
        if parsed.scheme.casefold() != "https":
            raise NetworkPolicyError("https_required")
        if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
            raise NetworkPolicyError("remote_userinfo_forbidden")
        if any(_SENSITIVE_QUERY_KEY.search(key) for key, _ in parse_qsl(parsed.query)):
            raise NetworkPolicyError("remote_credential_query_forbidden")
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
            if not ipaddress.ip_address(address).is_global:
                raise NetworkPolicyError("unsafe_resolved_address")
        return ResolvedTarget(url=url, hostname=hostname, port=port, addresses=addresses)


def _parse_direct_ip(hostname: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(hostname)
    except ValueError:
        return None


def _is_numeric_alias(hostname: str) -> bool:
    parts = hostname.split(".")
    return bool(parts) and all(_NUMERIC_COMPONENT.fullmatch(part) for part in parts)

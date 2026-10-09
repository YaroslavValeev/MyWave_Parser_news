"""SSRF-safe HTTP helpers for outbound fetches of untrusted URLs."""
from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

BLOCKED_NETWORKS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)


class UnsafeURLError(ValueError):
    """Raised when a URL resolves to a blocked/private target."""


def _host_is_blocked(hostname: str, *, resolve_dns: bool = False) -> bool:
    host = (hostname or "").strip().lower().rstrip(".")
    if not host or host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if not resolve_dns:
            return False
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as exc:
            raise UnsafeURLError(f"dns_resolution_failed:{host}") from exc
        for info in infos:
            raw = info[4][0]
            try:
                ip = ipaddress.ip_address(raw)
            except ValueError:
                continue
            if any(ip in network for network in BLOCKED_NETWORKS):
                return True
        return False
    return any(ip in network for network in BLOCKED_NETWORKS)


def assert_public_http_url(url: str, *, allow_http: bool = True, resolve_dns: bool = False) -> str:
    text = str(url or "").strip()
    if not text:
        raise UnsafeURLError("empty_url")
    parsed = urlparse(text)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"https", "http"}:
        raise UnsafeURLError("unsupported_scheme")
    if scheme == "http" and not allow_http:
        raise UnsafeURLError("http_not_allowed")
    hostname = parsed.hostname or ""
    if _host_is_blocked(hostname, resolve_dns=resolve_dns):
        raise UnsafeURLError(f"blocked_host:{hostname or 'empty'}")
    return text


def safe_get(
    url: str,
    *,
    timeout: float = 15.0,
    allow_http: bool = True,
    allow_redirects: bool = True,
    **kwargs: Any,
) -> requests.Response:
    """GET with pre-flight SSRF checks. Redirect targets are re-validated when allowed."""
    safe_url = assert_public_http_url(url, allow_http=allow_http, resolve_dns=True)
    if not allow_redirects:
        return requests.get(safe_url, timeout=timeout, allow_redirects=False, **kwargs)

    response = requests.get(safe_url, timeout=timeout, allow_redirects=False, **kwargs)
    hops = 0
    while response.is_redirect and hops < 5:
        location = response.headers.get("Location") or ""
        next_url = urljoin(response.url, location)
        assert_public_http_url(next_url, allow_http=allow_http, resolve_dns=True)
        response = requests.get(next_url, timeout=timeout, allow_redirects=False, **kwargs)
        hops += 1
    return response


__all__ = [
    "UnsafeURLError",
    "assert_public_http_url",
    "safe_get",
]

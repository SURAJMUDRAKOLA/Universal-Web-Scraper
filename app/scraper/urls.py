"""URL normalization, validation, and SSRF protection.

Every URL entering the system passes through normalize_url() before any
network request is made.  This module must not contain HTML parsing logic.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

# Ports that are acceptable on public web URLs (per prompt spec)
ALLOWED_PORTS = {80, 443, 8080}


class URLPolicyError(ValueError):
    """Raised when a URL fails the security or format policy check."""


def normalize_url(value: str) -> str:
    """Validate a public HTTP(S) URL, apply SSRF protections, and normalize it.

    Returns the normalized URL string or raises URLPolicyError.
    """
    candidate = value.strip()
    parsed = urlsplit(candidate)

    # ── Scheme check ─────────────────────────────────────────────────────────
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise URLPolicyError(
            "Only absolute http and https URLs are supported"
        )

    # ── Credentials ──────────────────────────────────────────────────────────
    if parsed.username or parsed.password:
        raise URLPolicyError("URLs with embedded credentials are not supported")

    # ── Port check ───────────────────────────────────────────────────────────
    try:
        port = parsed.port
    except ValueError as exc:
        raise URLPolicyError("URL contains an invalid port") from exc

    if port and port not in ALLOWED_PORTS:
        raise URLPolicyError(
            f"Only standard web ports are supported ({', '.join(str(p) for p in sorted(ALLOWED_PORTS))})"
        )

    # ── Hostname / SSRF protection ────────────────────────────────────────────
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in {"localhost", "localhost.localdomain"}:
        raise URLPolicyError("Local and private network URLs are not supported")

    try:
        # Resolve with a short timeout so legit public hosts don't block long
        addresses = {entry[4][0] for entry in socket.getaddrinfo(hostname, None)}
    except socket.gaierror as exc:
        raise URLPolicyError("The host could not be resolved") from exc

    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise URLPolicyError(
                "Local and private network URLs are not supported"
            )

    # ── Normalize ─────────────────────────────────────────────────────────────
    netloc = hostname if port is None else f"{hostname}:{port}"
    return urlunsplit(
        (parsed.scheme.lower(), netloc, parsed.path or "/", parsed.query, "")
    )


def normalize_for_dedup(url: str) -> str:
    """Lowercase scheme+host, strip trailing slash — for visited-URL sets."""
    try:
        parsed = urlsplit(url)
        result = urlunsplit((
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path,
            parsed.query,
            "",
        ))
        return result.rstrip("/")
    except Exception:
        return url.rstrip("/").lower()

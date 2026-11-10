"""Optional egress allowlist for URLs Scout opens or fetches.

An empty allowlist preserves the existing behavior (any http/https host). When
configured, exact hosts and their subdomains are permitted and everything else
is refused before the browser is launched.
"""

from __future__ import annotations

from urllib.parse import urlparse


class EgressDeniedError(Exception):
    """Raised when a URL host is outside the configured allowlist."""


def normalize_host(host: str) -> str:
    return host.strip().lower().rstrip(".")


class EgressPolicy:
    """Host allowlist applied to Scout-initiated navigation."""

    def __init__(self, allowlist: list[str] | tuple[str, ...] | None = None) -> None:
        self.allowlist = tuple(
            normalize_host(host) for host in (allowlist or ()) if normalize_host(host)
        )

    @property
    def enabled(self) -> bool:
        return bool(self.allowlist)

    def allows(self, url: str) -> bool:
        if not self.enabled:
            return True
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        host = normalize_host(parsed.hostname or "")
        if not host:
            return False
        return any(host == allowed or host.endswith("." + allowed) for allowed in self.allowlist)

    def check(self, url: str) -> None:
        if not self.allows(url):
            host = urlparse(url).hostname or url
            raise EgressDeniedError(
                f"Egress blocked for '{host}'. Add it to SCOUT_EGRESS_ALLOWLIST to permit it."
            )

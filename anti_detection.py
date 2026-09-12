"""Utility helpers to reduce predictable, bot-like network behaviour.

These helpers are intentionally self-contained and side-effect-free so they
can be unit-tested without any network access and wired into
``ikea_okazje.py`` by a maintainer who has read access to its current
fetch/session/loop code. Nothing in this module talks to the network,
reads environment variables at import time, or holds process-wide mutable
state beyond what is explicitly passed in by the caller.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


# ---------------------------------------------------------------------------
# 1. Jitter for periodic check intervals
# ---------------------------------------------------------------------------

def jittered_interval(
    base_seconds: float,
    jitter_ratio: float = 0.25,
    rng: Optional[random.Random] = None,
) -> float:
    """Return base_seconds randomised by +/- jitter_ratio.

    Example: jittered_interval(600, 0.25) returns a value uniformly drawn
    from [450, 750]. Pass a seeded ``random.Random`` instance in tests for
    determinism; production callers can omit ``rng`` to use the module-level
    default generator.
    """
    if base_seconds <= 0:
        raise ValueError("base_seconds must be positive")
    if not (0 <= jitter_ratio < 1):
        raise ValueError("jitter_ratio must be in [0, 1)")
    r = rng or random
    spread = base_seconds * jitter_ratio
    return r.uniform(base_seconds - spread, base_seconds + spread)


# ---------------------------------------------------------------------------
# 2. TLS/JA3 impersonation profile rotation with matching headers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BrowserProfile:
    """A curl_cffi impersonation target paired with matching HTTP headers."""

    impersonate: str
    user_agent: str
    sec_ch_ua: Optional[str] = None
    sec_ch_ua_platform: Optional[str] = None

    def headers(self) -> Dict[str, str]:
        headers = {"User-Agent": self.user_agent}
        if self.sec_ch_ua:
            headers["sec-ch-ua"] = self.sec_ch_ua
        if self.sec_ch_ua_platform:
            headers["sec-ch-ua-platform"] = self.sec_ch_ua_platform
        return headers


DEFAULT_PROFILES: Tuple[BrowserProfile, ...] = (
    BrowserProfile(
        impersonate="chrome124",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        sec_ch_ua='"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        sec_ch_ua_platform='"Windows"',
    ),
    BrowserProfile(
        impersonate="chrome131",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        sec_ch_ua='"Chromium";v="131", "Google Chrome";v="131", "Not-A.Brand";v="24"',
        sec_ch_ua_platform='"Windows"',
    ),
    BrowserProfile(
        impersonate="safari17_0",
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
        ),
        sec_ch_ua=None,
        sec_ch_ua_platform=None,
    ),
)


def pick_profile(
    profiles: Tuple[BrowserProfile, ...] = DEFAULT_PROFILES,
    rng: Optional[random.Random] = None,
) -> BrowserProfile:
    """Pick one profile at random. Call this once per session/cycle, not per
    request within an existing session, to avoid rotating fingerprints in the
    middle of a cookie-bearing sequence of requests to the same store."""
    if not profiles:
        raise ValueError("profiles must be a non-empty sequence")
    r = rng or random
    return r.choice(profiles)


# ---------------------------------------------------------------------------
# 3. Exponential backoff after repeated failures (e.g. HTTP 403)
# ---------------------------------------------------------------------------

@dataclass
class BackoffState:
    """Tracks consecutive failures for exponential backoff with jitter.

    Not thread-safe by design; use one instance per store/key if you need
    independent backoff tracking (e.g. a dict keyed by store id) or a single
    shared instance for a global backoff policy.
    """

    base_seconds: float = 60.0
    cap_seconds: float = 1800.0
    jitter_seconds: float = 60.0
    _consecutive_failures: int = field(default=0, init=False)

    def record_failure(self, rng: Optional[random.Random] = None) -> float:
        """Register one more failure and return the delay to wait before the
        next attempt."""
        self._consecutive_failures += 1
        return self.current_delay(rng=rng)

    def record_success(self) -> None:
        """Reset the failure streak after a successful fetch."""
        self._consecutive_failures = 0

    def current_delay(self, rng: Optional[random.Random] = None) -> float:
        if self._consecutive_failures <= 0:
            return 0.0
        r = rng or random
        raw = self.base_seconds * (2 ** (self._consecutive_failures - 1))
        capped = min(self.cap_seconds, raw)
        return capped + r.uniform(0, self.jitter_seconds)

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

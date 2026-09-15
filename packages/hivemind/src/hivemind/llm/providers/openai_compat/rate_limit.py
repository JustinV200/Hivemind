"""Parse the OpenAI-compatible wire's rate-limit response headers into a RateLimitSnapshot.

Roadmap step 4.7a ("hosted headroom is measured, not assumed"): `rate_limit_from_headers` is a
sibling of `mapping.py`'s request/response translation, split into its own module purely by
codingrules 5.1's size limit (mapping.py was already at its own limit before this concern existed)
rather than by a difference in responsibility -- it is still wire translation, just for the
response's headers instead of its body, and still the only other file (besides `mapping.py`) where
an OpenAI-compatible wire field name (here, a header name) is allowed to appear. A local server
(Ollama, vLLM, llama.cpp, LM Studio -- this adapter's whole reason to exist) never sends these
headers, so every field on the snapshot this module builds is `None` for one, exactly as
`hivemind.llm.models.RateLimitSnapshot`'s own docstring promises.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.providers.
    openai_compat`. Called by `OpenAICompatProvider.complete` (`provider.py`) once per call, over
    the headers `OpenAICompatClient.post_json_with_headers` (`client.py`) already extracted into a
    plain mapping. Calls into `hivemind.llm.models` only.

Key invariants:
    - Never touches an `httpx.Headers` object: `client.py` already converted the response's
      headers to a plain, lower-cased `Mapping[str, str]` before this module ever sees them, so
      vendor types stay confined to `client.py` (codingrules section 8.6).
    - Returns None, not a RateLimitSnapshot with every field None, when neither remaining-count
      header was present: "not reported this call" is a whole-snapshot fact, not a per-field one,
      matching how `hivemind.llm.providers.anthropic.mapping.rate_limit_from_headers` treats the
      same case.

See Also:
    - .claude/codingrules.md section 5.1 for the size limit this module's split exists to satisfy.
    - .claude/roadmap.md step 4.7a for "hosted headroom is measured, not assumed", verbatim.
    - hivemind.llm.providers.openai_compat.mapping for the request/response wire translation this
      module's own split half of the same responsibility sits beside.
    - hivemind.llm.providers.anthropic.mapping.rate_limit_from_headers for the sibling adapter's
      own version of this same function, over Anthropic's differently-shaped headers.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timedelta

from hivemind.llm.models import RateLimitSnapshot

# The OpenAI-compatible wire's own rate-limit response headers; a local server never sends these.
_REQUESTS_REMAINING_HEADER = "x-ratelimit-remaining-requests"
_TOKENS_REMAINING_HEADER = "x-ratelimit-remaining-tokens"
_REQUESTS_RESET_HEADER = "x-ratelimit-reset-requests"
_TOKENS_RESET_HEADER = "x-ratelimit-reset-tokens"
# The wire's reset headers are a Go-style duration ("6m0s", "1s"), not a timestamp (unlike
# Anthropic's own -reset headers): this pattern pulls out each optional h/m/s component,
# case-insensitively, so a component a particular server omits (most send only whichever units
# are non-zero) simply contributes 0 rather than failing the whole parse. NOTE: sub-second
# durations expressed only in "ms" ("500ms" with no seconds component) are not matched -- these
# two headers' own values are dominated by minutes/seconds in practice, and an unmatched value
# falls back to None (an unmeasured reset) rather than a wrong one.
_DURATION_PATTERN = re.compile(
    r"^(?:(?P<hours>\d+(?:\.\d+)?)h)?(?:(?P<minutes>\d+(?:\.\d+)?)m)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)s)?$",
    re.IGNORECASE,
)

__all__ = ["rate_limit_from_headers"]


def rate_limit_from_headers(headers: Mapping[str, str], now: datetime) -> RateLimitSnapshot | None:
    """Build a RateLimitSnapshot from one response's OpenAI-compatible rate-limit headers.

    Args:
        headers: Every response header from `OpenAICompatClient.post_json_with_headers`,
            lower-cased.
        now: The clock reading to resolve a relative reset duration (e.g. "6m0s") against; injected
            rather than read internally, so this function stays a pure, deterministic mapping of
            its inputs (codingrules section 11's "never a hidden clock" spirit).

    Returns:
        A RateLimitSnapshot with whichever of the four headers were present, or None when neither
        remaining-count header was sent at all -- every local server (Ollama, vLLM, llama.cpp, LM
        Studio) omits all four, so this is the common case.
    """
    requests_remaining = _parse_non_negative_int(headers.get(_REQUESTS_REMAINING_HEADER))
    tokens_remaining = _parse_non_negative_int(headers.get(_TOKENS_REMAINING_HEADER))
    if requests_remaining is None and tokens_remaining is None:
        # Neither headline figure was reported: nothing to build, per the "never invent a number
        # a provider did not report" rule (hivemind.llm.models.RateLimitSnapshot's docstring).
        return None
    return RateLimitSnapshot(
        requests_remaining=requests_remaining,
        tokens_remaining=tokens_remaining,
        requests_reset_at=_parse_reset_duration(headers.get(_REQUESTS_RESET_HEADER), now),
        tokens_reset_at=_parse_reset_duration(headers.get(_TOKENS_RESET_HEADER), now),
    )


def _parse_non_negative_int(raw: str | None) -> int | None:
    """Parse `raw` as a non-negative int, or None when absent or not a plain non-negative number."""
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def _parse_reset_duration(raw: str | None, now: datetime) -> datetime | None:
    """Parse a Go-style duration header ("6m0s") as `now` plus that duration, or None.

    Falls back to reading `raw` as a bare number of seconds first (some OpenAI-compatible
    servers send a plain float rather than the Go duration shorthand this wire is documented to
    use), matching `client.py`'s own `_parse_retry_after`'s "try float first" pattern.
    """
    if raw is None:
        return None
    try:
        return now + timedelta(seconds=float(raw))
    except ValueError:
        pass
    match = _DURATION_PATTERN.match(raw.strip())
    if match is None or not any(match.groups()):
        return None  # An unrecognised shape; not worth failing the whole call over.
    hours, minutes, seconds = (float(g) if g is not None else 0.0 for g in match.groups())
    return now + timedelta(hours=hours, minutes=minutes, seconds=seconds)

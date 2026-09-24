"""Scrub what the flight recorder keeps: credential-looking text, URL parameters, and length.

ADR-0032: redaction happens where the evidence is made. A step's typed secret is already only a
length in `GuiStep.describe()`; this module handles everything else the recorder writes down as
text. `scrub_text` replaces anything shaped like a credential (an API key, a bearer token, a
`password=...` pair, a JWT, a private key block) with a marker and bounds the length; `scrub_url`
masks the value of every query parameter whose name says it carries a secret (`token`, `key`,
`password`, `session`, `code` and their relatives) and drops any user:password in the netloc.
`redact_step` applies the same to a typed GUI step: a secret's text becomes the mask, other typed
text is scrubbed, a URL is masked, and the step keeps its shape so a procedure can be rebuilt;
`describe_step` is the one-line form, which keeps a secret's length and nothing else of it.
Pixels are not scrubbed: browsers mask password fields, and a model never types a secret the plan
did not hand it (ADR-0032).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.recorder`. Called by `recorder.FlightRecorder` and the surface when it
    builds Evidence. Calls into the standard library and waggle (GuiStep) only.

Key invariants:
    - `scrub_text(x)` never returns longer than its `limit`, and never returns a matched secret.
    - `scrub_url` keeps the scheme, host, path and every parameter name, so a reader can still
      see where the page was.

See Also:
    - docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md,
      "Redaction happens where the evidence is made".
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from waggle.messages.capping import GuiStep
from waggle.messages.capping.gui import MAX_GUI_TEXT_CHARS

MASK = "[redacted]"  # What replaces a secret, in text and in a URL parameter's value.
_TRUNCATED = "...[truncated]"
# Credential shapes worth scrubbing wherever they appear: provider API keys, bearer tokens, JWTs,
# key=value pairs whose key names a secret, and PEM private key blocks.
_SECRET_PATTERNS = (
    re.compile(r"\b(sk|pk|rk|ghp|gho|ghs|xox[abprs])[-_][A-Za-z0-9_\-]{12,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-+/=]{12,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
    re.compile(
        r"(?i)\b(password|passwd|secret|token|api[_-]?key|auth)(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)"
    ),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
)
# Query parameter names whose values are secrets, matched case-insensitively as whole names or
# name parts ("access_token", "sessionid", "X-Api-Key").
_SECRET_PARAM = re.compile(r"(?i)(token|key|secret|pass|pwd|session|sid|auth|code|signature|sig)")

__all__ = ["MASK", "describe_step", "redact_step", "scrub_text", "scrub_url"]


def scrub_text(text: str, limit: int) -> str:
    """Replace every credential-shaped run in `text` and bound its length.

    Args:
        text: What would be recorded (an accessibility snapshot, an expected value).
        limit: The most characters to keep; longer text is cut and marked.

    Returns:
        The scrubbed, bounded text.
    """
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(_mask_match, text)
    if len(text) > limit:
        return text[: max(0, limit - len(_TRUNCATED))] + _TRUNCATED
    return text


def scrub_url(url: str) -> str:
    """Mask secret-looking query parameters and any credentials in a URL.

    Args:
        url: A page URL as the browser reported it.

    Returns:
        The same URL with every secret parameter's value, and any user:password, replaced.
    """
    parts = urlsplit(url)
    netloc = parts.netloc.rpartition("@")[2]  # "user:pass@host" keeps only the host.
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    masked = [(name, MASK if _SECRET_PARAM.search(name) else value) for name, value in pairs]
    query = urlencode(masked, safe="[]") if pairs else parts.query
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


def redact_step(step: GuiStep) -> GuiStep:
    """Return `step` safe to record: its secret masked, its text scrubbed, its URL masked.

    Args:
        step: A step as the bee proposed it.

    Returns:
        The same op on the same target; a secret's text is the mask (the step stays `secret`, so
        a procedure rebuilt from it knows to ask for the value), other text is scrubbed and a
        NAVIGATE URL has its secret-looking parameters masked.
    """
    update: dict[str, str] = {}
    if step.text is not None:
        update["text"] = MASK if step.secret else scrub_text(step.text, MAX_GUI_TEXT_CHARS)
    if step.url is not None:
        update["url"] = scrub_url(step.url)
    # model_copy keeps every other field; the replacements satisfy GuiStep's own bounds.
    return step.model_copy(update=update) if update else step


def describe_step(step: GuiStep) -> str:
    """Describe `step` in one line for the recording: a secret as its length, other text scrubbed.

    Args:
        step: A step as the bee proposed it.

    Returns:
        `GuiStep.describe()` of the step itself when it is secret (which already shows only the
        secret's length, the one fact worth keeping), else of its redacted copy.
    """
    return step.describe() if step.secret else redact_step(step).describe()


def _mask_match(match: re.Match[str]) -> str:
    """Keep a key=value pair's key and separator; replace every other match whole."""
    if match.lastindex is not None and match.lastindex >= 3:
        return f"{match.group(1)}{match.group(2)}{MASK}"
    return MASK

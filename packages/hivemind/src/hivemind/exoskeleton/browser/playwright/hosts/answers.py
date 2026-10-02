"""Shape how the host guard answers a request it fetched: the policy header, the re-entry page.

`.requests.HostGuard` fetches a navigation or a script itself and answers the browser with what it
got. Two answers need building. A response the browser will run gets the lease's page policy added
to whatever policy the site already sent (`with_policy`); both are enforced, so the site's own
policy is never loosened. A navigation's redirect to an allowed host is answered with a page that
sends the frame on with `location.replace` (`reentry_page`): a fresh navigation that Playwright's
route sees again, where the redirect itself would be followed unasked, and one that takes the
redirect's place in history, as a followed redirect would (checked on Edge 154 on Windows,
2026-10-02: eight three-hop chains each landed on the final page, one history entry each).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.browser.playwright.hosts`. Called by `.requests`. Calls into the
    standard library only.

Key invariants:
    - `with_policy` never drops or replaces a policy the response already carries.
    - The URL `reentry_page` writes cannot end its script or its attribute, whatever it holds.

See Also:
    - hivemind.exoskeleton.browser.hosts.page_policy for the policy itself.
"""

from __future__ import annotations

import html
import json

POLICY_HEADER = "content-security-policy"  # Lowercase, as Playwright's response headers are.
# The re-entry page is the guard's own: it is never cached, so going back never replays it.
REENTRY_HEADERS = {"content-type": "text/html; charset=utf-8", "cache-control": "no-store"}
# Sends the frame on in place of this page. No referrer, so a token in the redirecting URL is not
# handed to the next host; the refresh is the fallback for a frame that may not run script.
_REENTRY_PAGE = (
    '<!doctype html><meta name="referrer" content="no-referrer">'
    '<noscript><meta http-equiv="refresh" content="0;url={attribute}"></noscript>'
    "<script>location.replace({script})</script>"
)

__all__ = ["POLICY_HEADER", "REENTRY_HEADERS", "reentry_page", "with_policy"]


def with_policy(headers: dict[str, str], policy: str | None) -> dict[str, str]:
    """Return a response's headers with the lease's policy enforced beside any it already has.

    Args:
        headers: The fetched response's headers, lowercase names.
        policy: The lease's page policy; None when every host is allowed.

    Returns:
        A new dict; the policy is joined to an existing one with a comma, which keeps both
        enforced (each is a policy of its own).
    """
    merged = dict(headers)
    if policy is None:
        return merged
    existing = merged.get(POLICY_HEADER)
    merged[POLICY_HEADER] = f"{existing}, {policy}" if existing else policy
    return merged


def reentry_page(target: str) -> str:
    """Return the page that sends a frame on to `target` as a navigation of its own.

    Args:
        target: The absolute URL a redirect led to, already allowed by the lease's hosts.

    Returns:
        A minimal HTML page that replaces itself with `target`.
    """
    # json.dumps quotes it as a script string; "<" is escaped so "</script>" cannot close the tag.
    script = json.dumps(target).replace("<", "\\u003c")
    return _REENTRY_PAGE.format(attribute=html.escape(target, quote=True), script=script)

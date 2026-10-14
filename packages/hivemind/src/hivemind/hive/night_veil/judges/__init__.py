"""Re-export Night Veil's pure judges: what the attestation concludes from text a Cell printed.

Night Veil's checks (roadmap step 5.7b) each run one or more fixed, read-only commands on the Cell
and conclude from the text. The conclusions that need more than "the command exited zero" or "this
word is in its output" live here, one module per question, each a pure function over text so it is
tested without a Cell: `hidden_service` (did the Hive Stand's listener answer, by its HTTP status),
`resolver` (is every DNS resolver Tor's own or behind a tunnel), `hostname` (is the name random, and
the one place its shape is fixed) and `webrtc` (does the browser profile switch WebRTC off). The
simple judges stay beside the commands in `hive.night_veil.session_commands`. This file is the
package's face (codingrules 5.2, 5.4): a caller writes `from hivemind.hive.night_veil.judges import
judge_hostname` without knowing the split.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hive.night_veil`. Called by `hive.night_veil.session_probe`;
    `new_night_veil_hostname` also by the backends that provision a Night Veil Cell. Calls into
    `hivemind.cell.tunnel` (the DNS judge and routing readers shared with Propolis) and
    `hive.night_veil.results`.

Key invariants:
    - This file holds re-exports and __all__ only; the modules behind it define every name.
    - Every judge is pure and total, and records nothing: its `detail` is a fixed word.

See Also:
    - docs/adr/0030-night-veil-retention-and-clearance-boundary.md for why these are looks at an
      image and never configuration of a Cell.
    - hivemind.cell.tunnel for the tunnel judges the resolver judge reuses.

Public API:
    - judge_http_status, WEBSOCKET_PROBE_STATUS: the hidden service's answer (hidden_service).
    - judge_resolver, tor_dns_addresses, DNS_PORT: where the DNS resolver is (resolver).
    - judge_hostname, new_night_veil_hostname, HOSTNAME_PREFIX, SHIPPED_HOSTNAMES: the hostname's
      shape, minted and judged (hostname).
    - judge_webrtc, webrtc_setting, WEBRTC_PREF: whether the browser profile switches WebRTC off
      (webrtc).
"""

from hivemind.hive.night_veil.judges.hidden_service import WEBSOCKET_PROBE_STATUS, judge_http_status
from hivemind.hive.night_veil.judges.hostname import (
    HOSTNAME_PREFIX,
    SHIPPED_HOSTNAMES,
    judge_hostname,
    new_night_veil_hostname,
)
from hivemind.hive.night_veil.judges.resolver import DNS_PORT, judge_resolver, tor_dns_addresses
from hivemind.hive.night_veil.judges.webrtc import WEBRTC_PREF, judge_webrtc, webrtc_setting

__all__ = [
    "DNS_PORT",
    "HOSTNAME_PREFIX",
    "SHIPPED_HOSTNAMES",
    "WEBRTC_PREF",
    "WEBSOCKET_PROBE_STATUS",
    "judge_hostname",
    "judge_http_status",
    "judge_resolver",
    "judge_webrtc",
    "new_night_veil_hostname",
    "tor_dns_addresses",
    "webrtc_setting",
]

"""Talk to the Landing Board from the CLI, as an enrolled device: sign, log in, step up, follow.

The Landing Board is the Hive Entrance's versioned HTTP contract (ADR-0032), and every client of
it is a device enrolled with its own key and approved at the Hive Stand, which logs in with that
key plus the operator's password and signs every request (ADR-0033). The ``hive`` CLI is two such
clients: the Hive Stand's own console (``hive entrance ...``, over the loopback listener) and a
laptop's enrolled key (``hive remote``, ``hive run --remote``, ``hive inbox --remote``). This
package is what both share: the signing (every string built by ``hivemind.entrance.auth``), the
client that logs in and steps up, the WebSocket views authenticated by their first frame, the
transport that verifies TLS, the password prompt, the refusals as sentences, and the options the
commands carry on their context. The session token and the password live in this process's
memory for one command, never on disk and never in output.

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli``. Used by ``hivemind.cli.entrance``,
    ``hivemind.cli.remote``, ``hivemind.cli.run`` and ``hivemind.cli.readback.inbox``. Calls
    into ``hivemind.entrance`` (the canonical strings and the Landing Board's models), ``httpx``,
    ``websockets`` and ``typer``.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - Nothing here logs or prints a token, a password, a key or a signature.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md.
    - docs/entrance/openapi.json for the contract these calls follow.

Public API:
    - LandingClient, LandingSession, SignedIn, signed_in: the device's calls (client).
    - DeviceKey, Credential, OutgoingRequest, Stamp, sign, enrol_signature, login_signature,
      request_headers, first_frame: the signatures (signing).
    - View, ViewClosedError, open_view, FELL_BEHIND, FORBIDDEN, SESSION_ENDED: live views (stream).
    - EntranceAddress, entrance_address, open_http: where the Entrance is and its TLS
      (transport).
    - read_password, read_new_password, PROMPT, NEW_PROMPT, CURRENT_PROMPT: the password
      (password).
    - LandingError, LandingRefusedError, EntranceUnreachableError, LandingProtocolError: failures
      (errors).
    - CarriedOption, carried_command, carried_group, carried_flag, carried_text, carried_path,
      PASSWORD_STDIN: options carried on the context (options).
"""

from hivemind.cli.landing.client import LandingClient, LandingSession, SignedIn, signed_in
from hivemind.cli.landing.errors import (
    EntranceUnreachableError,
    LandingError,
    LandingProtocolError,
    LandingRefusedError,
)
from hivemind.cli.landing.options import (
    PASSWORD_STDIN,
    CarriedOption,
    carried_command,
    carried_flag,
    carried_group,
    carried_path,
    carried_text,
)
from hivemind.cli.landing.password import (
    CURRENT_PROMPT,
    NEW_PROMPT,
    PROMPT,
    read_new_password,
    read_password,
)
from hivemind.cli.landing.signing import (
    Credential,
    DeviceKey,
    OutgoingRequest,
    Stamp,
    enrol_signature,
    first_frame,
    login_signature,
    request_headers,
    sign,
)
from hivemind.cli.landing.stream import (
    FELL_BEHIND,
    FORBIDDEN,
    SESSION_ENDED,
    View,
    ViewClosedError,
    open_view,
)
from hivemind.cli.landing.transport import EntranceAddress, entrance_address, open_http

__all__ = [
    "CURRENT_PROMPT",
    "FELL_BEHIND",
    "FORBIDDEN",
    "NEW_PROMPT",
    "PASSWORD_STDIN",
    "PROMPT",
    "SESSION_ENDED",
    "CarriedOption",
    "Credential",
    "DeviceKey",
    "EntranceAddress",
    "EntranceUnreachableError",
    "LandingClient",
    "LandingError",
    "LandingProtocolError",
    "LandingRefusedError",
    "LandingSession",
    "OutgoingRequest",
    "SignedIn",
    "Stamp",
    "View",
    "ViewClosedError",
    "carried_command",
    "carried_flag",
    "carried_group",
    "carried_path",
    "carried_text",
    "enrol_signature",
    "entrance_address",
    "first_frame",
    "login_signature",
    "open_http",
    "open_view",
    "read_new_password",
    "read_password",
    "request_headers",
    "sign",
    "signed_in",
]

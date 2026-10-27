"""`hive abscond`: tear the whole Hive down, human-only and break-glass (roadmap step 13.4).

One command (`command`) over four parts: what would go, read from backend labels and the trail
alone (`scope`); the human's re-auth, the `absconding` point and the typed phrase (`consent`); the
scope review and the Leavings the human ticks (`review`); and the teardown with its
`guard.absconded` record (`execute`), built on roadmap step 5.13's Cells-and-leases pass.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli`. Registered as `hive
    abscond` by `hivemind.cli.app`. Calls into its own modules only.

Key invariants:
    - This file holds re-exports and __all__ only.

See Also:
    - docs/runbooks/absconding.md for the operator's walk-through.

Public API:
    - abscond_command: the command (command).
    - AbscondScope, ScopeSources, gather_scope: what would go (scope).
    - Evidence, authorize_abscond, phrase_matches, reauthenticate, PHRASE: consent (consent).
    - AbscondReceipt, carry_out, ABSCONDED_KIND: the teardown (execute).
"""

from hivemind.cli.abscond.command import abscond_command
from hivemind.cli.abscond.consent import (
    PHRASE,
    Evidence,
    authorize_abscond,
    phrase_matches,
    reauthenticate,
)
from hivemind.cli.abscond.execute import ABSCONDED_KIND, AbscondReceipt, carry_out
from hivemind.cli.abscond.scope import AbscondScope, ScopeSources, gather_scope

__all__ = [
    "ABSCONDED_KIND",
    "PHRASE",
    "AbscondReceipt",
    "AbscondScope",
    "Evidence",
    "ScopeSources",
    "abscond_command",
    "authorize_abscond",
    "carry_out",
    "gather_scope",
    "phrase_matches",
    "reauthenticate",
]

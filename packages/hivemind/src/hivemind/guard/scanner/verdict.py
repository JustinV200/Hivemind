"""Define ScanSource, ScanAction and ScanVerdict: where outside text came from and what was decided.

The untrusted-content scanner (roadmap step 10.6b, ADR-0035) reads outside text before a model
does: a tool's result, a Cell session's own output, a human's chat message, and (phase 7) a Honey
hit or a Nectar deposit. `ScanSource` names those entry points as a closed set, so the trail and
the Guard Bee (the Hive's security watcher, roadmap 10.6) can say where a flag came from without
carrying the text. `ScanAction` is the three-way verdict a score maps to per Comb Shield tier
(the Cell's security tier): pass it on unchanged, label it harder, or drop it. `ScanVerdict` is the
one value that crosses from the Guard to whoever renders the text into a prompt
(`hivemind.memory.render_untrusted`): the action, the score, which pattern families fired, how much
of the input was read when it was cut at the scanner's bound, and the keyed hash that stands in
for the text on the trail. It never carries the text itself.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.guard.scanner`.
    Produced by `hivemind.guard.scanner.scanner.ContentScanner.scan`; read by `hivemind.memory`
    (assembly applies the action), `hivemind.workers.tools` (a tool result's action) and the Queen
    (a chat message's action). Calls into pydantic only.

Key invariants:
    - A PASS verdict carries no content hash, and a LABEL or DROP verdict always carries one: a
      flag is always recorded with the hash, and nothing is hashed that was not flagged.
    - `scanned_chars` is set exactly when the text was cut at the scanner's bound; every renderer
      shows a model only that head, never the unscanned rest.
    - `families` is sorted and holds only family names, never a matched fragment of the text.
    - `ScanSource` values are stable once shipped: they are recorded on the trail.

See Also:
    - docs/adr/0035-guard-bee-requests-queen-only-isolation-and-tainted-memory.md for the scanner.
    - hivemind.guard.scanner.scanner for ContentScanner, the producer.
    - docs/guard/untrusted-content.md for what each family and action means.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_FAMILY_NAME_CHARS = 40  # A family is a short snake_case table name ("secret_exfiltration").
MAX_FAMILIES = 32  # Far more than the six shipped families; bounds what one verdict can carry.
HASH_PREFIX = "hmac-sha256:"  # Names the keyed hash's scheme, so a later scheme cannot be confused.
MAX_HASH_CHARS = 128  # The prefix plus 64 hex characters, with room to spare.

__all__ = [
    "HASH_PREFIX",
    "MAX_FAMILIES",
    "MAX_FAMILY_NAME_CHARS",
    "MAX_HASH_CHARS",
    "ScanAction",
    "ScanSource",
    "ScanVerdict",
]

# One fired family's name, as the pattern file spells its table.
_FamilyName = Annotated[str, Field(min_length=1, max_length=MAX_FAMILY_NAME_CHARS)]


class ScanSource(Enum):
    """Where one scanned text entered the Hive on its way to a prompt."""

    TOOL_RESULT = "tool_result"  # A tool's result reaching a Worker's model (roadmap 3.16).
    SESSION_OUTPUT = "session_output"  # A Cell session's own output: a command's, a file's.
    LANDING_BOARD = "landing_board"  # A human's chat words from an enrolled device (step 10.5).
    HONEY_HIT = "honey_hit"  # Seam: phase 7 retrieval (7.7) scans each hit before assembly.
    NECTAR_INTAKE = "nectar_intake"  # Seam: phase 7 intake (7.4) scans each deposit on arrival.


class ScanAction(Enum):
    """What happens to a scanned text, from its score and the Cell's tier thresholds."""

    PASS = "pass"  # noqa: S105 (a verdict, not a credential). Below label: shown as it is.
    LABEL = "label"  # At or above label: reaches the model inside a harder, flagged label.
    DROP = "drop"  # At or above drop: never reaches the model; a withheld notice stands in.


class ScanVerdict(BaseModel):
    """One scan's decision about one text: never the text, only what was found and decided.

    Crosses from the Guard to every renderer of outside text (`hivemind.memory.render_untrusted`)
    and rides on a `hivemind.memory.UntrustedText` into an assembled prompt.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: ScanAction = Field(description="Pass, label harder, or drop.")
    score: float = Field(ge=0, description="The summed weights of every family that fired.")
    families: tuple[_FamilyName, ...] = Field(
        default=(),
        max_length=MAX_FAMILIES,
        description="The names of the pattern families that fired, sorted; never a fragment.",
    )
    scanned_chars: int | None = Field(
        default=None,
        ge=0,
        description="How many leading characters were matched when the text was longer than the "
        "scanner's bound ([guard.untrusted_content] max_scan_chars); None when it was read whole. "
        "Nothing past it may reach a model: it was never scanned.",
    )
    content_hash: str | None = Field(
        default=None,
        max_length=MAX_HASH_CHARS,
        description="The keyed hash (HMAC-SHA256 under the node's scanner key) of the whole text, "
        "set exactly when the text was flagged; it cannot confirm a guess without the key.",
    )

    @property
    def flagged(self) -> bool:
        """Whether this verdict labels or drops its text (anything but PASS)."""
        return self.action is not ScanAction.PASS

    @property
    def truncated(self) -> bool:
        """Whether the text was longer than the scanner's bound, so only its head was matched."""
        return self.scanned_chars is not None

    @model_validator(mode="after")
    def _hash_exactly_when_flagged(self) -> ScanVerdict:
        """Refuse a flagged verdict with no hash, or a passing one that hashed its text."""
        if self.flagged != (self.content_hash is not None):
            raise ValueError(
                f"a {self.action.value} verdict must "
                f"{'carry' if self.flagged else 'not carry'} a content hash."
            )
        if self.content_hash is not None and not self.content_hash.startswith(HASH_PREFIX):
            raise ValueError(f"content_hash must start with {HASH_PREFIX!r}.")
        return self

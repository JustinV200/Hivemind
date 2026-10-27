"""Define the ``[nurse]`` section: whether the Nurse Bee reviews attempts, and its bounds.

The Nurse Bee (ADR-0061) is a fresh-context reviewer: once a Warden's deterministic acceptance
checks pass on an attempt, a model that never saw the Worker's conversation reads Warden-built
evidence and says PASS or what to fix. ``NurseSection`` is what the operator may tune about it:
whether it runs at all, how many review rounds a task gets before the Queen hands it to a human,
which Worker roles are never reviewed, and how much file text one review may read. There is
deliberately no per-tempo opt-out: a hurried task is reviewed like any other.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Embedded by
    ``hivemind.manifest.schema.manifest.HiveManifest``; converted by the composition root
    (``hivemind.cli.compose.deps.warden``) into the Warden's ``hivemind.wardens.nurse.NurseWiring``,
    and read by the Queen's round count (ADR-0061's loop). Calls into ``waggle.messages.task``
    (WorkerRole, for the role-name validator) only.

Key invariants:
    - Frozen and forbids unknown fields (codingrules section 8.5).
    - ``max_evidence_chars`` never exceeds ``MAX_EVIDENCE_CHARS``, a mirror of
      ``hivemind.supervision.capping.checks.nurse.MAX_NURSE_EVIDENCE_CHARS`` (this Layer 1 module
      cannot import Layer 2); a test holds the two in step.
    - ``max_rounds`` never exceeds ``MAX_ROUNDS``, a mirror of the wire's
      ``waggle.messages.task.review.MAX_REVIEW_ROUND``, so a round the Queen sends always
      validates.
    - Every ``skip_roles`` entry is a ``WorkerRole`` member name; an unknown one is refused.

See Also:
    - docs/adr/0061-the-nurse-bee-reviews-an-attempt-in-a-fresh-context.md for the decision.
    - docs/manifests/full.toml for the annotated example.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from waggle.messages.task import WorkerRole
from waggle.messages.task.review import MAX_REVIEW_ROUND

MAX_EVIDENCE_CHARS = 100_000  # Mirrors checks.nurse.MAX_NURSE_EVIDENCE_CHARS: the hard ceiling.
# About eight thousand tokens of file text: room to read the files an attempt touched, small
# enough that a mid-sized local model can still take the prompt and its reply.
DEFAULT_MAX_EVIDENCE_CHARS = 32_000
DEFAULT_MAX_ROUNDS = 2  # Two fix-forward rounds, then a human: Justin's default (ADR-0061).
MAX_ROUNDS = MAX_REVIEW_ROUND  # A higher cap could send a round the wire refuses.
# Roles whose work is housekeeping, not a deliverable an objective can be judged against.
DEFAULT_SKIP_ROLES = ("UNDERTAKER", "HOUSE_BEE")

__all__ = [
    "DEFAULT_MAX_EVIDENCE_CHARS",
    "DEFAULT_MAX_ROUNDS",
    "DEFAULT_SKIP_ROLES",
    "MAX_EVIDENCE_CHARS",
    "MAX_ROUNDS",
    "NurseSection",
]


class NurseSection(BaseModel):
    """``[nurse]``: whether the Nurse Bee runs, its round cap, skipped roles and evidence budget."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = Field(
        default=True, description="Whether a Warden asks a Nurse to review an accepted attempt."
    )
    max_rounds: int = Field(
        default=DEFAULT_MAX_ROUNDS,
        ge=1,
        le=MAX_ROUNDS,
        description="Times a Nurse rejection sends a task back to fix forward; the rejection "
        "after the last of them goes to a human.",
    )
    skip_roles: tuple[str, ...] = Field(
        default=DEFAULT_SKIP_ROLES,
        description="WorkerRole names whose attempts are never reviewed; SCOUT is reviewed.",
    )
    max_evidence_chars: int = Field(
        default=DEFAULT_MAX_EVIDENCE_CHARS,
        ge=0,
        le=MAX_EVIDENCE_CHARS,
        description="Characters of touched-file text one review may read, across every file.",
    )

    @field_validator("skip_roles")
    @classmethod
    def _known_roles(cls, roles: tuple[str, ...]) -> tuple[str, ...]:
        """Refuse a role name that is not a WorkerRole member, so a typo never skips nothing."""
        known = {role.value for role in WorkerRole}
        unknown = sorted(set(roles) - known)
        if unknown:
            raise ValueError(f"[nurse] skip_roles names unknown roles {unknown}; known: {known}.")
        return roles

    def skipped(self) -> frozenset[WorkerRole]:
        """Return `skip_roles` as WorkerRole members."""
        return frozenset(WorkerRole(name) for name in self.skip_roles)

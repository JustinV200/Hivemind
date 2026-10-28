"""Check the two data tables a Hive cannot start without: the escalation policy and Forage.

A manifest can validate and still name an escalation policy file that is missing, malformed or
hands a Warden a lever only the Queen holds; `hive run` would then stop at its first Warden. The
policy check loads it exactly as a Warden does (`load_warden_policy`, which also runs the Queen's
`load_policy`), from the path the composition root would resolve. Forage sanity is the other half:
every chat binding in `[llm.slots]` needs a `[forage.map]` row so a grant can price and grade its
calls. A missing row is a warning, not a failure, because the Hive still runs, with that model
unpriced; the embedder and the transcriber are left out, since no grant prices them by the map.

Fits into the Hive:
    Layer 7 (the terminal), inside `hivemind.cli.doctor`. Called by
    `hivemind.cli.doctor.command`. Calls into `hivemind.supervision` (the policy loader),
    `hivemind.cli.compose.deps` (the path rule) and `hivemind.manifest`.

Key invariants:
    - Read-only, and every failure becomes a `Check`; nothing here raises.
    - The policy path is resolved by the same function `hive run` uses, so the doctor checks the
      file the Hive would read.

See Also:
    - hivemind.supervision.policy for what a valid policy is.
"""

from __future__ import annotations

from hivemind.cli.compose.deps.parts import supervision_file
from hivemind.cli.doctor.report import Check, Outcome
from hivemind.forage import ModelSlot
from hivemind.manifest import HiveManifest
from hivemind.supervision import PolicyError, load_warden_policy

# Bindings no grant prices by the Forage map: embeddings and audio are not metered per token there.
_UNPRICED_SLOTS = frozenset({ModelSlot.EMBEDDER.manifest_key, ModelSlot.TRANSCRIBER.manifest_key})

__all__ = ["check_escalation_policy", "check_forage"]


def check_escalation_policy(manifest: HiveManifest) -> Check:
    """Check `[supervision] policy_file` (or the shipped default) loads as a Warden loads it."""
    name = "escalation policy"
    path = supervision_file(manifest, manifest.supervision.policy_file)
    try:
        policy = load_warden_policy(path)
    except PolicyError as exc:
        return Check(name=name, outcome=Outcome.FAILED, detail=str(exc))
    source = str(path) if path is not None else "the shipped default"
    return Check(name=name, outcome=Outcome.PASSED, detail=f"{source}, {len(policy.rules)} rules")


def check_forage(manifest: HiveManifest) -> Check:
    """Check every chat binding has a `[forage.map]` row to price and grade it."""
    name = "forage"
    mapped = {(spec.provider, spec.model) for spec in manifest.forage.map.values()}
    # Each [llm.slots] row a grant may bill, fallbacks included, named with what it binds.
    unmapped = [
        f"{key} ({binding.provider}/{binding.model})"
        for key, binding in manifest.llm.slots.items()
        if key not in _UNPRICED_SLOTS and (binding.provider, binding.model) not in mapped
    ]
    if unmapped:
        detail = f"no [forage.map] row prices or grades: {', '.join(unmapped)}"
        return Check(name=name, outcome=Outcome.WARNED, detail=detail)
    detail = f"[forage.map] prices every chat binding ({len(manifest.forage.map)} rows)"
    return Check(name=name, outcome=Outcome.PASSED, detail=detail)

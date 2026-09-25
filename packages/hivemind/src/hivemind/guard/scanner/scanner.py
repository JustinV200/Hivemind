"""Scan outside text before a model reads it and record every flag: the effectful edge.

`ContentScanner.scan` is what every entry point for outside text calls (roadmap step 10.6b,
ADR-0043): a Worker's tool results and session output, the Queen's chat messages, and (phase 7)
Honey hits at assembly and Nectar at intake. It scores the text with the pure `score_text`, maps
the score to PASS, LABEL or DROP for the reading bee's Comb Shield tier (the Cell's security tier),
and for anything flagged records `guard.injection_suspected` on the reading bee's own trail before
returning: the source, the consuming bee, the tier, the score, the families that fired and a keyed
hash of the text (`ContentHasher`), never the text itself. This is the only producer of the
injection signal the Guard Bee (the Hive's security watcher, roadmap 10.6) correlates with
denials. A flag never stops the bee: the verdict tells the caller how to render the text (label it
harder, or withhold it), and nothing here refuses, raises or narrows anything on a flag.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.guard.scanner`. Built
    by a composition root (`hivemind.cli.compose.hive` with `[guard.untrusted_content]` and the
    Hive's file secret store) or by `default_content_scanner` (the shipped patterns and thresholds,
    an in-memory key); held by `QueenDeps`, `WardenDeps` and `WorkerContext`. Calls into
    `hivemind.cell` (CombShieldLevel, CellIdentity), `hivemind.common.secrets`,
    `hivemind.guard.capabilities`, `hivemind.manifest.schema.security.guard`, `hivemind.pheromone`
    (GuardEvent) and this package's `hasher`, `patterns`, `score` and `verdict`.

Key invariants:
    - A flagged verdict's event is on the trail before `scan` returns it; a PASS writes nothing.
    - The event's payload carries ids, counts, family names and the keyed hash only; the trail's
      own validator refuses a `text`, `content` or `output` key regardless (codingrules 12).
    - The event lands on the site's own trail: on a Night Veil Cell that is the Cell's own
      ephemeral segment, purged at teardown (codingrules 12), never the Queen's surviving trail.

See Also:
    - docs/adr/0043-guard-bee-requests-queen-only-isolation-and-tainted-memory.md.
    - hivemind.guard.scanner.score for the pure decision this wraps.
    - hivemind.memory.render_untrusted for how a verdict is applied to a prompt.
    - docs/guard/untrusted-content.md for the families, the thresholds and the event.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import JsonValue

from hivemind.cell import CellIdentity, CombShieldLevel
from hivemind.common.secrets import MemorySecretStore
from hivemind.guard.capabilities import CapabilitySet
from hivemind.guard.scanner.hasher import ContentHasher
from hivemind.guard.scanner.patterns import CompiledPatterns, load_scan_patterns
from hivemind.guard.scanner.score import ScanScore, decide, score_text, thresholds_for
from hivemind.guard.scanner.verdict import ScanAction, ScanSource, ScanVerdict
from hivemind.manifest.schema.security.guard import UntrustedContentSection
from hivemind.pheromone import GuardEvent, PheromoneTrail
from waggle.clock import Clock
from waggle.ids import TaskId, new_event_id

INJECTION_SUSPECTED_KIND = "guard.injection_suspected"  # The one kind this module records.
MAX_REF_CHARS = 128  # A site reference is an id or a tool name, never prose.
_SCORE_DIGITS = 3  # Scores on the trail are rounded: the weights are small decimals.

__all__ = [
    "INJECTION_SUSPECTED_KIND",
    "MAX_REF_CHARS",
    "ContentScanner",
    "ScanRecorder",
    "ScanSite",
    "default_content_scanner",
]


@dataclass(frozen=True, slots=True)
class ScanRecorder:
    """Where a flag is recorded: the reading bee's own trail, identity and clock."""

    trail: PheromoneTrail  # The reading bee's own trail segment.
    identity: CellIdentity  # The Hive, node and actor the event is stamped with.
    clock: Clock  # Mints the event's id and timestamp.


@dataclass(frozen=True, slots=True)
class ScanSite:
    """Everything about where one text is being read, and nothing of the text itself.

    Attributes:
        source: Which entry point the text came through.
        consumer: The reading bee's own principal id (a worker_, warden_ or hive_ id): the
            event's subject.
        recorder: Where a flag is recorded.
        tier: The Comb Shield tier the reader runs at; picks the thresholds.
        targets: The reader's own capability set, whose `net` scopes are the hosts its task may
            reach; None when the text has no task (a human's chat message).
        task_id: The task the text arrived for, when there is one.
        ref: A short locator for the text (a tool name, a chat line id), never the text.
    """

    source: ScanSource
    consumer: str
    recorder: ScanRecorder
    tier: CombShieldLevel = CombShieldLevel.MEADOW
    targets: CapabilitySet | None = None
    task_id: TaskId | None = None
    ref: str | None = None


class ContentScanner:
    """Score outside text, decide its fate per tier, and record every flag on the trail.

    Holds no mutable state of its own beyond its hasher's loaded key, so one scanner may serve
    every entry point of a node concurrently.
    """

    def __init__(
        self, patterns: CompiledPatterns, policy: UntrustedContentSection, hasher: ContentHasher
    ) -> None:
        """Build a scanner.

        Args:
            patterns: The compiled pattern families (`load_scan_patterns`).
            policy: The manifest's `[guard.untrusted_content]` table: the input bound and the
                label and drop thresholds per tier.
            hasher: Computes the keyed hash a flag is recorded with.
        """
        self._patterns = patterns
        self._policy = policy
        self._hasher = hasher

    @property
    def policy(self) -> UntrustedContentSection:
        """The input bound and per-tier thresholds this scanner decides against."""
        return self._policy

    async def scan(self, text: str, site: ScanSite) -> ScanVerdict:
        """Score `text` for `site`, record a flag if it is one, and return the verdict.

        Args:
            text: The outside text, whole, as it arrived.
            site: Where it is being read: source, reader, tier, targets.

        Returns:
            PASS with no hash, or LABEL/DROP with the keyed hash of the whole text.

        Raises:
            hivemind.common.errors.SecretStoreError: The scanner key could not be read or minted.
            Whatever the trail's `record` raises: a flag is never returned unrecorded.
        """
        bound = self._policy.max_scan_chars
        score = score_text(text, self._patterns, site.targets, bound)
        action = decide(score.total, thresholds_for(self._policy, site.tier))
        if action is ScanAction.PASS:
            return _verdict(action, score, bound, content_hash=None)
        # Flagged: hash the whole text under the node's key, then put the flag on the trail
        # before the caller sees the verdict (codingrules 12: the record exists first).
        verdict = _verdict(action, score, bound, content_hash=await self._hasher.digest(text))
        await site.recorder.trail.record(_suspected_event(verdict, score, site, len(text)))
        return verdict


def default_content_scanner() -> ContentScanner:
    """Build a scanner from the shipped patterns and thresholds, keyed in memory.

    The default a `WorkerContext`, `WardenDeps` or `QueenDeps` built without one gets, and what a
    Virtual Cell's Warden runs with (it has no manifest and no Hive secret store): fully working,
    only its key is not persisted, so its hashes do not match another process's.

    Returns:
        A ContentScanner over `load_scan_patterns()`, `UntrustedContentSection()` and a fresh
        in-memory key.
    """
    return ContentScanner(
        load_scan_patterns(), UntrustedContentSection(), ContentHasher(MemorySecretStore())
    )


def _verdict(
    action: ScanAction, score: ScanScore, bound: int, content_hash: str | None
) -> ScanVerdict:
    """Build the verdict for `score`, validated (a flag must carry its hash, a pass none).

    A text cut at `bound` says so with the length that was read, so every renderer can show a
    model that head and nothing of the unscanned rest.
    """
    return ScanVerdict(
        action=action,
        score=round(score.total, _SCORE_DIGITS),
        families=score.families,
        scanned_chars=bound if score.truncated else None,
        content_hash=content_hash,
    )


def _suspected_event(
    verdict: ScanVerdict, score: ScanScore, site: ScanSite, chars: int
) -> GuardEvent:
    """Build the `guard.injection_suspected` event for one flag: ids and numbers, never text."""
    recorder = site.recorder
    payload: dict[str, JsonValue] = {
        "source": site.source.value,
        "consumer": site.consumer,
        "action": verdict.action.value,
        "tier": site.tier.value,
        "score": verdict.score,
        "families": list(verdict.families),
        "hits": {hit.family: hit.hits for hit in score.hits},
        "content_hash": verdict.content_hash,
        "chars": chars,
        "truncated": verdict.truncated,
    }
    # Optional locators, only when the site has them.
    if site.task_id is not None:
        payload["task_id"] = site.task_id
    if site.ref is not None:
        payload["ref"] = site.ref[:MAX_REF_CHARS]
    return GuardEvent(
        id=new_event_id(recorder.clock),
        hive_id=recorder.identity.hive_id,
        node_id=recorder.identity.node_id,
        at=recorder.clock.now(),
        actor=recorder.identity.actor,
        kind=INJECTION_SUSPECTED_KIND,
        subject_id=site.consumer,
        payload=payload,
    )

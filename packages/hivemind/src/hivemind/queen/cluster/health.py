"""Define HealthPoller: poll a clustered provider's health with backoff, pure schedule included.

Appendix C's "Provider health" row: `HEALTHY <-> DEGRADED <-> DOWN`, "in memory, re-probed on
start". Once `hivemind.queen.cluster.protocol.cluster` has paused a provider's bees, something has
to notice when it recovers without hammering it every tick; `HealthPoller` is that something.
The same poller watches every provider a live grant is bound to *before* any outage (roadmap
step 4.9's first trigger, "`ProviderHealth` failing with no fallback within Forage"): a healthy
provider is re-probed every `ClusterBackoff.healthy_interval_s`, and `failed_probes` counts the
consecutive DOWN readings so `hivemind.queen.cluster.tick` clusters on the second, never on one
blip.
`next_probe_at` is the pure half (codingrules section 9's usual split between a pure decision and
its effect): given how many consecutive probes a provider has failed and the current time, it
returns the next instant worth probing again, growing the gap exponentially up to a manifest-
derived ceiling. `probe` is the effectful half: it calls `LLMProvider.health()` -- never
`complete`/`stream` (codingrules: Clustering's own protocol never awaits a model) -- for one
provider whose next probe is due, and folds the reading back into this poller's own schedule.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's cluster
    sub-package. Held as a `hivemind.queen.deps.QueenDeps` field (`health_poller`), built once by
    whichever composition root constructs a Queen; read and mutated every tick by
    `hivemind.queen.cluster.tick.run_cluster_tick`, which calls `probe` for each currently
    clustered provider whose schedule is due. Calls into `hivemind.llm` (LLMProvider, HealthState,
    ProviderLookup) and waggle only.

Key invariants:
    - `next_probe_at` is pure: same `(attempt, now)` in, same instant out, no I/O, no clock read
      of its own -- `now` is always the caller's own injected reading (codingrules section 9's
      "pure decision, then effects" shape, same as `hivemind.queen.dispatcher`'s own split).
    - The backoff grows monotonically with `attempt` and never exceeds `max_s`: a provider stuck
      DOWN for a long outage is probed less and less often, never abandoned.
    - `probe` never calls `LLMProvider.complete`/`.stream`: only `.health()`. A test asserts this
      directly against a `FakeLLMProvider` whose `calls` list stays empty.
    - `HealthPoller` owns its own mutable state in place (codingrules section 8.5): `_attempts`
      and `_next_probe_at`, one entry per provider name, grow and shrink as `probe`/`reset` run.

See Also:
    - .claude/codingrules.md Appendix C, "Provider health" row, for the machine this module polls.
    - docs/adr/0024-clustering-protocol.md for "health polled with backoff".
    - hivemind.llm.provider for LLMProvider.health, this module's one effectful call.
    - hivemind.queen.cluster.tick for run_cluster_tick, this module's one caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from hivemind.llm import HealthState, ProviderHealth, ProviderLookup

DEFAULT_INITIAL_BACKOFF_S = 5.0  # First re-probe, one tick's worth of "just went down" patience.
DEFAULT_MAX_BACKOFF_S = 300.0  # Cap at five minutes: a long outage is still noticed reasonably.
DEFAULT_BACKOFF_FACTOR = 2.0  # Doubling: cheap to reason about, matches the Fanner's own retries.
# How often a provider that reads HEALTHY is probed again: one GET per provider per half minute
# is cheap on a loopback server and on a hosted one, and bounds how long an outage goes
# unnoticed to this plus one initial backoff (two DOWN readings cluster it).
DEFAULT_HEALTHY_PROBE_INTERVAL_S = 30.0

__all__ = [
    "DEFAULT_BACKOFF_FACTOR",
    "DEFAULT_HEALTHY_PROBE_INTERVAL_S",
    "DEFAULT_INITIAL_BACKOFF_S",
    "DEFAULT_MAX_BACKOFF_S",
    "ClusterBackoff",
    "HealthPoller",
    "next_probe_at",
]


@dataclass(frozen=True, slots=True)
class ClusterBackoff:
    """The manifest-derived constants `next_probe_at` grows its schedule from.

    Attributes:
        initial_s: The gap before the first re-probe of a newly clustered provider.
        max_s: The largest gap this schedule ever reaches.
        factor: How much the gap multiplies by per consecutive failed probe.
        healthy_interval_s: The steady-state gap between probes of a provider that last read
            HEALTHY, whether it is bound and running or has just recovered.
    """

    initial_s: float = DEFAULT_INITIAL_BACKOFF_S
    max_s: float = DEFAULT_MAX_BACKOFF_S
    factor: float = DEFAULT_BACKOFF_FACTOR
    healthy_interval_s: float = DEFAULT_HEALTHY_PROBE_INTERVAL_S


def next_probe_at(attempt: int, now: datetime, backoff: ClusterBackoff | None = None) -> datetime:
    """Return the next instant worth probing a provider that has failed `attempt` probes so far.

    Pure: no I/O, no clock of its own (module docstring).

    Args:
        attempt: How many consecutive failed (DOWN) probes this provider has had; 0 for "never
            probed yet" (the first probe is due immediately).
        now: The caller's own injected current time.
        backoff: The schedule's constants; the module defaults when a caller has none of its own.

    Returns:
        `now` for `attempt <= 0`; otherwise `now` plus `min(initial_s * factor**(attempt - 1),
        max_s)` seconds.
    """
    if attempt <= 0:
        return now  # Never probed (or just recovered): due right away.
    active_backoff = backoff if backoff is not None else ClusterBackoff()
    delay = min(
        active_backoff.initial_s * (active_backoff.factor ** (attempt - 1)), active_backoff.max_s
    )
    return now + timedelta(seconds=delay)


@dataclass(slots=True)
class HealthPoller:
    """Poll each clustered provider's health on its own backoff schedule.

    Owns its own mutable state in place (codingrules section 8.5): `_attempts` and
    `_next_probe_at`, one entry per provider name, added on the first `probe` call for that
    provider and dropped by `reset` once it recovers or is resumed by hand.
    """

    backoff: ClusterBackoff = field(default_factory=ClusterBackoff)
    _attempts: dict[str, int] = field(default_factory=dict)
    _next_probe_at: dict[str, datetime] = field(default_factory=dict)

    def is_due(self, provider: str, now: datetime) -> bool:
        """Return whether `provider` is due for a probe at `now`.

        True for a provider this poller has never probed (module docstring's `attempt=0` case).

        Args:
            provider: The `[llm.providers.<name>]` key to check.
            now: The caller's own injected current time.
        """
        due_at = self._next_probe_at.get(provider)
        return due_at is None or now >= due_at

    async def probe(
        self, provider: str, provider_lookup: ProviderLookup, now: datetime
    ) -> ProviderHealth:
        """Probe `provider`'s health once, and fold the reading into this poller's own schedule.

        Args:
            provider: The `[llm.providers.<name>]` key to probe.
            provider_lookup: Resolves `provider` to a live `LLMProvider`; `QueenDeps.
                provider_lookup` in production, `ProviderRegistry.provider` bound to an instance.
            now: The caller's own injected current time, used to schedule the *next* probe.

        Returns:
            The fresh `ProviderHealth` reading. A `HEALTHY` reading drops this provider's own
            failed-probe count and schedules the next probe one `healthy_interval_s` out; any
            other reading (`DEGRADED`/`DOWN`) advances the count and reschedules on the backoff.
        """
        instance = provider_lookup(provider)
        reading = await instance.health()  # External await: an outage probe, no fixed timeout
        # here since `hivemind.llm.provider.LLMProvider.health` itself never blocks on the model
        # (it reports readiness, never completes a call) -- see that Protocol's own contract.
        if reading.state is HealthState.HEALTHY:
            # Steady state: forget any earlier failures, but keep watching at the slow cadence
            # so a bound provider going down is noticed without a probe on every tick.
            self._attempts.pop(provider, None)
            self._next_probe_at[provider] = now + timedelta(seconds=self.backoff.healthy_interval_s)
        else:
            attempt = self._attempts.get(provider, 0) + 1
            self._attempts[provider] = attempt
            self._next_probe_at[provider] = next_probe_at(attempt, now, self.backoff)
        return reading

    def failed_probes(self, provider: str) -> int:
        """Return how many consecutive probes of `provider` have read DOWN or DEGRADED.

        0 for a provider never probed, or whose last reading was HEALTHY. `hivemind.queen.cluster.
        tick` clusters a bound provider only once this reaches its own threshold, so one failed
        GET never pauses a whole provider's bees.

        Args:
            provider: The `[llm.providers.<name>]` key to look up.
        """
        return self._attempts.get(provider, 0)

    def reset(self, provider: str) -> None:
        """Drop `provider`'s own attempt count and scheduled next probe.

        Called by `hivemind.queen.cluster.tick` once an operator's `hive wake` has resumed a
        provider by hand, so it is probed again at once and a provider clustered again later
        starts its backoff fresh rather than picking up where a past outage left off.

        Args:
            provider: The provider to reset.
        """
        self._attempts.pop(provider, None)
        self._next_probe_at.pop(provider, None)

"""Pin a goal to one device: build its capability ceiling, wait for the device, run the goal.

`hive swarm run NODE "GOAL"` is `hive run` with one difference: the goal may be placed on that
device's Cell and nowhere else. A goal carries the capability set of whoever submitted it (its
*ceiling*), and the Queen's placement only considers a Cell the ceiling allows: the Hive Stand
(the machine she runs on) needs `cell:hive_stand`, a borrowed device needs `cell:real:<its Cell>`.
So the pin is a ceiling that holds the second and not the first, and nothing else changes: the
ceiling lists every family a Worker might use at its widest, as the operator's role holds them in
the shipped Guard policy, minus the families that place a goal on another Cell or that only a
person or an enrolled client holds. A ceiling only narrows (a Worker's set is its role's default,
cut down by its Warden's set and by the device's access level, and then by this), so it can lend
the goal nothing a goal without a ceiling would not have; the device's access level stays the real
limit on what may happen to the device.

A Hive that has just been built has not met the device yet: the device's packet redials it, the
swarm gate attaches the device's Warden to the Queen, and only then does she know the Cell exists.
`run_goal_on_device` therefore waits, inside the same time budget as the goal, until the Warden is
attached, and fails with a sentence naming the device if it never is. And a goal is over a beat
before its Warden finds nothing to do and gives the device back (releases its lease), so the run
waits for that too, a moment, before the Hive is stopped: stopping a Warden in the middle of its
own release fails the release (a lease cannot be released twice at once), and the point of the
command is that the device is left as found.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the CLI's `swarm` command group. Called by
    `hive swarm run`. Calls into `hivemind.cli.compose.hive` (Hive, GoalRun, run_goal_with) and
    `hivemind.swarm` (SwarmNode, GatewayUnavailableError) only.

Key invariants:
    - The ceiling names exactly one Real Cell, the device's, and never `cell:hive_stand` or
      `cell:virtual`, so a goal run through here can be placed on that device or nowhere.
    - The ceiling is a subset of the operator role's set in the shipped Guard policy (a test holds
      the two together), so it can never name something the operator could not do.
    - The wait and the goal share one `timeout_s`: the goal gets what the wait left.
    - A goal that finished is not reported until its Warden has released the device, or
      `RELEASE_WAIT_S` has passed (a device gone silent is left to its dead-man switch).

See Also:
    - tests/e2e/test_swarm_level0.py for the same pin, spelled out, on a scripted goal.
    - hivemind.guard.defaults.policy.toml for the operator role this ceiling is cut from.
    - hivemind.queen.placement.rules for how a ceiling decides which Cells a goal may use.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from hivemind.cli.compose.hive import GoalReport, GoalRun, Hive, run_goal_with
from hivemind.swarm import GatewayUnavailableError, SwarmNode
from waggle.clock import Clock
from waggle.ids import DeviceId

# Every work family at its widest, as `[roles.operator]` in the shipped Guard policy holds them,
# less what a pinned goal must not carry. Left out on purpose: `cell:hive_stand`, `cell:virtual`
# and `cell:real:*` (the pin replaces them), `tool:quarantine_on_real` (no Warden or Worker holds
# it), `warden:spawn`, `watch:*`, the device families `observe*` and `entrance:*`, and the
# human-only `supersede` and `sting_cut`. The comb-shield scope is `*`: the device's own tier is
# what places a task, and the ceiling has no say in it.
DEVICE_WORK_CAPABILITIES = (
    "cell:comb_shield:*",
    "tool:*",
    "tool:scope:*",
    "tool:request",
    "fs:read:**",
    "fs:write:**",
    "exec:*",
    "cell:outside_scratch:**",
    "net:*",
    "device:*",
    "exoskeleton:*",
    "tactic:*",
    "geo:*",
    "wifi:scan",
    "host:metadata",
    "llm:*",
    "spend:*",
    "honey:read:*",
    "honey:write",
    "honey:clearance:c2",
    "wax:propose",
    "forage:request",
    "question:human",
)
DEVICE_POLL_S = 0.25  # How often the wait looks at the Queen's attached Wardens: cheap and local.
RELEASE_WAIT_S = 10.0  # The longest the Warden gets to give the device back once the goal is over.

__all__ = [
    "DEVICE_POLL_S",
    "DEVICE_WORK_CAPABILITIES",
    "RELEASE_WAIT_S",
    "await_device",
    "await_release",
    "device_ceiling",
    "run_goal_on_device",
]


def device_ceiling(node: SwarmNode) -> tuple[str, ...]:
    """Return the capability ceiling that lets a goal be placed on `node`'s Cell and nowhere else.

    Args:
        node: The enrolled device the goal is for.

    Returns:
        `cell:real:<the device's Cell>` followed by `DEVICE_WORK_CAPABILITIES`.
    """
    return (f"cell:real:{node.cell_id}", *DEVICE_WORK_CAPABILITIES)


async def await_device(
    clock: Clock, is_served: Callable[[], bool], device_id: DeviceId, timeout_s: float
) -> None:
    """Wait until the device's Warden is attached to the Queen, which is when its Cell can be used.

    Args:
        clock: The Hive's clock; the wait is measured and paced on it.
        is_served: Whether the Queen has the device's Warden attached right now.
        device_id: The device being waited for, named in the error.
        timeout_s: The longest to wait, in seconds on `clock`.

    Raises:
        GatewayUnavailableError: The Warden was not attached within `timeout_s`: the device is
            off, cannot reach the listener, or its link failed verification.
    """
    deadline = clock.monotonic() + timeout_s
    # The swarm gate attaches a device's Warden once the device's packet has connected and been
    # verified, so its presence among the Queen's Wardens is the one fact that says "usable".
    while not is_served():
        if clock.monotonic() >= deadline:
            raise GatewayUnavailableError(
                f"Device {device_id} did not connect to this Hive within {timeout_s:g} s. "
                "Check that the device is on, that its packet (`pollen status`) can reach the "
                "swarm listener's advertised address, and that no other `hive run` or `hive "
                "serve` already holds the listener's port."
            )
        # A local, in-memory look: the wait costs the device's reconnection time and nothing else.
        await clock.sleep(DEVICE_POLL_S)


async def await_release(
    clock: Clock, holds_lease: Callable[[], bool], timeout_s: float = RELEASE_WAIT_S
) -> bool:
    """Wait until the device's Warden has given the device back, or `timeout_s` has passed.

    Args:
        clock: The Hive's clock; the wait is measured and paced on it.
        holds_lease: Whether the device's Warden holds a lease on the device right now.
        timeout_s: The longest to wait, in seconds on `clock`.

    Returns:
        True when the Warden holds no lease, False when the wait ran out first (the device is
        silent or slow; the Hive's shutdown tries the release again, and the device's own
        dead-man switch ends a lease nobody confirms).
    """
    deadline = clock.monotonic() + timeout_s
    while holds_lease():
        if clock.monotonic() >= deadline:
            return False
        await clock.sleep(DEVICE_POLL_S)  # A local, in-memory look at the Warden: cheap.
    return True


def _holds_lease(hive: Hive, device_id: DeviceId) -> bool:
    """Whether the Warden the swarm gate keeps for `device_id` holds a lease on it now."""
    swarm = hive.swarm
    served = swarm.gate.wardens.get(device_id) if swarm is not None else None
    return served is not None and served.warden.lease is not None


async def run_goal_on_device(hive: Hive, node: SwarmNode, goal: str, run: GoalRun) -> GoalReport:
    """Run `goal` on `node` and nowhere else, once the device has reconnected.

    Args:
        hive: A Hive inside `run_hive`.
        node: The enrolled device the goal is pinned to.
        goal: The goal text, as the human stated it.
        run: The clearance, budget and event hook; its own `capabilities` are replaced by the pin.

    Returns:
        The goal's report, as `run_goal_with` returns it.

    Raises:
        GatewayUnavailableError: The device did not connect within the budget.
    """

    def is_served() -> bool:
        """Whether the Queen has this device's Warden attached now."""
        return node.warden_id in {link.warden_id for link in hive.queen.wardens}

    started = hive.clock.monotonic()
    await await_device(hive.clock, is_served, node.device_id, run.timeout_s)
    # One budget for the wait and the goal, so `--timeout` means what it says on `hive run`.
    remaining_s = max(run.timeout_s - (hive.clock.monotonic() - started), 0.0)
    pinned = replace(run, timeout_s=remaining_s, capabilities=device_ceiling(node))
    report = await run_goal_with(hive, goal, pinned)
    if not report.timed_out:
        # The goal's task is terminal a beat before its Warden releases the lease: let it finish,
        # so the Hive is not stopped under a release in progress (module docstring).
        await await_release(hive.clock, lambda: _holds_lease(hive, node.device_id))
    return report

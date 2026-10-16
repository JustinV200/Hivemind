"""Derive the operating-system resource limits a Comb run is held to, as pure data.

`QuarantineLimits` says what a run may spend in the units the report speaks (CPU seconds, resident
bytes, wall seconds, output bytes). The runner inside the sandbox needs the same budget as
`resource.setrlimit` values, and those differ in two ways worth writing down once: the runner's
own start-up (importing the Hive's libraries) already costs CPU and address space, so the cap is
the baseline plus the budget rather than the budget alone; and a limit the platform lacks is
simply skipped. The memory budget is resident bytes, which the resources gate measures after the
run, while the kernel can only cap address space, which every process reserves far beyond what it
touches (thread stacks, allocator arenas, mapped files). The address-space cap is therefore a
backstop against a runaway allocation taking the host down, sized well above the resident budget
so an honest tool is judged by the gate and never killed first. This module is the pure
arithmetic; the runner is the only place that calls `resource`, so nothing here imports it and
the plan can be tested without touching the process.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.quarantine_comb.
    Called by the runner (to apply limits) and by the gates (to read the budget). Calls into
    `hivemind.royal_jelly.spec` for `QuarantineLimits` only.

Key invariants:
    - `rlimit_plan` is pure: the same limits and baseline always give the same plan.
    - Every cap is at least the baseline, so the plan can never kill the runner before the tool
      has run a line.

See Also:
    - docs/adr/0047 (the Comb's gates and sandbox) for what the runner does with the plan.
    - hivemind.royal_jelly.spec.report for QuarantineLimits.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.royal_jelly.spec import QuarantineLimits

CPU_GRACE_S = (
    1  # Whole seconds added to the CPU cap: RLIMIT_CPU counts in integers and rounds down.
)
FSIZE_BYTES = 16 * 1024 * 1024  # One file may not outgrow this; a tool writes source-sized data.
PROCESS_HEADROOM = 64  # New tasks (processes or threads) the user may start beyond those running.
# Address space allowed per byte of the resident budget: the allocator's per-thread arenas, a
# mapped scratch file (at most FSIZE_BYTES each) and the interpreter's own over-reservation all
# count against RLIMIT_AS but not against resident memory, and are each a small multiple of what
# the tool actually touches. Four keeps the cap a real backstop: a tool that reserves this much
# is not an honest one that the resources gate would have passed.
ADDRESS_SPACE_FACTOR = 4
# What one new thread reserves of address space before it has touched a byte (glibc's default
# stack reservation; Windows and macOS reserve less). The plan allows PROCESS_HEADROOM new tasks,
# so it allows their stacks too, or a thread pool would be killed for a budget it never spent.
THREAD_STACK_BYTES = 8 * 1024 * 1024
MIN_FUZZ_CALL_S = 1.0  # A fuzz call needs at least this long to finish an honest tool.
MAX_FUZZ_CALL_S = 30.0  # And never more: a hang must not spend the whole wall budget on one case.

__all__ = [
    "ADDRESS_SPACE_FACTOR",
    "CPU_GRACE_S",
    "FSIZE_BYTES",
    "MAX_FUZZ_CALL_S",
    "MIN_FUZZ_CALL_S",
    "PROCESS_HEADROOM",
    "THREAD_STACK_BYTES",
    "Baseline",
    "RlimitPlan",
    "address_space_cap",
    "fuzz_call_timeout",
    "rlimit_plan",
]


@dataclass(frozen=True, slots=True)
class Baseline:
    """What the runner already used before the tool's first line (its imports and start-up).

    Attributes:
        cpu_s: CPU seconds spent so far (user plus system).
        address_space_bytes: Virtual size of the process so far.
        task_count: Tasks (processes and their threads) this user already runs across the
            machine, which is what `RLIMIT_NPROC` counts against; 0 where it could not be read.
    """

    cpu_s: float
    address_space_bytes: int
    task_count: int


@dataclass(frozen=True, slots=True)
class RlimitPlan:
    """The `resource` limits to apply, by constant name; a value is `(soft, hard)`.

    Names (`RLIMIT_CPU`) rather than the numeric constants keep this module free of `resource`
    and let the runner skip a name the platform does not define.
    """

    limits: dict[str, tuple[int, int]]


def rlimit_plan(limits: QuarantineLimits, baseline: Baseline) -> RlimitPlan:
    """Turn the report-level budget into `setrlimit` values on top of the runner's baseline.

    Args:
        limits: What the run may spend.
        baseline: What the runner already spent before the tool started.

    Returns:
        The plan: CPU seconds, address space, file size and process count caps.
    """
    # CPU: the budget plus what start-up used, rounded up, plus a grace second so a tool that
    # lands exactly on its budget is judged by the resources gate rather than killed by SIGXCPU.
    cpu_cap = int(baseline.cpu_s + limits.cpu_s) + CPU_GRACE_S
    caps = {
        "RLIMIT_CPU": cpu_cap,
        "RLIMIT_FSIZE": FSIZE_BYTES,
    }
    # Address space: virtual size always exceeds resident size, so the cap sits above the
    # baseline's virtual size by a multiple of the memory budget plus the stacks of the tasks the
    # plan allows; the gate still judges resident memory, and this cap only ends a run no honest
    # tool makes. A zero baseline means the platform could not measure it, and a guessed cap is
    # worse than none.
    if baseline.address_space_bytes > 0:
        caps["RLIMIT_AS"] = address_space_cap(limits, baseline)
    # Processes are counted per user across the machine, so the cap is what runs now plus headroom.
    if baseline.task_count > 0:
        caps["RLIMIT_NPROC"] = baseline.task_count + PROCESS_HEADROOM
    return RlimitPlan(limits={name: (value, value) for name, value in caps.items()})


def address_space_cap(limits: QuarantineLimits, baseline: Baseline) -> int:
    """Return the RLIMIT_AS value: the baseline's virtual size plus the budget's headroom.

    Args:
        limits: What the run may spend; `memory_bytes` is the resident budget.
        baseline: What the runner already reserved before the tool started.

    Returns:
        `address_space_bytes + memory_bytes * ADDRESS_SPACE_FACTOR + PROCESS_HEADROOM *
        THREAD_STACK_BYTES`, in bytes.
    """
    headroom = limits.memory_bytes * ADDRESS_SPACE_FACTOR + PROCESS_HEADROOM * THREAD_STACK_BYTES
    return baseline.address_space_bytes + headroom


def fuzz_call_timeout(limits: QuarantineLimits) -> float:
    """Return the per-call timeout of one fuzz case: the wall budget shared across the cases.

    Args:
        limits: What the run may spend.

    Returns:
        `wall_s / fuzz_cases`, clamped to [MIN_FUZZ_CALL_S, MAX_FUZZ_CALL_S].
    """
    share = limits.wall_s / limits.fuzz_cases
    return min(MAX_FUZZ_CALL_S, max(MIN_FUZZ_CALL_S, share))

"""Define the Royal Jelly Lab's and the Basket's manifest sections: ``[royal_jelly]``, ``[basket]``.

Phase 9 lets the Hive grow its own tools (the Royal Jelly Lab scaffolds one, the Quarantine Comb
proves it in a sandbox, the Comb Registry keeps it) and keep inert files (the Basket). Every
number those run on is an operator's choice, so it lives here as schema with a documented
default (codingrules section 13). ``QuarantineLimitsSection`` is what one Comb run may spend, as
data (CPU seconds, memory, wall time, output, fuzz inputs); ``RoyalJellySection`` picks the
sandbox the Comb runs in (a disposable Virtual Cell, or a dev-only local subprocess), the
Virtual Cell's image, distribution and footprint, how often a failed scaffold is retried with the
Comb's report, and how long a tool request may take end to end; ``BasketSection`` says where
blob bytes live, the largest blob, the chunk size a shipment uses, the resident disk ceiling a
Cell gets when the Queen sets none, and whether a promoted tool's package is shipped to the
Cells that can run it. The Comb's own ``QuarantineLimits`` model lives in ``hivemind.royal_jelly``
(a higher layer than this one), so the composition root turns a ``QuarantineLimitsSection`` into
one; the two carry the same fields and defaults, and a test pins that they stay equal.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Embedded by
    ``hivemind.manifest.schema.manifest.HiveManifest`` as ``royal_jelly`` and ``basket``; read by
    ``hivemind.royal_jelly`` and ``hivemind.basket`` through the composition root. Calls into
    pydantic, ``hivemind.manifest.schema.placement`` (the image default) and waggle's chunk bound
    only.

Key invariants:
    - Every model here is frozen and forbids unknown fields (codingrules section 8.5).
    - ``chunk_bytes`` never exceeds ``waggle.messages.base.MAX_CHUNK_BYTES``, so a chunk's base64
      text always fits one frame.
    - A manifest holds no secret: the Hive key that signs blobs is minted into the secret store.
    - ``sandbox = "virtual"`` needs ``[virtual_cells] backend``; that cross-section check lives on
      ``HiveManifest``, which sees both sections.

See Also:
    - docs/adr/0046-tool-spec-comb-registry-and-scopes.md and docs/adr/0047-quarantine-comb-gates-
      and-sandbox.md for what ``[royal_jelly]`` governs.
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md for ``[basket]``.
    - docs/manifests/full.toml for every field shown with its default.
    - hivemind.manifest.schema.placement for ``[virtual_cells]``, the image naming reused here.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from hivemind.manifest.schema.placement import DEFAULT_VIRTUAL_CELLS_IMAGE
from waggle.messages.base import MAX_CHUNK_BYTES

_MIB = 1024**2
_GIB = 1024**3
DEFAULT_CPU_S = 30.0  # A tool's tests and fuzz run in seconds; half a minute is generous.
DEFAULT_LIMIT_MEMORY_BYTES = 512 * _MIB  # Enough for an interpreter and a modest dataset.
DEFAULT_WALL_S = 120.0  # Two minutes of wall time covers the test run plus the fuzz inputs.
DEFAULT_OUTPUT_BYTES = _MIB  # One MiB of stdout and stderr: a failure message, never a dump.
DEFAULT_FUZZ_CASES = 32  # Enough schema-derived inputs to catch a crash without a long run.
DEFAULT_SANDBOX_DISTRIBUTION = "ubuntu"  # The distribution the default image is built on.
DEFAULT_MAX_RETRIES = 1  # One more scaffold attempt, shown the failing report.
MAX_RETRIES_CEILING = 3  # Past three attempts the model is not converging; ask the human.
DEFAULT_REQUEST_TIMEOUT_S = 600.0  # Ten minutes from request to verdict, sandbox boot included.
DEFAULT_SANDBOX_CPU_CORES = 1.0  # A sandbox runs one tool's tests: one core is plenty.
DEFAULT_SANDBOX_MEMORY_BYTES = 1 * _GIB  # Headroom over the 512 MiB tool limit for the runner.
DEFAULT_SANDBOX_DISK_BYTES = 2 * _GIB  # The package, its scratch and the interpreter's caches.
DEFAULT_SANDBOX_LIFETIME_S = 900.0  # A sandbox is torn down after the report; this is the cap.
MAX_LIMIT_CPU_S = (
    3_600.0  # An hour: the same ceiling hivemind.royal_jelly.spec.QuarantineLimits has.
)
MIN_LIMIT_MEMORY_BYTES = 16 * _MIB  # Below this the interpreter itself does not start.
MAX_LIMIT_MEMORY_BYTES = 8 * _GIB  # Beyond this a "small" sandbox is no longer small.
MIN_OUTPUT_BYTES = 1_024  # A kilobyte: enough for one traceback.
MAX_OUTPUT_BYTES = 64 * _MIB  # A cap on what a runaway tool can make the Comb read back.
MAX_FUZZ_CASES = 1_000  # More inputs cost wall time without finding new classes of failure.
DEFAULT_MAX_BLOB_BYTES = 256 * _MIB  # A wheel set or a dataset; weights are a later, larger step.
MAX_BLOB_BYTES_CEILING = 1 * _GIB  # The Basket's own hard deposit ceiling (hivemind.basket.models).
MIN_CHUNK_BYTES = 1_024  # Smaller chunks spend the link on envelopes rather than bytes.
DEFAULT_CHUNK_BYTES = MAX_CHUNK_BYTES  # 256 KiB: the largest chunk a frame carries.
DEFAULT_RESIDENT_CEILING_BYTES = 2 * _GIB  # A Cell's own resident Basket disk when none is set.

__all__ = [
    "DEFAULT_CHUNK_BYTES",
    "DEFAULT_MAX_BLOB_BYTES",
    "DEFAULT_RESIDENT_CEILING_BYTES",
    "BasketSection",
    "QuarantineLimitsSection",
    "RoyalJellySection",
    "SandboxKind",
]

# A frozen, extras-forbidding config every section here shares (codingrules section 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class SandboxKind(Enum):
    """Where the Quarantine Comb runs a candidate tool (ADR-0047)."""

    VIRTUAL = "virtual"  # A disposable Virtual Cell with no network: the real sandbox.
    SUBPROCESS = "subprocess"  # A local subprocess of the Hive Stand: development only.


class QuarantineLimitsSection(BaseModel):
    """``[royal_jelly.limits]``: what one Quarantine Comb run may spend before a gate fails."""

    model_config = _MODEL_CONFIG

    cpu_s: float = Field(
        default=DEFAULT_CPU_S,
        gt=0,
        le=MAX_LIMIT_CPU_S,
        description="CPU seconds the tool's tests and fuzz inputs may use in total.",
    )
    memory_bytes: int = Field(
        default=DEFAULT_LIMIT_MEMORY_BYTES,
        ge=MIN_LIMIT_MEMORY_BYTES,
        le=MAX_LIMIT_MEMORY_BYTES,
        description="Peak resident memory the tool may reach, in bytes.",
    )
    wall_s: float = Field(
        default=DEFAULT_WALL_S,
        gt=0,
        le=MAX_LIMIT_CPU_S,
        description="Wall-clock seconds the whole run may take.",
    )
    output_bytes: int = Field(
        default=DEFAULT_OUTPUT_BYTES,
        ge=MIN_OUTPUT_BYTES,
        le=MAX_OUTPUT_BYTES,
        description="Stdout and stderr bytes the run may produce before it is cut off.",
    )
    fuzz_cases: int = Field(
        default=DEFAULT_FUZZ_CASES,
        ge=1,
        le=MAX_FUZZ_CASES,
        description="Schema-derived inputs the fuzz gate feeds the tool.",
    )


class RoyalJellySection(BaseModel):
    """``[royal_jelly]``: the sandbox the Comb runs in, its footprint, and the retry budget."""

    model_config = _MODEL_CONFIG

    sandbox: SandboxKind = Field(
        default=SandboxKind.VIRTUAL,
        description="Where candidate tools are proven: 'virtual' (a disposable Virtual Cell with "
        "no network; needs [virtual_cells] backend) or 'subprocess' (a local process; development "
        "only, logged as a warning at start).",
    )
    sandbox_image: str = Field(
        default=DEFAULT_VIRTUAL_CELLS_IMAGE,
        min_length=1,
        description="The images/<name> the sandbox Virtual Cell boots.",
    )
    sandbox_distribution: str = Field(
        default=DEFAULT_SANDBOX_DISTRIBUTION,
        min_length=1,
        description="The Linux distribution of sandbox_image; a tool aimed at any other platform "
        "is also tested on a Real Cell of that platform (ADR-0047). Unset with the subprocess "
        "sandbox, it is this host's own distribution, because that sandbox is this host.",
    )
    limits: QuarantineLimitsSection = Field(
        default_factory=QuarantineLimitsSection,
        description="What one Comb run may spend (CPU, memory, wall time, output, fuzz inputs).",
    )
    max_retries: int = Field(
        default=DEFAULT_MAX_RETRIES,
        ge=0,
        le=MAX_RETRIES_CEILING,
        description="Times a failed scaffold is retried with the Comb's report before the tool "
        "request is rejected; 0 means one attempt.",
    )
    request_timeout_s: float = Field(
        default=DEFAULT_REQUEST_TIMEOUT_S,
        gt=0,
        description="Seconds a tool request may take from acceptance to verdict, sandbox boot "
        "included, before it is rejected as timed out.",
    )
    sandbox_cpu_cores: float = Field(
        default=DEFAULT_SANDBOX_CPU_CORES,
        gt=0,
        description="Logical cores reserved for a sandbox Virtual Cell.",
    )
    sandbox_memory_bytes: int = Field(
        default=DEFAULT_SANDBOX_MEMORY_BYTES,
        gt=0,
        description="Memory reserved for a sandbox Virtual Cell, in bytes.",
    )
    sandbox_disk_bytes: int = Field(
        default=DEFAULT_SANDBOX_DISK_BYTES,
        gt=0,
        description="Disk reserved for a sandbox Virtual Cell, in bytes.",
    )
    sandbox_lifetime_s: float = Field(
        default=DEFAULT_SANDBOX_LIFETIME_S,
        gt=0,
        description="The longest a sandbox Virtual Cell may live before it is torn down, whether "
        "or not its report has arrived.",
    )


class BasketSection(BaseModel):
    """``[basket]``: where blob bytes live, the size bounds and the shipping policy."""

    model_config = _MODEL_CONFIG

    root: Path | None = Field(
        default=None,
        description="The directory blob bytes are kept under; relative paths resolve against "
        "the manifest's own directory. None means a 'basket' directory beside the Hive database.",
    )
    max_blob_bytes: int = Field(
        default=DEFAULT_MAX_BLOB_BYTES,
        gt=0,
        le=MAX_BLOB_BYTES_CEILING,
        description="The largest blob a deposit may carry, in bytes; a larger one is refused.",
    )
    chunk_bytes: int = Field(
        default=DEFAULT_CHUNK_BYTES,
        ge=MIN_CHUNK_BYTES,
        le=MAX_CHUNK_BYTES,
        description="Raw bytes in one shipment chunk; at most the wire's chunk bound so the "
        "base64 frame fits one Waggle frame.",
    )
    default_resident_ceiling_bytes: int = Field(
        default=DEFAULT_RESIDENT_CEILING_BYTES,
        gt=0,
        description="The resident Basket disk ceiling for a Cell whose Forage ceilings set none, "
        "in bytes; the oldest resident blobs are evicted to stay under it.",
    )
    ship_on_promotion: bool = Field(
        default=True,
        description="Whether a newly promoted tool's package is shipped to every Cell that can "
        "run it at once; false ships it on first use.",
    )

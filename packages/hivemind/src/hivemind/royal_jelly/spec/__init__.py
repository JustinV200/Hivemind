"""Define ToolSpec and its companions: validation, packaging and the Quarantine Comb's report.

A tool starts as a `ToolSpec` (a requested tool described precisely: name, version, argument
schema, needed capabilities, entry point, platforms, scope). `validate_spec` refuses a spec that
asks for more than its requester holds; `pack`/`unpack` turn the tool's files into a
deterministic archive named by its SHA-256; `QuarantineReport` is the Comb's verdict on one spec
and one package, the evidence the Comb Registry (the catalogue of promoted tools) promotes on.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside the royal_jelly package.
    Called by the scaffolder, the Quarantine Comb, the promotion flow and the registry; calls into
    `hivemind.guard` (capabilities) and waggle's authoring bounds.

Key invariants:
    - One canonical spec hash (`spec_sha256`) and one canonical package hash (`sha256_of`), used
      everywhere a hash names a version.
    - A package is deterministic to pack and safe to unpack.

See Also:
    - docs/adr/0046 (tool spec, Comb Registry and scopes) for the decisions.
    - hivemind.royal_jelly.registry for what consumes a spec, a report and a package hash.

Public API:
    - ToolSpec, spec_sha256 (models): the request and its canonical hash.
    - SpecValidation, validate_spec (validate): the refusals a well-formed spec can still earn.
    - ToolPackage, pack, unpack, sha256_of, required_files_present, MAX_PACKAGE_BYTES,
      TOOL_MODULE, TESTS_MODULE, SPEC_FILE (package): the deterministic archive.
    - QuarantineReport, GateResult, ResourceUsage, QuarantineLimits, GATE_ORDER (report): the
      Quarantine Comb's verdict and the budget it ran under.
"""

from hivemind.royal_jelly.spec.models import ToolSpec, spec_sha256
from hivemind.royal_jelly.spec.package import (
    MAX_PACKAGE_BYTES,
    SPEC_FILE,
    TESTS_MODULE,
    TOOL_MODULE,
    ToolPackage,
    pack,
    required_files_present,
    sha256_of,
    unpack,
)
from hivemind.royal_jelly.spec.report import (
    GATE_ORDER,
    GateResult,
    QuarantineLimits,
    QuarantineReport,
    ResourceUsage,
)
from hivemind.royal_jelly.spec.validate import SpecValidation, validate_spec

__all__ = [
    "GATE_ORDER",
    "MAX_PACKAGE_BYTES",
    "SPEC_FILE",
    "TESTS_MODULE",
    "TOOL_MODULE",
    "GateResult",
    "QuarantineLimits",
    "QuarantineReport",
    "ResourceUsage",
    "SpecValidation",
    "ToolPackage",
    "ToolSpec",
    "pack",
    "required_files_present",
    "sha256_of",
    "spec_sha256",
    "unpack",
    "validate_spec",
]

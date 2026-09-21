"""Hold the Hive's per-cloud-provider CellBackend implementations.

Each cloud provider the Hive can provision a Virtual Cell on gets one CellBackend implementation
here. ADR-0026 deliberately chose none for Brood 1.0 ("cloud backends ... are optional"): `base.py`
defines what every real provider must add on top of `hivemind.hive.backends.base.CellBackend`
(`CloudCellBackend`, `CloudBackendConfig`, `CloudCredentials`, `CloudRegion`, `PricingTag`), and
`fake.py`'s `FakeCloudCellBackend` is this phase's own reference implementation -- an in-memory
fake, not a vendor SDK, exactly like `hivemind.hive.backends.fake.FakeCellBackend` is for the base
protocol. See `README.md` for what a real, post-1.0 provider must implement.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside the hive package. Handles
    one CellBackend implementation per cloud provider. Called by hive's public API on behalf of
    whatever calls hive itself; calls into sibling packages at Layer 3 or below, never back up
    into hive's other sub-packages directly.

Key invariants:
    - No vendor cloud SDK is imported anywhere in this package (codingrules section 4): the one
      shipped implementation, `FakeCloudCellBackend`, is in-memory only.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under hive.
    - .claude/roadmap.md phase 5 step 5.12 for the work that populates it.
    - docs/adr/0026-cell-backends-docker-first-qemu-second.md for why no provider is chosen here.

Public API:
    - CloudCellBackend, CloudBackendConfig, CloudCredentials, CloudRegion, PricingTag: the
      cloud-specific seam every real provider must add on top of CellBackend
      (hivemind.hive.backends.cloud.base).
    - FakeCloudCellBackend: the in-memory reference implementation (hivemind.hive.backends.
      cloud.fake).
"""

from hivemind.hive.backends.cloud.base import (
    CloudBackendConfig,
    CloudCellBackend,
    CloudCredentials,
    CloudRegion,
    PricingTag,
)
from hivemind.hive.backends.cloud.fake import FakeCloudCellBackend

__all__ = [
    "CloudBackendConfig",
    "CloudCellBackend",
    "CloudCredentials",
    "CloudRegion",
    "FakeCloudCellBackend",
    "PricingTag",
]

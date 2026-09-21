"""Define what every cloud CellBackend must add on top of hivemind.hive.backends.base.CellBackend.

ADR-0026 chose Docker first and QEMU second for Brood 1.0 and deliberately named no cloud
provider: "cloud backends come after both, behind `hive/backends/cloud/base.py`, and are optional
for Brood 1.0." This module is that seam. `CloudCredentials` (`SecretStr` fields, never in
`repr`), `CloudRegion` and `PricingTag` (a currency-neutral cost per hour that maps onto
`hivemind.forage.models.sources.ModelCost`'s own cost shape, so Forage's cost accounting reads one
vocabulary whether the spend is a model's per-seat-hour rate or a Cell's own hourly instance
price) are the three pieces of configuration a real cloud provider's Cell always needs beyond a
local backend's; `CloudBackendConfig` bundles them; `CloudCellBackend` is a `Protocol` extending
`CellBackend` with the one thing local backends have no equivalent for -- reporting a Cell's own
accrued spend, so it can be folded into a Forage cost figure the same way a hosted model's per-token
spend already is.

No vendor SDK lives here or anywhere under this package (codingrules section 4: a vendor SDK is
confined to the one module that imports it, and this dispatch adds none): the one implementation
this phase ships is `hivemind.hive.backends.cloud.fake.FakeCloudCellBackend`, an in-memory
reference the contract suite runs against as a fourth harness, exactly as `FakeCellBackend` and
`FakeDockerClient` are shipped, non-test-only fakes (codingrules 14.4). A real provider (AWS, GCP,
Azure, ...) is post-1.0 work; `cloud/README.md` says exactly what it must implement.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.hive.backends.
    cloud`. Called by hive's public API on behalf of whatever calls hive itself, and by
    `hivemind.forage` (a later integration) when it folds a Virtual Cell's own spend into a cost
    figure. Calls into `hivemind.forage` (ModelCost) and `waggle.ids` (CellId) only.

Key invariants:
    - `CloudCredentials`' fields are all `pydantic.SecretStr`: neither `repr(credentials)` nor
      `str(credentials)` ever shows a secret value (pydantic's own `SecretStr` redaction), matching
      codingrules section 13's "secrets ... redacted in repr".
    - `PricingTag.cost_per_hour_usd` is never negative; `as_model_cost()` maps it onto
      `ModelCost.cost_per_seat_hour_usd` unchanged, reusing the one cost vocabulary Forage already
      reads rather than inventing a second one for Virtual Cells (roadmap step 5.12's own
      instruction: "read hivemind/forage/models.py for the cost fields and reuse them").
    - `CloudCellBackend` is a structural `Protocol`: a class satisfies it by shape, exactly like
      `CellBackend` itself, never by explicit subclassing.

See Also:
    - docs/adr/0026-cell-backends-docker-first-qemu-second.md for why no cloud provider is chosen
      here, and why `cloud/fake.py` is this phase's own reference implementation instead.
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md for "no inbound port,
      egress to the Queen endpoint only", the same rule a real cloud instance must honour.
    - hivemind.hive.backends.qemu.cloud_init for the cloud-init user-data documents a real cloud
      implementation's own user-data must carry (see this module's CloudCellBackend docstring).
    - hivemind.forage.models.sources for ModelCost, the cost vocabulary PricingTag reuses.
    - hivemind.hive.backends.base for CellBackend, the protocol this module extends.
    - hivemind.hive.backends.cloud.fake for FakeCloudCellBackend, this phase's reference.
"""

from __future__ import annotations

from typing import Annotated, NewType, Protocol

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from hivemind.forage import ModelCost
from hivemind.hive.backends.base import CellBackend
from waggle.ids import CellId

# A provider-defined region code (e.g. "us-east-1", "europe-west4"); never validated in shape
# here since every provider spells its own regions differently, exactly like CloudRegion itself
# only names what a real backend must carry, never how it is used.
CloudRegion = NewType("CloudRegion", str)

_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")  # codingrules 8.5, shared by every model.

__all__ = [
    "CloudBackendConfig",
    "CloudCellBackend",
    "CloudCredentials",
    "CloudRegion",
    "PricingTag",
]


class CloudCredentials(BaseModel):
    """The minimal shape every cloud provider's own credential type maps onto.

    A key id and a secret, the pair almost every provider's own access-key style credential
    reduces to (an access key id plus a secret access key, a service-account key plus a client
    secret, ...); a real provider's own implementation (post-1.0, see `cloud/README.md`) may add
    provider-specific fields in a subclass. Both fields are `SecretStr` so neither ever appears in
    a log line, the Pheromone Trail, or this model's own `repr` (codingrules section 13).
    """

    model_config = _MODEL_CONFIG

    key_id: SecretStr = Field(
        description="The credential's own identifying half (an access key id, a client id, a "
        "service-account email); still a SecretStr, since some providers treat it as sensitive."
    )
    secret: SecretStr = Field(
        description="The credential's own secret half (a secret access key, a client secret, a "
        "private key); never logged, never in the Pheromone Trail, never in a manifest file."
    )


class PricingTag(BaseModel):
    """What one cloud instance shape costs per hour, and how that maps onto Forage cost.

    Currency-neutral in shape: a single rate, mirroring `hivemind.forage.models.sources.
    ModelCost.cost_per_seat_hour_usd`'s own "dollars per hour something is held" convention rather
    than inventing a second currency scheme for Virtual Cells.
    """

    model_config = _MODEL_CONFIG

    cost_per_hour_usd: Annotated[float, Field(ge=0)] = Field(
        description="US dollars per hour this instance shape costs while a Cell is provisioned "
        "on it; 0.0 for a free tier or a self-hosted-but-cloud-shaped backend."
    )

    def as_model_cost(self) -> ModelCost:
        """Map this pricing tag onto the Forage cost vocabulary every other source already uses.

        Returns:
            A `ModelCost` whose `cost_per_seat_hour_usd` equals `cost_per_hour_usd`; every other
            `ModelCost` field stays at its own default (0.0), since a Virtual Cell's own hourly
            price has no per-token component to report.
        """
        return ModelCost(cost_per_seat_hour_usd=self.cost_per_hour_usd)


class CloudBackendConfig(BaseModel):
    """Everything a cloud CellBackend needs beyond what `CellBackend.provision` already carries.

    Bundled into one value so a composition root builds it once per provider and hands it to both
    a real implementation's constructor and `hivemind.hive.backends.cloud.fake.
    FakeCloudCellBackend` alike.
    """

    model_config = _MODEL_CONFIG

    region: CloudRegion = Field(description="Which region this backend provisions Cells into.")
    credentials: CloudCredentials = Field(description="This provider's own access credentials.")
    pricing: PricingTag = Field(
        description="What this backend's own instance shape costs per hour, for Forage cost "
        "reporting (`CloudCellBackend.accrued_cost_usd`)."
    )
    instance_type: Annotated[str, Field(min_length=1, max_length=128)] = Field(
        description="The provider's own instance/flavor/machine-type name this backend "
        "provisions, e.g. 't3.medium' or 'e2-standard-4'."
    )


class CloudCellBackend(CellBackend, Protocol):
    """What every cloud CellBackend adds on top of the base protocol: cost accounting.

    A real implementation (post-1.0) must additionally, beyond what `CellBackend.provision`
    already requires of every backend:

    - Tag every instance it creates with this Hive's id, exactly as Docker's own container labels
      and QEMU's own `cell.json` do, so `list_cells`/orphan sweeps work the same way
      (ADR-0026: "orphans are recoverable from labels alone").
    - Carry the same cloud-init user-data documents QEMU renders
      (`hivemind.hive.backends.qemu.cloud_init.render_user_data`/`render_meta_data`): a real cloud
      provider's own "user data" field is the same NoCloud-shaped document a QEMU VM's seed image
      carries, so a real implementation reuses those renderers rather than inventing its own.
    - Provision every instance with no public IP and no inbound security-group rule; egress
      restricted to the Queen's own endpoint wherever the provider's own networking primitives
      allow it (ADR-0027: "a Virtual Cell exposes no inbound port"), mirroring
      `hivemind.hive.backends.qemu.network`'s own NONE-policy guestfwd exception.
    - Report spend through `accrued_cost_usd`, so it can be folded into a Forage cost figure via
      `CloudBackendConfig.pricing.as_model_cost()`.
    """

    @property
    def config(self) -> CloudBackendConfig:
        """This backend's own region, credentials and pricing."""
        ...

    async def accrued_cost_usd(self, cell_id: CellId) -> float:
        """Return how much `cell_id` has cost so far, in US dollars, for Forage cost reporting.

        Args:
            cell_id: The Cell to report on; must be one this backend actually provisioned.

        Returns:
            Elapsed hours this Cell has held its instance, times
            `config.pricing.cost_per_hour_usd`; keeps accruing until the Cell is destroyed, then
            stays fixed at whatever it reached (mirroring how a cloud bill stops the moment an
            instance terminates).

        Raises:
            hivemind.hive.errors.UnknownCellError: This backend has no record of `cell_id` (never
                provisioned, or its record was cleared some other way than `destroy`).
        """
        ...

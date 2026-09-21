# hivemind.hive.backends.cloud

The cloud package holds one `CellBackend` implementation per cloud provider the Hive can provision
a Virtual Cell on. ADR-0026 chose Docker first and QEMU second for Brood 1.0 and deliberately named
no cloud provider: cloud backends are **optional** and land post-1.0.

## Public API (roadmap step 5.12)

- `CloudCellBackend` (`base.py`): a `Protocol` extending `hivemind.hive.backends.base.CellBackend`
  with the one thing every cloud provider adds -- `config` (this backend's own region,
  credentials and pricing) and `accrued_cost_usd(cell_id)` (spend so far, for Forage cost
  reporting).
- `CloudBackendConfig` (`base.py`): `region`, `credentials`, `pricing`, `instance_type`.
- `CloudCredentials` (`base.py`): `key_id`, `secret`, both `pydantic.SecretStr` -- never in a
  `repr`, a log line or the Pheromone Trail.
- `CloudRegion` (`base.py`): a `NewType(str)` for a provider-defined region code.
- `PricingTag` (`base.py`): `cost_per_hour_usd`, plus `as_model_cost()` mapping it onto
  `hivemind.forage.models.sources.ModelCost.cost_per_seat_hour_usd` -- the same cost vocabulary
  every hosted model source already reports through, reused rather than reinvented.
- `FakeCloudCellBackend` (`fake.py`): this phase's own reference implementation. In-memory only,
  delegating `provision`/`destroy`/`list_cells`/`pause`/`resume` to an internally owned
  `hivemind.hive.backends.fake.FakeCellBackend`, and adding cost accrual driven by an injected
  `Clock`.

## What a real, post-1.0 provider must implement

Beyond `CellBackend`'s own contract (provision/destroy/list_cells/pause/resume, all-or-nothing
provisioning, idempotent destroy):

1. **Tag every instance with this Hive's id.** Exactly like Docker's container labels and QEMU's
   own `cell.json` (ADR-0026: "orphans are recoverable from labels alone"), so a startup orphan
   sweep and `hive cells abscond` work the same way regardless of which backend made a Cell.
2. **No public IP, no inbound security-group rule.** ADR-0027: a Virtual Cell exposes no inbound
   port under any policy. Egress should be restricted to the Queen's own reachable endpoint
   wherever the provider's own networking primitives allow it, mirroring
   `hivemind.hive.backends.qemu.network`'s NONE-policy guestfwd exception (the closest analogue:
   an explicit, narrow exception to an otherwise-closed network, not a general allowlist).
3. **Reuse the cloud-init documents QEMU already renders.** A cloud instance's own "user data"
   field is the same NoCloud-shaped `#cloud-config` document a QEMU VM's seed image carries;
   `hivemind.hive.backends.qemu.cloud_init.render_user_data`/`render_meta_data` are the renderers
   to call, not a second implementation of the same HIVEMIND_* environment contract.
4. **Report spend through `accrued_cost_usd`.** Fold `CloudBackendConfig.pricing.as_model_cost()`
   into whatever Forage's own cost accounting expects once that integration exists.
5. **Never import a vendor SDK outside this provider's own module.** Codingrules section 4 confines
   `subprocess` and vendor SDK imports to `hive/backends/*`; a cloud provider's own SDK (`boto3`,
   `google-cloud-compute`, ...) should additionally be confined to that provider's own module,
   mirroring `hivemind.hive.backends.docker.sdk_client`'s "the only module that may `import
   docker`" convention, and made an optional extra in `packages/hivemind/pyproject.toml`.
6. **Pass the contract suite.** `packages/hivemind/tests/contracts/test_cell_backend_contract.py`
   must pass with a new harness before the backend is registered anywhere else (codingrules 14.3).

## How to test this

- `packages/hivemind/tests/unit/hive/backends/cloud/`: unit tests for `base.py` and `fake.py`.
- `packages/hivemind/tests/contracts/test_cell_backend_contract.py`: `FakeCloudCellBackend` plugs
  in as the suite's fourth harness, alongside `fake`, `docker` and `qemu`.

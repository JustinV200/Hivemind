"""Tests for hivemind.cli.in_cell.config's offline limit: how long a cut-off Warden waits.

A Night Veil Cell's Warden can be cut off from the Queen when Tor dies, and clusters fully once it
has been cut off for the Hive's `[supervision] max_offline_s`. A Cell holds no manifest, so the
Hive tells it in `HIVEMIND_MAX_OFFLINE_S`; unset reads as the manifest's own shipped default. (A
value that is not a positive, finite number is refused where the environment is read, in
`tests/unit/manifest/test_env.py`.)

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/config.py (codingrules 5.1: split by feature from
    test_config.py).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.in_cell.config for build_runtime_config.
    - tests/unit/cli/in_cell/test_main_night_veil_redial.py for the limit at work.
"""

from __future__ import annotations

import pytest

from hivemind.cli.in_cell.config import InCellRuntimeConfig, build_runtime_config
from hivemind.manifest.env import read_in_cell_env
from hivemind.manifest.schema.supervision import DEFAULT_MAX_OFFLINE_S
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_hive_id, new_node_id
from waggle.signing import Ed25519Signer

_CLOCK = FakeClock()


def _config(**overrides: str) -> InCellRuntimeConfig:
    """Build this Cell's runtime config from a complete environment with `overrides`."""
    environ = {
        "HIVEMIND_QUEEN_WAGGLE_URL": "ws://host.docker.internal:8710",
        "HIVEMIND_CELL_ID": new_cell_id(_CLOCK),
        "HIVEMIND_HIVE_ID": new_hive_id(_CLOCK),
        "HIVEMIND_QUEEN_NODE_ID": new_node_id(_CLOCK),
        "HIVEMIND_CELL_SIGNING_KEY": Ed25519Signer.generate().private_key_bytes.hex(),
        "HIVEMIND_QUEEN_VERIFY_KEY": Ed25519Signer.generate().public_key_bytes.hex(),
    }
    environ.update(overrides)
    return build_runtime_config(read_in_cell_env(environ), _CLOCK)


def test_a_cell_the_hive_told_nothing_keeps_the_manifests_shipped_limit() -> None:
    assert _config().max_offline_s == DEFAULT_MAX_OFFLINE_S


@pytest.mark.parametrize(("text", "seconds"), [("90", 90.0), ("0.5", 0.5), (" 12.25 ", 12.25)])
def test_the_limit_the_hive_names_is_the_limit_the_cell_holds(text: str, seconds: float) -> None:
    assert _config(HIVEMIND_MAX_OFFLINE_S=text).max_offline_s == seconds

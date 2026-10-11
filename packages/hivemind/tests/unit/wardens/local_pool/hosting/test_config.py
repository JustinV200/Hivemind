"""Tests for hivemind.wardens.local_pool.hosting.config: ServerConfig and HostedCell are values.

Fits into the Hive:
    Mirrors src/hivemind/wardens/local_pool/hosting/config.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.local_pool.hosting.config for the module under test.
"""

from __future__ import annotations

import dataclasses

import pytest
from builders.hosting import make_server_config


def test_a_server_config_is_frozen() -> None:
    config = make_server_config()

    with pytest.raises(dataclasses.FrozenInstanceError):
        config.port = 1  # type: ignore[misc]


def test_a_server_config_keeps_its_argv_template_unsubstituted() -> None:
    config = make_server_config()

    assert "{port}" in config.argv
    assert all(source.spec.host_cell_id is None for source in config.sources)


def test_a_server_config_loads_on_start_by_default() -> None:
    assert make_server_config().load_on_start is True

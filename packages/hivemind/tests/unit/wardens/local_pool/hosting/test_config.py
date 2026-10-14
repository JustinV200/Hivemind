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

from hivemind.wardens.local_pool.hosting.config import bounded


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


def test_bounded_lowers_a_start_up_wait_to_a_shorter_deadline() -> None:
    config = make_server_config(startup_timeout_s=30.0)

    assert bounded(config, 5.0).startup_timeout_s == 5.0
    assert config.startup_timeout_s == 30.0  # The manifest's row is never edited.


def test_bounded_never_stretches_the_manifests_own_wait() -> None:
    config = make_server_config(startup_timeout_s=3.0)

    assert bounded(config, 60.0) is config  # An order can hurry a start, not slow one.


def test_bounded_with_no_deadline_keeps_the_row_as_it_is() -> None:
    config = make_server_config()

    assert bounded(config, None) is config

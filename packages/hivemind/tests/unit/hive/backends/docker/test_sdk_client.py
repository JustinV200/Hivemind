"""Unit tests for hivemind.hive.backends.docker.sdk_client: the lazy import and pure helpers.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors
    src/hivemind/hive/backends/docker/sdk_client.py (codingrules section 3). SdkDockerClient's own
    docker-py calls need a real daemon and are exercised by
    packages/hivemind/tests/integration/test_docker_backend.py instead; this module covers what
    can be proven with no daemon: the lazy-import guard and the "Created" timestamp parser.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.backends.docker.sdk_client for SdkDockerClient, under test.
    - packages/hivemind/tests/integration/test_docker_backend.py for the real-daemon coverage.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

import pytest

from hivemind.common.errors import ConfigurationError
from hivemind.hive.backends.docker.sdk_client import SdkDockerClient, _parse_created_at


def test_missing_docker_package_raises_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A None entry in sys.modules is CPython's own documented way to make `import docker` behave
    # as though the package is not installed, without needing it actually absent from the venv --
    # this venv has the "hivemind[docker]" extra installed so ruff/mypy/CI can check this module.
    monkeypatch.setitem(sys.modules, "docker", None)

    with pytest.raises(ConfigurationError, match="hivemind\\[docker\\]"):
        SdkDockerClient()


def test_parse_created_at_handles_nanosecond_precision() -> None:
    parsed = _parse_created_at("2024-01-15T10:30:00.123456789Z")

    assert parsed == datetime(2024, 1, 15, 10, 30, 0, 123456, tzinfo=UTC)


def test_parse_created_at_handles_no_fractional_seconds() -> None:
    parsed = _parse_created_at("2024-01-15T10:30:00Z")

    assert parsed == datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)


def test_parse_created_at_falls_back_to_now_for_none() -> None:
    before = datetime.now(UTC)

    parsed = _parse_created_at(None)

    assert before <= parsed <= datetime.now(UTC)


def test_parse_created_at_falls_back_to_now_for_garbage() -> None:
    before = datetime.now(UTC)

    parsed = _parse_created_at("not-a-timestamp")

    assert before <= parsed <= datetime.now(UTC)

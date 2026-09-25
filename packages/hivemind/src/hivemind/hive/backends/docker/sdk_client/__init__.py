"""Wrap the `docker` SDK behind DockerClientPort: the only package allowed to import it.

`SdkDockerClient` is `DockerClientPort`'s real implementation, over `docker.DockerClient` (the
`docker` PyPI package, the Docker Engine SDK for Python). Codingrules section 8.6 confines a
vendor LLM SDK to `llm/providers/<name>/`; this package is `hive/backends/docker`'s own equivalent
for a vendor infrastructure SDK, and the root `pyproject.toml`'s import-linter contract enforces
it the same way, down to the one module that imports `docker` (`client`, lazily, inside
`SdkDockerClient.__init__`). Every docker-py call is blocking, so each runs under
`asyncio.to_thread` (codingrules section 11): the container calls' blocking halves are the class's
own methods, and the network, volume and image calls' are module functions, one module per
docker-py collection, each taking one `DockerHandle`.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hivemind.hive.backends.docker`. Implements
    `hivemind.hive.backends.docker.client.DockerClientPort`; constructed by the composition root
    (`hivemind.cli.compose.virtual_cell_backends`) when `[virtual_cells] backend = "docker"`.
    Calls into the `docker` package only, plus `hivemind.common.errors` for the one configuration
    error a missing install raises.

Key invariants:
    - Importing this package never imports `docker`: only constructing a `SdkDockerClient` does,
      so `hivemind.hive.backends.docker` imports cleanly without the `hivemind[docker]` extra.
    - No `docker.*` type or exception crosses this package's surface: each becomes a
      `hivemind.hive.backends.docker.client.DockerClientError`.

See Also:
    - .claude/codingrules.md section 8.6 for the vendor-SDK confinement pattern this package
      mirrors for infrastructure instead of LLM providers.
    - hivemind.hive.backends.docker.fake for FakeDockerClient, the in-memory implementation tests
      use instead of this one.

Public API:
    - SdkDockerClient (`client.py`): the real DockerClientPort, over the `docker` SDK.
"""

from hivemind.hive.backends.docker.sdk_client.client import SdkDockerClient

__all__ = ["SdkDockerClient"]

"""Bundle the docker-py client and module into the one argument each blocking half takes.

`SdkDockerClient` runs every docker-py call on a worker thread. Its network, volume and image
calls' blocking halves are module functions in this package's `networks`, `volumes` and `images`
modules rather than methods, which keeps the class within codingrules section 5.1's 200-line
limit; the same section's five-parameter limit is why each takes one `DockerHandle` instead of
the client and the lazily imported `docker` module separately.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hivemind.hive.backends.docker.sdk_client`. Built by
    `SdkDockerClient`; taken by every blocking half in its `networks`, `volumes` and `images`
    siblings. Holds nothing but the two references.

Key invariants:
    - This module never imports `docker`: `docker_module` is the module `SdkDockerClient`
      imported, handed in, so a handle's `errors` and `types` are the real SDK's own.

See Also:
    - hivemind.hive.backends.docker.sdk_client.client for SdkDockerClient, which builds it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["DockerHandle"]


@dataclass(frozen=True, slots=True)
class DockerHandle:
    """`SdkDockerClient`'s docker-py client and `docker` module, handed to a blocking half.

    Attributes:
        client: The `docker.DockerClient` every call goes through.
        docker_module: The `docker` module itself, for its `errors` and `types`.
    """

    client: Any
    docker_module: Any

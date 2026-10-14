"""Write a Hive Manifest with the Swarm on, for the composition tests and the Swarm e2e.

`swarm_manifest` is `builders.cli.fake_manifest` (every model slot on one scripted `kind = "fake"`
provider, short supervision intervals, a scratch root and a database under the test's own
directory) with a `[swarm]` section appended that turns the Swarm on. The section's other keys are
whatever the test names, as TOML values, so a test about TLS passes `tls_cert` and `tls_key` and one
about the beat passes `heartbeat_interval_s`; everything it leaves out is the manifest's default
(listening on loopback, plaintext). A test that starts the listener picks its port with
`builders.swarm.live.free_port`.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the composition tests under
    tests/unit/cli/compose/swarm and by the Swarm's end-to-end test.

Key invariants:
    - A manifest written here loads: its `[swarm]` section is validated by the same schema a real
      operator's is.

See Also:
    - builders.cli for fake_manifest, the manifest this one extends.
    - hivemind.manifest.schema.swarm for the keys the section accepts.
"""

from __future__ import annotations

import json
from pathlib import Path

from builders.cli import fake_manifest

__all__ = ["swarm_manifest"]


def swarm_manifest(tmp_path: Path, **swarm: bool | int | float | str) -> Path:
    """Write `<tmp_path>/hive.toml` with `[swarm] enabled = true` and the keys named in `swarm`.

    Args:
        tmp_path: A test's own tmp_path; the manifest, the Hive's database and its scratch go there.
        **swarm: `[swarm]` keys to set, beyond `enabled = true`; `enabled=False` writes the section
            but leaves the Swarm off.

    Returns:
        The written manifest's path.
    """
    path = fake_manifest(tmp_path)
    keys: dict[str, bool | int | float | str] = {"enabled": True, **swarm}
    lines = "\n".join(f"{key} = {_toml(value)}" for key, value in keys.items())
    path.write_text(f"{path.read_text(encoding='utf-8')}\n[swarm]\n{lines}\n", encoding="utf-8")
    return path


def _toml(value: bool | int | float | str) -> str:
    """Spell a Python scalar as a TOML value."""
    if isinstance(value, bool):  # Before int: a bool is an int in Python.
        return "true" if value else "false"
    return json.dumps(value)

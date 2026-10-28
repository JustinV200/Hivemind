"""Provide `hive init`: the starter Hive Manifest and the first operator password.

Roadmap 14.2. One command (`command`): write `hive.toml` for a chosen provider path with the Hive
Stand enabled, never over an existing one without `--force`, then set the operator's password.

Fits into the Hive:
    Layer 7 (the terminal), inside `hivemind.cli`. Registered by `hivemind.cli.app`. Calls into
    this package's own modules only.

Key invariants:
    - This file holds re-exports and `__all__` only.

See Also:
    - hivemind.manifest.starter for the templates.

Public API:
    - init_command, InitCommand: `hive init` and the command class carrying its options
      (command).
"""

from hivemind.cli.init.command import InitCommand, init_command

__all__ = ["InitCommand", "init_command"]

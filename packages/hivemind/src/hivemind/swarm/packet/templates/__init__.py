"""Hold the Pollen Packet's templates: the two installer scripts and the README; carries no code.

`hive swarm packet` copies one installer (`pollinate.ps1` for Windows, `pollinate.sh` for Linux and
macOS) and `README.txt` into every packet, after `hivemind.swarm.packet.writer` fills their
`@@NAME@@` placeholders with validated values (the Hive Stand's host and port, the platform, the
label and the expiry). They are read through `importlib.resources.files(...)`, which addresses
packages by dotted name, which is why this is a real package with an `__init__.py`: the files ship
inside the installed distribution whether the Hive runs from a checkout or a wheel.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down). Read by
    hivemind.swarm.packet.writer; nothing imports this module for a Python name.

Key invariants:
    - No template holds or prints a secret: the installers read the invite and the tailnet key
      from the packet's `secrets/` folder, pass them by file or standard input, and delete it.
    - Every placeholder a template uses is one the writer fills.

See Also:
    - hivemind.swarm.packet.writer for the placeholders.
    - docs/runbooks/enrolling-a-device.md, "Pollen Packet over Tailscale".

Public API: none; this package holds data files (the scripts and the README), not importable names.
"""

__all__: list[str] = []

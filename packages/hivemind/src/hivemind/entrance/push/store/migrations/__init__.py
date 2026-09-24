"""Hold the push tables' numbered SQL migration series; carries no code of its own.

Every file directly under this directory matching ``NNNN_name.sql``
(``hivemind.common.migrations.MIGRATION_FILE_PATTERN``) is one migration in the ``entrance_push``
series. ``hivemind.entrance.push.store.sqlite.apply_push_migrations`` reads them through
``importlib.resources.files("hivemind.entrance.push.store.migrations")``, which is why this is a
real package (an ``__init__.py``) rather than a bare directory: ``importlib.resources`` addresses
packages by dotted name, so the files ship inside the installed distribution whether the Hive
runs from a checkout or a wheel.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.push.store``. Read by
    ``hivemind.entrance.push.store.sqlite``; nothing imports this module for a Python name.

Key invariants:
    - Every ``.sql`` file here is numbered contiguously from 0001 with no gaps or duplicates
      (``hivemind.common.migrations.load_migrations`` enforces it at load time).
    - No migration file opens or closes a transaction; ``hivemind.common.migrations`` wraps each.

See Also:
    - hivemind.common.migrations for load_migrations and apply_migrations.
    - docs/adr/0006-sqlite-as-the-single-hive-store.md for the numbered-series decision.

Public API: none; this package holds data files (``.sql`` migrations), not importable names.
"""

__all__: list[str] = []

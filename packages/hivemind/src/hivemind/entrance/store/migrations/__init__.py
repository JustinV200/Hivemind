"""Hold the Entrance tables' numbered SQL migration series; carries no code of its own.

Every file directly under this directory matching ``NNNN_name.sql`` (``hivemind.common.migrations.
MIGRATION_FILE_PATTERN``) is one migration in the Hive Entrance's (the Hive's one HTTP door) own
series, recorded under subsystem ``"entrance"`` in the Hive database's shared ``schema_migrations``
table. ``hivemind.entrance.store.sqlite.apply_entrance_migrations`` reads them through
``importlib.resources.files("hivemind.entrance.store.migrations")``, which is why this is a real
package: ``importlib.resources`` addresses packages by dotted name, so the files ship inside the
installed distribution whether the Hive runs from a checkout or a wheel.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.store``. Read by
    ``hivemind.entrance.store.sqlite``; nothing imports this module for a Python name.

Key invariants:
    - The series is numbered contiguously from 0001; ``0001_create_entrance.sql`` creates the
      operator, device and invite tables, and ``0002_create_sessions.sql`` the tables login,
      sessions, step-up and the Entrance Reducer keep (sessions and spent nonces, login failures
      and known networks, pending confirmations, the Entrance mode). Push subscriptions keep
      their own series (``hivemind.entrance.push.store``).
    - No migration's text opens or closes a transaction: ``hivemind.common.migrations`` wraps
      each file itself.

See Also:
    - hivemind.common.migrations for load_migrations and apply_migrations.
    - docs/adr/0006-sqlite-as-the-single-hive-store.md for the numbered-series decision.

Public API: none; this package holds data files (``.sql`` migrations), not importable names.
"""

__all__: list[str] = []

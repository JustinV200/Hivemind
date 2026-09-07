"""Test composition root for the hygiene checker scripts under scripts/.

Per codingrules section 8.2, the composition root for a test suite is exactly one file. This one
is empty of fixtures because every checker test builds its own sample files directly with
pytest's `tmp_path`, needing nothing shared; it exists so the convention (every tests/ tree has a
conftest.py, per the sibling packages/*/tests/conftest.py files) holds here too.

Fits into the Hive:
    Layer: none (test infrastructure for dev tooling, not shipped). Loaded automatically by
    pytest before every test module under scripts/tests/.

Key invariants:
    - None yet: no fixtures are defined.

See Also:
    - packages/waggle/tests/conftest.py for the sibling file this one follows the shape of.
"""

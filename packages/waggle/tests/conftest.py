"""Test composition root for the waggle package.

This module is where waggle's test suite wires together its collaborators (fakes, in-memory
transports, and similar test doubles) once real modules exist here to test. Per codingrules
section 8.2, the composition root for any entry point — a CLI, a gateway, or a test suite — is
exactly one file; for tests, that file is this one.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Loaded automatically by pytest before every test
    module under packages/waggle/tests/. Nothing depends on it; it depends on nothing yet.

Key invariants:
    - None yet: no fixtures are defined until step 0.1's sibling modules land.

See Also:
    - .claude/codingrules.md section 8.2 for the composition-root rule this file follows.
"""

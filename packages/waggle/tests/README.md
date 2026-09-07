# waggle tests

Tests for the waggle package: the Hive's shared wire protocol (message envelopes, ids, the clock,
and the standard long-running loop shape). This tree has no fixed internal shape yet beyond
`conftest.py`, the test composition root; it fills in as `packages/waggle/src/waggle/` grows,
starting with the message and transport contract suites in phase 1.

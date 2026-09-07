# hivemind.manifest

The manifest package loads and validates the Hive Manifest, the TOML configuration file that
describes one Hive (a running instance of the whole system): which backend provisions Virtual
Cells, which model providers are allowed, and similar operator choices. Every field is validated
by a pydantic model before anything else in the Hive reads it.

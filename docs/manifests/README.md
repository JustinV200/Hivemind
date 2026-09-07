# Annotated example Hive Manifests

A Hive Manifest is the TOML configuration file that describes one running Hive (a Hive is one
deployment of the system: a Queen, its Wardens, and the Cells they supervise): which Virtual Cell
backend it uses, which model providers it may call, and the other operator choices that shape how
that Hive behaves. This directory will hold annotated example manifests, each showing what every
field means in context, once `hivemind.manifest` can load and validate them. The first real
manifest lands in phase 3, alongside the code that reads it.

Among those examples, `full.toml` will list every field the manifest schema supports, kept honest
by a test that loads every example manifest in this directory so the docs cannot drift from the
schema. This directory will also document the `HIVEMIND_` prefixed environment variables that are
allowed to override manifest values, read in exactly one place (`manifest/env.py`), as described
in coding rules section 13.

Until phase 3, this file is a placeholder: there are no example manifests here yet.

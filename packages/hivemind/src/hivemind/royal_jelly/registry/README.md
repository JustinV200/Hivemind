# hivemind.royal_jelly.registry

The registry package is the CombRegistry of promoted tools, tracked separately at hive scope
(usable anywhere) and Cell scope (usable only on one Cell). `protocol.py` is the contract,
`sqlite.py` the durable store (`writes.py` holds its three transactions, `sql.py` its statements,
`migrations/` the numbered schema), `fake.py` the in-memory fake, `rules.py` the pure policy both
share, `principals.py` and `rows.py` the values. See the package README one level up for the
promotion checks.

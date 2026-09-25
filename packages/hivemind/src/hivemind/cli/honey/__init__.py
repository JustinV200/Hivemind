"""Assemble `hive honey`: query, count, ripen, re-embed, browse and relabel the Honey Store.

Roadmap step 7.11 gives the Honey Store (the Hive's cold-tier knowledge base: Nectar, raw
findings, ripened into Honey, labelled searchable knowledge) its operator commands, and step 7.10
its folder browser. They would not fit codingrules section 5.1's 300-line file limit as one flat
module, so they are split the way `hivemind.cli.memory` splits its own: `query.py` (`query`,
`stats`), `browse.py` (`ls`, `cat`, `propose`), `maintain.py` (`ripen --now` -- the House Bee's
whole pass: drains queued notes, then ripens -- `reembed`, `relabel`), a shared `context.py` (the
group's options, the operator's reader, opening the store),
`sources.py` (the browser's memory-backed wax and Bee Bread sources) and `render.py` (printing).
This face only wires them together (codingrules section 5.4). `--manifest`, `--db` and
`--clearance` belong to the group, so they come right after `honey` and before the subcommand
(`hive honey --manifest hive.toml --clearance C1 ls /hive`).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Imported by `hivemind.cli.app`, which mounts `app`
    under the `honey` command-group name. Calls into `hivemind.cli.honey.context`, `.query`,
    `.browse` and `.maintain` only.

Key invariants:
    - None: this file holds composition only; no command logic is defined here.

See Also:
    - .claude/roadmap.md steps 7.10 and 7.11 for this package's own deliverable.
    - hivemind.honey_store.browse for the folder tree `ls`, `cat` and `propose` walk.

Public API:
    - app: the `hive honey` Typer app (`query`, `stats`, `ripen`, `reembed`, `ls`, `cat`,
      `propose`, `relabel`).
"""

import typer

from hivemind.cli.honey.browse import cat_command, ls_command, propose_command
from hivemind.cli.honey.context import honey_callback
from hivemind.cli.honey.maintain import reembed_command, relabel_command, ripen_command
from hivemind.cli.honey.query import query_command, stats_command

__all__ = ["app"]

app = typer.Typer(
    name="honey",
    help="Search, browse and maintain the Honey Store, the Hive's cold-tier knowledge base.",
)
app.callback()(honey_callback)
app.command("query")(query_command)
app.command("stats")(stats_command)
app.command("ripen")(ripen_command)
app.command("reembed")(reembed_command)
app.command("ls")(ls_command)
app.command("cat")(cat_command)
app.command("propose")(propose_command)
app.command("relabel")(relabel_command)

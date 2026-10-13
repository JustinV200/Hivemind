"""Assemble `hive basket`: list, show, deposit, ship, evict and retire the Basket's blobs.

Roadmap steps 9.2a and 9.10 give the Basket (the Hive's store of inert blobs, kept by content hash
and signed with the Hive key, ADR-0048) its operator commands. They would not fit codingrules
section 5.1's 300-line file limit as one module, so they are split the way `hivemind.cli.honey`
splits its own: `listing.py` (`list`, `show`), `deposit.py` (`deposit`), `lifecycle.py` (`ship`,
`evict`, `retire`), a shared `context.py` (the group's `--manifest`, the stores and the deposit
door) and `render.py` (printing). This face only wires them together (codingrules section 5.4).
`--manifest` belongs to the group, so it comes right after `basket` and before the subcommand
(`hive basket --manifest hive.toml list`). A blob is named by its sha256 or any unambiguous prefix.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Imported by `hivemind.cli.app`, which mounts `app`
    under the `basket` command-group name. Calls into `hivemind.cli.basket.context`, `.listing`,
    `.deposit` and `.lifecycle` only.

Key invariants:
    - None: this file holds composition only; no command logic is defined here.

See Also:
    - .claude/roadmap.md steps 9.2a and 9.10 for this package's own deliverable.
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md for what these commands drive.

Public API:
    - app: the `hive basket` Typer app (`list`, `show`, `deposit`, `ship`, `evict`, `retire`).
"""

import typer

from hivemind.cli.basket.context import basket_callback
from hivemind.cli.basket.deposit import DepositCommand, deposit_command
from hivemind.cli.basket.lifecycle import evict_command, retire_command, ship_command
from hivemind.cli.basket.listing import list_command, show_command

__all__ = ["app"]

app = typer.Typer(
    name="basket",
    help="List, deposit, ship, evict and retire the Basket's inert blobs.",
)
app.callback()(basket_callback)
app.command("list")(list_command)
app.command("show")(show_command)
app.command("deposit", cls=DepositCommand)(deposit_command)
app.command("ship")(ship_command)
app.command("evict")(evict_command)
app.command("retire")(retire_command)

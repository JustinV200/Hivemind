"""Write (or check) the Landing Board's committed OpenAPI document, docs/entrance/openapi.json.

The Landing Board (the Hive Entrance's versioned API, ADR-0034) is a published contract: programs
are written from the committed document alone, so it is generated from the route table both
listeners are built from, never edited by hand. Run this after changing a route, a model or a
signed string, and commit the result; `--check` writes nothing and exits 1 when the committed
document differs from what the route table renders (CI and the drift test run it that way).

Usage:
    uv run --frozen python scripts/write_landing_board.py            # rewrite when out of date
    uv run --frozen python scripts/write_landing_board.py --check    # exit 1 on drift

Fits into the Hive:
    Layer: none (a dev-time tool, not shipped code). Calls into
    `hivemind.entrance.landing_board`, the one place the document is rendered.

Key invariants:
    - The document is written only when it differs, and always from `render_document`.

See Also:
    - hivemind.entrance.landing_board for how the document is built.
    - docs/adr/0034-landing-board-versioning-and-push.md for the contract's role.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from hivemind.entrance.landing_board import DOCUMENT_PATH, render_document

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent  # scripts/ sits at the repository root.

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    """Rewrite the committed document when it drifted, or with ``--check`` only report it.

    Args:
        argv: Command-line arguments, excluding the program name; None means ``sys.argv[1:]``.

    Returns:
        0 when the document is current (or was just rewritten), 1 when ``--check`` found drift.
    """
    parser = argparse.ArgumentParser(description="Write the Landing Board's OpenAPI document.")
    parser.add_argument("--check", action="store_true", help="Report drift; write nothing.")
    parser.add_argument(
        "--path",
        type=Path,
        default=REPOSITORY_ROOT / DOCUMENT_PATH,
        help="Where the document lives (default: docs/entrance/openapi.json).",
    )
    args = parser.parse_args(argv)
    path: Path = args.path
    rendered = render_document()
    current = path.read_bytes() if path.exists() else None
    # Nothing to do when the committed bytes are exactly what the route table renders.
    if current == rendered:
        return 0
    if args.check:
        print(f"{path}: out of date; run scripts/write_landing_board.py and commit the result.")
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(rendered)
    print(f"{path}: written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

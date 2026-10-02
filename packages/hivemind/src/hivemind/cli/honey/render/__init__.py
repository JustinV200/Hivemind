"""Print what `hive honey` returns: listings, documents, hits, counts, proposals, slot status.

Output formatting only (codingrules section 2's CLI row): every value printed here was produced
by `hivemind.honey_store` -- a `BrowseListing`, a document, a `HoneyResponse`, a `HoneyStats`, a
label lowering proposal (ADR-0038) and what the judge's review did, a prune's outcome -- and this
package decides only how it looks on a terminal, or as JSON for `--json`. Retrieved text is shown
as reference data, never run (codingrules section 15, "Nothing is executed from the Honey Store").
The single module reached its size limit (phase 7 handoff item 6), so it is split by what is
printed: `browse` (`ls` and `cat`), `store` (query hits, counts, slot status, a prune), `review`
(label lowering proposals and `ReviewEntry`), `ripen` (what `ripen` found waiting, or the totals
of `ripen --now`'s passes), and `common`, the placeholders and formats the others share.
This face only re-exports them (codingrules section 5.4).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the CLI's `honey` command group. Imported
    by `hivemind.cli.honey.query`, `.browse`, `.maintain` and `.review`. Calls into this
    package's own modules only.

Key invariants:
    - None: this file holds re-exports only; no printing logic is defined here.

See Also:
    - hivemind.honey_store.browse for BrowseListing and the document shapes.
    - hivemind.honey_store.lowering for LoweringProposal and ReviewOutcome.
    - waggle.messages.honey for HoneyResponse and HoneyHit.

Public API:
    - print_listing, print_document, document_json: a folder page and one document (browse).
    - print_response, print_stats, print_slot, print_advice, print_prune: hits, counts, a
      slot and manifest advice about it, a prune (store).
    - ReviewEntry, print_review, print_review_entry, review_json, review_counts,
      print_decision: label lowering proposals (review).
    - RipenTotals, WaitingCounts, print_ripen, print_waiting, ripen_json: `ripen` with and
      without `--now` (ripen).
"""

from hivemind.cli.honey.render.browse import document_json, print_document, print_listing
from hivemind.cli.honey.render.review import (
    ReviewEntry,
    print_decision,
    print_review,
    print_review_entry,
    review_counts,
    review_json,
)
from hivemind.cli.honey.render.ripen import (
    RipenTotals,
    WaitingCounts,
    print_ripen,
    print_waiting,
    ripen_json,
)
from hivemind.cli.honey.render.store import (
    print_advice,
    print_prune,
    print_response,
    print_slot,
    print_stats,
)

__all__ = [
    "ReviewEntry",
    "RipenTotals",
    "WaitingCounts",
    "document_json",
    "print_advice",
    "print_decision",
    "print_document",
    "print_listing",
    "print_prune",
    "print_response",
    "print_review",
    "print_review_entry",
    "print_ripen",
    "print_slot",
    "print_stats",
    "print_waiting",
    "review_counts",
    "review_json",
    "ripen_json",
]

"""The untrusted-content scanner: read outside text before a model does, flag what steers it.

Outside text reaches a bee's prompt at a handful of places: a tool's result, a Cell session's own
output, a human's chat message through the Landing Board (the Hive Entrance's public contract),
and in phase 7 a Honey hit (the cold knowledge tier) at assembly and Nectar (raw material waiting
to ripen) at intake. Roadmap step 10.6b puts one deterministic scanner, with no model, at each of
them (ADR-0043). Its patterns are data (`hivemind.guard.defaults/untrusted-content.toml`, one
family per table, each with a weight: imperatives addressed to the model, role and identity
overrides, secret paths beside exfiltration verbs, encoded blobs over a size, tool-call-shaped
text, hosts outside the task's targets); a text's score is the sum of the families it trips, and
`[guard.untrusted_content]` in the Hive Manifest says per Comb Shield tier (the Cell's security
tier) at which score the text is labelled harder and at which it is dropped. Every flag is recorded
as `guard.injection_suspected` with a keyed hash of the text, never the text. A flag never stops a
bee; only the Guard Bee's correlation rule (roadmap 10.6) escalates.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.guard`. Called by
    `hivemind.workers.tools` (tool results and session output), `hivemind.queen.ticks.awake` (chat
    messages) and, from phase 7, Honey retrieval and Nectar intake; its verdicts are applied by
    `hivemind.memory` (`render_untrusted`, `assemble`). Calls into `hivemind.cell`,
    `hivemind.common.secrets`, `hivemind.guard.capabilities`, `hivemind.manifest.schema.guard` and
    `hivemind.pheromone`.

Key invariants:
    - Deterministic and model-free: the same text, patterns, tier and targets always give the same
      verdict, and nothing here imports `hivemind.llm`.
    - Input is bounded before matching and every data pattern has only bounded repetitions, so a
      hostile document can never make the scanner the slow path.
    - The trail sees ids, counts, family names and a keyed hash; never the scanned text.

See Also:
    - docs/adr/0043-guard-bee-requests-queen-only-isolation-and-tainted-memory.md.
    - docs/guard/untrusted-content.md for the families, thresholds and seams, explained.
    - .claude/roadmap.md step 10.6b.

Public API:
    - ScanSource, ScanAction, ScanVerdict, HASH_PREFIX: where text came from and what was decided
      (verdict).
    - ScanPatterns, PatternFamily, ProximityFamily, RunFamily, HostFamily, CompiledPatterns,
      CompiledFamily, load_scan_patterns, unbounded_repetition, PATTERNS_FILENAME: the pattern
      file and its loader (patterns).
    - Detector, PatternDetector, ProximityDetector, RunDetector, HostDetector, normalise: how each
      family matches (detectors).
    - ScanScore, FamilyHit, score_text, decide, thresholds_for: the pure decision (score).
    - ContentHasher, SCANNER_KEY_NAME, KEY_BYTES: the keyed hash of flagged text (hasher).
    - ContentScanner, ScanSite, ScanRecorder, default_content_scanner, INJECTION_SUSPECTED_KIND:
      the scanner itself, which records every flag (scanner).
"""

from hivemind.guard.scanner.detectors import (
    Detector,
    HostDetector,
    PatternDetector,
    ProximityDetector,
    RunDetector,
    normalise,
)
from hivemind.guard.scanner.hasher import KEY_BYTES, SCANNER_KEY_NAME, ContentHasher
from hivemind.guard.scanner.patterns import (
    PATTERNS_FILENAME,
    CompiledFamily,
    CompiledPatterns,
    HostFamily,
    PatternFamily,
    ProximityFamily,
    RunFamily,
    ScanPatterns,
    load_scan_patterns,
    unbounded_repetition,
)
from hivemind.guard.scanner.scanner import (
    INJECTION_SUSPECTED_KIND,
    ContentScanner,
    ScanRecorder,
    ScanSite,
    default_content_scanner,
)
from hivemind.guard.scanner.score import FamilyHit, ScanScore, decide, score_text, thresholds_for
from hivemind.guard.scanner.verdict import HASH_PREFIX, ScanAction, ScanSource, ScanVerdict

__all__ = [
    "HASH_PREFIX",
    "INJECTION_SUSPECTED_KIND",
    "KEY_BYTES",
    "PATTERNS_FILENAME",
    "SCANNER_KEY_NAME",
    "CompiledFamily",
    "CompiledPatterns",
    "ContentHasher",
    "ContentScanner",
    "Detector",
    "FamilyHit",
    "HostDetector",
    "HostFamily",
    "PatternDetector",
    "PatternFamily",
    "ProximityDetector",
    "ProximityFamily",
    "RunDetector",
    "RunFamily",
    "ScanAction",
    "ScanPatterns",
    "ScanRecorder",
    "ScanScore",
    "ScanSite",
    "ScanSource",
    "ScanVerdict",
    "decide",
    "default_content_scanner",
    "load_scan_patterns",
    "normalise",
    "score_text",
    "thresholds_for",
    "unbounded_repetition",
]

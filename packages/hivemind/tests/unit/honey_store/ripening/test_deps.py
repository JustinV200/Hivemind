"""Tests for hivemind.honey_store.ripening.deps: RipenerDeps and its default embed gate.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/ripening/deps.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.ripening.deps for the module under test.
"""

from __future__ import annotations

from pathlib import Path

from builders.honey import make_ripener_deps, open_test_honey_store

from hivemind.llm import DirectEmbedGate
from waggle.clock import FakeClock


async def test_ripener_deps_default_to_no_bindings_and_a_direct_embed_gate(tmp_path: Path) -> None:
    clock = FakeClock()
    deps = make_ripener_deps(await open_test_honey_store(tmp_path, clock), clock)

    assert deps.ripener is None and deps.embedder is None and deps.call_gate is None
    assert isinstance(deps.embedding_gate(), DirectEmbedGate)


async def test_ripener_deps_embedding_gate_is_the_wired_one_when_given(tmp_path: Path) -> None:
    clock = FakeClock()
    gate = DirectEmbedGate()
    deps = make_ripener_deps(await open_test_honey_store(tmp_path, clock), clock, embed_gate=gate)

    assert deps.embedding_gate() is gate

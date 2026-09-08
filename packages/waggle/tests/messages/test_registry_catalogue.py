"""Tests for the full Waggle catalogue: sixty-six kinds, one example of each, the package face.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Pins the registry's kind order to the spec's
    catalogue table, then loads the EXAMPLES tuple of each family's root test module by file
    path (the test tree has no packages, so a sibling test module cannot be imported by name)
    and checks that every example is registered, resolves back to its kind and survives wrap,
    encode and decode unchanged; that the ten tuples together cover every registered model
    exactly once; and that waggle.messages and each family package re-export every registered
    class.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.messages.catalogue and waggle.messages.registry for the modules under test.
    - tests/test_spec_drift.py for the same list checked against docs/waggle/spec.md.
    - tests/messages/test_registry.py for the lookups and the import-time checks.
"""

from __future__ import annotations

import importlib
import importlib.util
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import pytest

from waggle import messages
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope
from waggle.ids import new_message_id
from waggle.messages.base import MessageShape, WaggleMessage
from waggle.messages.registry import MESSAGE_SPECS, all_kinds, kind_for, spec_for

# The ten families of the spec's catalogue, each a package under waggle/messages/ whose root
# test module, tests/messages/<family>/test_<root>.py, defines EXAMPLES: one valid instance of
# every message class of that family.
EXAMPLE_MODULES: dict[str, str] = {
    "task": "assignment",
    "supervision": "oversight",
    "forage": "grants",
    "cell": "status",
    "session": "commands",
    "honey": "exchange",
    "tool": "authoring",
    "capping": "proposals",
    "swarm": "enrolment",
    "control": "protocol",
}
FAMILIES = tuple(EXAMPLE_MODULES)

# Every kind of the spec's catalogue table (docs/waggle/spec.md section 8), in the table's order.
CATALOGUE_KINDS: tuple[str, ...] = (
    "task.assign",
    "task.progress",
    "task.result",
    "task.cancel",
    "task.pause",
    "task.resume",
    "supervision.heartbeat",
    "supervision.alarm_raised",
    "supervision.alarm_resolved",
    "supervision.inspect",
    "supervision.inspect_reply",
    "supervision.intervene",
    "supervision.question",
    "supervision.answer",
    "forage.capacity_report",
    "forage.grant_issued",
    "forage.grant_revoked",
    "forage.request",
    "forage.reply",
    "forage.hosting_decided",
    "forage.ceilings_set",
    "forage.plan_written",
    "cell.ready",
    "cell.heartbeat",
    "cell.teardown_request",
    "cell.request",
    "cell.lease_opened",
    "cell.lease_released",
    "cell.wax_proposed",
    "cell.wax_written",
    "cell.wax_cleared",
    "session.open",
    "session.exec",
    "session.stdin",
    "session.output",
    "session.exit",
    "session.put_file",
    "session.get_file",
    "session.close",
    "honey.nectar_deposit",
    "honey.query",
    "honey.response",
    "tool.request",
    "tool.promoted",
    "tool.invoke",
    "tool.result",
    "capping.proposal_submitted",
    "capping.check_result",
    "capping.verdict",
    "capping.postcondition_result",
    "capping.rollback_done",
    "swarm.enrol_request",
    "swarm.enrol_accept",
    "swarm.device_heartbeat",
    "swarm.nuc_promote",
    "swarm.nuc_promoted",
    "swarm.trail_segment_sync",
    "control.ping",
    "control.pong",
    "control.error",
    "control.shutdown",
    "control.cluster",
    "control.wake",
    "control.human_message",
    "control.mask_override",
    "control.queen_moved",
)


def _load_examples(family: str) -> tuple[WaggleMessage, ...]:
    """Load ``EXAMPLES`` from tests/messages/<family>/test_<root>.py by file path.

    pytest runs with --import-mode=importlib and the test tree has no __init__.py, so a test
    module has no importable name; loading it by path under a private name is the one way to
    read a sibling's module-level tuple. Defined before the constants built from it because
    those are evaluated at import.
    """
    path = Path(__file__).parent / family / f"test_{EXAMPLE_MODULES[family]}.py"
    spec = importlib.util.spec_from_file_location(f"waggle_examples_{family}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}: no import spec for it.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    raw: object = module.EXAMPLES
    # Typed by inspection rather than trusted, so mypy sees WaggleMessage instances, not Any.
    if not isinstance(raw, tuple):
        raise TypeError(f"{path.name} must define EXAMPLES as a tuple, got {type(raw).__name__}.")
    examples: list[WaggleMessage] = []
    for item in raw:
        if not isinstance(item, WaggleMessage):
            raise TypeError(f"{path.name} EXAMPLES holds a {type(item).__name__}, not a message.")
        examples.append(item)
    return tuple(examples)


EXAMPLES_BY_FAMILY: dict[str, tuple[WaggleMessage, ...]] = {
    family: _load_examples(family) for family in FAMILIES
}
ALL_EXAMPLES: tuple[WaggleMessage, ...] = tuple(
    example for family in FAMILIES for example in EXAMPLES_BY_FAMILY[family]
)


# ──────────────────────────────────────────────────────────────────────────────
# The kind list
# ──────────────────────────────────────────────────────────────────────────────


def test_all_kinds_lists_the_sixty_six_catalogue_kinds_in_table_order() -> None:
    assert len(CATALOGUE_KINDS) == 66
    assert all_kinds() == CATALOGUE_KINDS


def test_every_registered_model_is_a_distinct_waggle_message_subclass() -> None:
    models = [spec.model for spec in MESSAGE_SPECS]

    assert len(set(models)) == len(models)
    for model in models:
        assert issubclass(model, WaggleMessage)
        assert model is not WaggleMessage


# ──────────────────────────────────────────────────────────────────────────────
# One example of every kind, from the ten family test modules
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("family", FAMILIES)
def test_family_examples_are_registered_under_that_family(family: str) -> None:
    examples = EXAMPLES_BY_FAMILY[family]

    assert examples
    for example in examples:
        assert kind_for(type(example)).startswith(f"{family}."), type(example).__name__


def test_examples_cover_every_registered_model_exactly_once() -> None:
    assert Counter(type(example) for example in ALL_EXAMPLES) == Counter(
        spec.model for spec in MESSAGE_SPECS
    )


@pytest.mark.parametrize("example", ALL_EXAMPLES, ids=lambda example: type(example).__name__)
def test_example_round_trips_through_wrap_and_the_codec(
    example: WaggleMessage,
    make_envelope: Callable[..., Envelope],
    plain_codec: Codec,
    fake_clock: FakeClock,
) -> None:
    kind = kind_for(type(example))
    assert spec_for(kind).model is type(example)
    # Spec section 3: a reply must carry the id of the request it answers and a request must
    # not; an event may, and the round trip is the same either way, so events go without.
    correlation_id = (
        new_message_id(fake_clock) if spec_for(kind).shape is MessageShape.REPLY else None
    )
    envelope = make_envelope(example, correlation_id=correlation_id)

    decoded = plain_codec.decode(plain_codec.encode(envelope))

    assert decoded == envelope
    assert decoded.kind == kind
    assert type(decoded.payload) is type(example)


# ──────────────────────────────────────────────────────────────────────────────
# The package faces: waggle.messages and each family package re-export every registered class
# ──────────────────────────────────────────────────────────────────────────────


def test_messages_package_re_exports_every_registered_class() -> None:
    exported = set(messages.__all__)
    missing = [spec.model.__name__ for spec in MESSAGE_SPECS if spec.model.__name__ not in exported]

    assert not missing, f"Registered classes missing from waggle.messages.__all__: {missing}"
    for spec in MESSAGE_SPECS:
        assert getattr(messages, spec.model.__name__) is spec.model


@pytest.mark.parametrize("family", FAMILIES)
def test_family_package_re_exports_every_registered_class_of_that_family(family: str) -> None:
    package = importlib.import_module(f"waggle.messages.{family}")
    exported = set(package.__all__)
    registered = [spec.model for spec in MESSAGE_SPECS if spec.kind.startswith(f"{family}.")]

    assert registered
    missing = [model.__name__ for model in registered if model.__name__ not in exported]
    assert not missing, f"Classes missing from waggle.messages.{family}.__all__: {missing}"
    for model in registered:
        assert getattr(package, model.__name__) is model


def test_messages_package_re_exports_the_registry_api_and_base_classes() -> None:
    assert {
        "MESSAGE_SPECS",
        "MessageSpec",
        "all_kinds",
        "kind_for",
        "model_for",
        "spec_for",
    } <= set(messages.__all__)
    assert messages.WaggleMessage is WaggleMessage
    assert messages.MessageShape is MessageShape
    assert messages.MESSAGE_SPECS is MESSAGE_SPECS


def test_messages_package_all_is_unique_and_resolves() -> None:
    assert len(messages.__all__) == len(set(messages.__all__))
    for name in messages.__all__:
        assert hasattr(messages, name), name

"""Define ModelSlot and Effort: the named places a model call resolves to, and how hard it tries.

Code never asks for a model by name; it asks for a slot (codingrules section 8.6): ``QUEEN`` for
the Queen (the central orchestrator), ``WARDEN`` for a Warden (a per-Cell supervisor), ``WORKER``
for a Worker (a subagent that does the hands-on work) and so on. The Hive Manifest's
``[llm.slots]`` table maps each slot to a provider and a model id, so moving a slot from a hosted
API to a local server is a configuration change, never a code change. ``Effort`` is how hard the
model behind a slot is asked to think on one call; it is a per-binding value that Forage grants
cap (``AllowedBinding.max_effort``) and that the Queen's autopilot sets per event class, never a
global. Both enums live here, in ``forage`` (the Hive's capacity, modelled as data), and not in
``llm`` (the package that talks to providers), because codingrules section 4 fixes the direction
within Layer 1 as ``llm`` importing ``forage`` and never the reverse: a grant, a routing input or
an autopilot rule has to name a slot and an effort without pulling in provider machinery.
``Effort`` mirrors ``waggle.messages.forage.Effort`` member for member because the same value
travels on grant messages over the wire; a sync test keeps the two from drifting apart.

Fits into the Hive:
    Layer 1 (forage; foundational services, capacity as data). Read by hivemind.llm.slots when
    it resolves a slot to a bound model, by hivemind.forage.allocate when it lists a grant's
    allowed bindings, by hivemind.manifest when it validates ``[llm.slots]`` keys, and by every
    autopilot rule that names the slot an awake episode should run on. Calls into
    waggle.messages only, for the wire conversions.

Key invariants:
    - A ModelSlot's value is its own UPPER_SNAKE name, which is exactly the shape the wire's
      ``SLOT_PATTERN`` (``^[A-Z][A-Z_]*$``) accepts, so ``to_wire`` is the value and
      ``from_wire`` is a lookup by value.
    - ``manifest_key`` is the lowercase name, the form ``[llm.slots]`` keys use;
      ``from_manifest_key`` inverts it and raises ``KeyError`` for a key that names no slot (a
      named binding such as ``local_worker`` is not a slot).
    - Effort's member names and values are identical to waggle.messages.forage.Effort's
      (tests/unit/forage/test_slots.py checks it member for member).

See Also:
    - .claude/codingrules.md section 8.6 for "model slots, not model names".
    - .claude/codingrules.md section 4 for why forage never imports llm.
    - waggle.messages.forage.values for the wire Effort this module mirrors and the slot label.
    - hivemind.forage.tempo for the other Layer 1 value llm reads from here.
"""

from __future__ import annotations

from enum import Enum

from waggle.messages.forage import Effort as WireEffort

__all__ = ["Effort", "ModelSlot"]


class ModelSlot(Enum):
    """A named place a model call resolves to; the manifest binds each one to a provider and model.

    Every member's value is its own name so the value doubles as the wire label
    (waggle's ``SLOT_PATTERN``), and the lowercase name is the ``[llm.slots]`` key.
    """

    QUEEN = "QUEEN"  # The Queen's awake episodes: the strongest grade the Hive can reach.
    ATTENDANT = "ATTENDANT"  # Inbox tie-breaks and unknown kinds: cheap, low grade, fast.
    WARDEN = "WARDEN"  # A Warden's awake episodes: one decision per stuck sub-bee event.
    WORKER = "WORKER"  # The default slot every Worker role runs its tool loop on.
    RIPENER = "RIPENER"  # House Bees ripening Nectar into Honey: batch work, low grade.
    SCAFFOLDER = "SCAFFOLDER"  # Royal Jelly Lab tool authoring: code generation.
    EMBEDDER = "EMBEDDER"  # Non-chat: text in, vector out (llm.embedding, phase 7).
    JUDGE = "JUDGE"  # Capping's independent reviewer; may be pinned to another provider.
    TRANSCRIBER = "TRANSCRIBER"  # Non-chat: audio in, text out (llm.transcription, phase 10).

    @property
    def manifest_key(self) -> str:
        """Return the ``[llm.slots]`` key for this slot: the lowercase member name.

        Returns:
            ``"queen"`` for ``ModelSlot.QUEEN``, and so on.
        """
        return self.name.lower()

    @classmethod
    def from_manifest_key(cls, key: str) -> ModelSlot:
        """Return the slot a ``[llm.slots]`` key names.

        Args:
            key: A lowercase slot name such as ``"worker"``. Case matters: manifest keys are
                lowercase by convention and a mixed-case key is a typo worth surfacing.

        Returns:
            The matching ModelSlot.

        Raises:
            KeyError: If ``key`` names no slot. A named binding (``local_worker``) is a legal
                ``[llm.slots]`` key but not a slot; callers that resolve fallbacks handle it.
        """
        # Members are looked up by name, so only an exact uppercase match of a lowercase key
        # resolves; anything else falls through to the KeyError the caller expects.
        for member in cls:
            if member.manifest_key == key:
                return member
        raise KeyError(f"'{key}' is not a model slot; slots are {[m.manifest_key for m in cls]}")

    @classmethod
    def from_wire(cls, label: str) -> ModelSlot:
        """Return the slot a wire label (``TaskAssign.slot``, ``AllowedBinding.slot``) names.

        Args:
            label: The UPPER_SNAKE label read off an Envelope.

        Returns:
            The matching ModelSlot.

        Raises:
            ValueError: If the label names no slot; the Enum constructor's own error.
        """
        return cls(label)

    def to_wire(self) -> str:
        """Return the wire label for this slot: its value, which already fits ``SLOT_PATTERN``.

        Returns:
            The UPPER_SNAKE label.
        """
        return self.value


class Effort(Enum):
    """How hard the model behind a binding is asked to think on one call.

    A per-binding value: a grant caps it (``AllowedBinding.max_effort``), routing picks it from
    tempo (codingrules section 8.14), and the Queen's autopilot sets it per event class for her
    own awake episodes. Mirrors waggle.messages.forage.Effort, the wire form on grant messages.
    """

    LOW = "LOW"  # Routine decisions and urgent low-bar work: fastest, cheapest.
    MEDIUM = "MEDIUM"  # The default for ordinary Worker calls.
    HIGH = "HIGH"  # Planning, contested Forage, critical accuracy bars.

    @classmethod
    def from_wire(cls, wire: WireEffort) -> Effort:
        """Build an Effort from the wire form waggle carries on grant messages.

        Args:
            wire: The waggle.messages.forage.Effort value read off an Envelope.

        Returns:
            The equivalent hivemind Effort.
        """
        # Same member names and values on both sides, so a lookup by value is the conversion.
        return cls(wire.value)

    def to_wire(self) -> WireEffort:
        """Build the wire form waggle carries on grant messages.

        Returns:
            The equivalent waggle.messages.forage.Effort.
        """
        return WireEffort(self.value)

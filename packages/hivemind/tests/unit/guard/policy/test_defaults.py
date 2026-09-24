"""Tests for hivemind.guard.policy.defaults: the shipped policy, policy files and [guard] overlays.

Fits into the Hive:
    Mirrors src/hivemind/guard/policy/defaults.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.policy.defaults for the module under test.
    - hivemind.guard.defaults for the shipped policy.toml the first half of this module pins.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib.resources import files
from pathlib import Path

import pytest

from hivemind.guard.capabilities import Capability, CapabilityFamily, ScopeKind
from hivemind.guard.errors import GuardPolicyError
from hivemind.guard.policy.defaults import load_guard_policy
from hivemind.guard.policy.models import EscalationAction
from hivemind.guard.policy.points import EnforcementPoint
from hivemind.guard.policy.roles import proposed_set, role_set
from hivemind.guard.policy.table import POLICY_ROLES
from hivemind.manifest import GuardRoleSection, GuardSection, load_manifest

# The repository root, six parents up from this test file
# (packages/hivemind/tests/unit/guard/policy/test_defaults.py).
_REPO_ROOT = Path(__file__).resolve().parents[6]
_SCRATCH = Path("/hive/scratch")
# The families only a person holds, and the device families: no bee's default may name either.
_HUMAN_ONLY = {CapabilityFamily.SUPERSEDE, CapabilityFamily.ENTRANCE_STEWARD}
_DEVICE_FAMILIES = {
    CapabilityFamily.ENTRANCE_SUBMIT,
    CapabilityFamily.ENTRANCE_ANSWER,
    CapabilityFamily.ENTRANCE_PUSH,
    CapabilityFamily.ENTRANCE_STEWARD,
    CapabilityFamily.OBSERVE,
    CapabilityFamily.OBSERVE_THOUGHTS,
    CapabilityFamily.OBSERVE_HONEY,
}
_BEE_ROLES = POLICY_ROLES - {"operator", "device", "swarm_device"}


def _widest(family: CapabilityFamily) -> Capability:
    """Return the widest capability of `family`: what holding it "at its widest" must cover."""
    match family.scope_kind:
        case ScopeKind.FLAG:
            return Capability(family=family)
        case ScopeKind.GLOB:
            return Capability(family=family, scope="**")
        case ScopeKind.ORDERED:
            return Capability(family=family, scope=family.scope_values[-1])  # The top rung.
        case _:
            return Capability(family=family, scope="*")


def _families(role: str) -> set[CapabilityFamily]:
    """Return every family the shipped policy's `role` holds, scratch root filled."""
    return {capability.family for capability in role_set(load_guard_policy(), role, _SCRATCH)}


def _shipped_text() -> str:
    """Return the shipped policy.toml, for tests that write a variant of it."""
    return (files("hivemind.guard.defaults") / "policy.toml").read_text(encoding="utf-8")


def _write(tmp_path: Path, text: str) -> Path:
    """Write a policy file into `tmp_path` and return its path."""
    path = tmp_path / "policy.toml"
    path.write_text(text, encoding="utf-8")
    return path


# ──────────────────────────────────────────────────────────────────────────────
# The shipped policy: two roots, bees below them, devices beside them
# ──────────────────────────────────────────────────────────────────────────────


def test_shipped_policy_defines_every_policy_role_and_nothing_hive_wide() -> None:
    policy = load_guard_policy()

    assert set(policy.roles) == POLICY_ROLES
    assert policy.deny.as_strings() == ()
    assert dict(policy.escalation) == {}


def test_operator_holds_every_family_at_its_widest() -> None:
    operator = role_set(load_guard_policy(), "operator")

    for family in CapabilityFamily:
        assert operator.allows(_widest(family)), family.value


def test_queen_holds_every_work_family_but_nothing_human_only_or_device_only() -> None:
    queen = role_set(load_guard_policy(), "queen")
    withheld = _HUMAN_ONLY | _DEVICE_FAMILIES

    for family in CapabilityFamily:
        assert queen.allows(_widest(family)) is (family not in withheld), family.value


@pytest.mark.parametrize("role", sorted(_BEE_ROLES))
def test_no_bee_role_holds_a_human_only_or_device_family(role: str) -> None:
    assert not _families(role) & (_HUMAN_ONLY | _DEVICE_FAMILIES)


def test_warden_default_is_the_adrs_list_plus_its_scratch_writes() -> None:
    warden = role_set(load_guard_policy(), "warden", _SCRATCH)

    assert set(warden.as_strings()) == {
        # Roadmap step 10.3 (lease_creation): the lease each kind of Warden's Cell needs.
        "cell:hive_stand",
        "cell:virtual",
        "cell:real:*",
        "tool:*",
        "tool:scope:*",
        "tool:request",
        "fs:read:**",
        "fs:write:/hive/scratch/**",  # Kept at SCRATCH, where fs:write:** is dropped.
        "fs:write:**",
        "exec:*",
        "net:*",
        "device:*",
        "cell:outside_scratch:**",
        "exoskeleton",
        "llm:*",
        "spend:*",
        "question:human",
        "forage:request",
        "wax:propose",
        "honey:read:*",
        "honey:write",
        "honey:clearance:c2",
        "tactic:*",
    }


def test_drone_default_keeps_phase_three_access_and_adds_its_slot() -> None:
    drone = role_set(load_guard_policy(), "drone", _SCRATCH)

    assert set(drone.as_strings()) == {
        "fs:write:/hive/scratch/**",
        "fs:read:**",
        "exec:*",
        "tool:*",
        "llm:worker",
        "spend:*",
        "question:human",
        "wax:propose",
        "honey:read:*",
        "honey:clearance:c2",
    }


def test_guard_bee_watches_on_the_judge_slot_and_never_touches_a_cell() -> None:
    families = _families("guard_bee")
    guard_bee = role_set(load_guard_policy(), "guard_bee", _SCRATCH)

    assert not families & {
        CapabilityFamily.FS_WRITE,
        CapabilityFamily.EXEC,
        CapabilityFamily.WAX_PROPOSE,
    }
    assert "llm:judge" in guard_bee.as_strings()
    assert "llm:worker" not in guard_bee.as_strings()


def test_house_bee_ripens_on_its_own_slots_and_deposits_honey() -> None:
    house_bee = set(role_set(load_guard_policy(), "house_bee", _SCRATCH).as_strings())

    assert {"llm:ripener", "llm:embedder", "honey:write"} <= house_bee
    assert "llm:worker" not in house_bee


def test_device_ceiling_carries_work_families_and_no_spend() -> None:
    device = role_set(load_guard_policy(), "device")

    for spec in ["entrance:steward", "cell:comb_shield:night_veil", "tool:x", "net:a.example"]:
        assert device.allows(Capability.parse(spec)), spec
    assert CapabilityFamily.SPEND not in {capability.family for capability in device}


def test_device_proposed_stays_inside_allow_without_steward_or_night_veil() -> None:
    policy = load_guard_policy()

    proposed = proposed_set(policy)

    assert proposed.issubset(role_set(policy, "device"))
    assert not proposed.allows(Capability.parse("entrance:steward"))
    assert not proposed.allows(Capability.parse("cell:comb_shield:night_veil"))
    assert proposed.allows(Capability.parse("cell:comb_shield:propolis"))


def test_swarm_device_holds_nothing_until_phase_eleven() -> None:
    assert role_set(load_guard_policy(), "swarm_device").as_strings() == ()


# ──────────────────────────────────────────────────────────────────────────────
# The manifest's [guard] table on top
# ──────────────────────────────────────────────────────────────────────────────


def test_a_role_override_replaces_that_roles_allow_list() -> None:
    section = GuardSection(roles={"drone": GuardRoleSection(allow=("fs:read:**", "llm:worker"))})

    policy = load_guard_policy(None, section)

    assert role_set(policy, "drone", _SCRATCH).as_strings() == ("fs:read:**", "llm:worker")
    assert "tool:*" in role_set(policy, "scout", _SCRATCH).as_strings()  # Others untouched.


def test_a_device_override_keeps_the_policys_proposed_list_unless_it_gives_one() -> None:
    shipped = proposed_set(load_guard_policy())
    allow = role_set(load_guard_policy(), "device").as_strings()

    kept = load_guard_policy(None, GuardSection(roles={"device": GuardRoleSection(allow=allow)}))
    given = load_guard_policy(
        None,
        GuardSection(roles={"device": GuardRoleSection(allow=allow, proposed=("observe",))}),
    )

    assert proposed_set(kept) == shipped
    assert proposed_set(given).as_strings() == ("observe",)


def test_the_manifests_deny_list_adds_to_the_policys() -> None:
    policy = load_guard_policy(None, GuardSection(deny=("exoskeleton:real_display", "net:*.x.io")))

    assert policy.deny.as_strings() == ("exoskeleton:real_display", "net:*.x.io")


def test_the_manifests_escalation_maps_points_to_actions() -> None:
    section = GuardSection(escalation={"tool_invocation": "alarm", "placement": "ask_human"})

    policy = load_guard_policy(None, section)

    assert policy.escalation_for(EnforcementPoint.TOOL_INVOCATION) is EscalationAction.ALARM
    assert policy.escalation_for(EnforcementPoint.PLACEMENT) is EscalationAction.ASK_HUMAN
    assert policy.escalation_for(EnforcementPoint.GRANT_ISSUE) is EscalationAction.REFUSE


@pytest.mark.parametrize(
    ("section", "match"),
    [
        (GuardSection(roles={"beekeeper": GuardRoleSection(allow=())}), "unknown roles"),
        (GuardSection(escalation={"teleport": "alarm"}), "unknown enforcement point 'teleport'"),
        (GuardSection(escalation={"placement": "panic"}), "unknown escalation action 'panic'"),
        (GuardSection(deny=("observe:foo",)), "'observe:foo' in the deny list"),
        (GuardSection(deny=("fs:write:{scratch}/**",)), "only a role's allow list may"),
        (
            GuardSection(roles={"drone": GuardRoleSection(allow=("llm:nonsense",))}),
            "'llm:nonsense' in role 'drone'.*not a capability: 'nonsense' is not one of",
        ),
        (
            GuardSection(roles={"drone": GuardRoleSection(allow=("fs:write:{home}/**",))}),
            "unknown placeholder",
        ),
        (
            GuardSection(roles={"drone": GuardRoleSection(allow=(), proposed=("observe",))}),
            "role 'drone'.* has a proposed list",
        ),
        (
            GuardSection(roles={"device": GuardRoleSection(allow=("observe",))}),
            "proposed list of role 'device'.* exceeds its allow list",
        ),
    ],
)
def test_an_invalid_overlay_is_refused_naming_what_is_wrong(
    section: GuardSection, match: str
) -> None:
    with pytest.raises(GuardPolicyError, match=match):
        load_guard_policy(None, section)


# ──────────────────────────────────────────────────────────────────────────────
# An operator's policy file
# ──────────────────────────────────────────────────────────────────────────────


def test_a_policy_file_replaces_the_shipped_policy(tmp_path: Path) -> None:
    text = _shipped_text().replace("deny = []", 'deny = ["wifi:scan"]', 1)

    policy = load_guard_policy(_write(tmp_path, text))

    assert policy.deny.as_strings() == ("wifi:scan",)


def test_a_policy_file_and_an_overlay_combine_their_deny_lists(tmp_path: Path) -> None:
    path = _write(tmp_path, _shipped_text().replace("deny = []", 'deny = ["wifi:scan"]', 1))

    policy = load_guard_policy(path, GuardSection(deny=("geo:*",)))

    assert policy.deny.as_strings() == ("geo:*", "wifi:scan")


@pytest.mark.parametrize(
    ("edit", "match"),
    [
        (lambda text: text.replace("[roles.swarm_device]\nallow = []\n", ""), "missing \\['swarm"),
        (lambda text: text + "\n[roles.beekeeper]\nallow = []\n", "unknown \\['beekeeper'\\]"),
        (lambda text: 'policy_file = "other.toml"\n' + text, "names a policy_file of its own"),
        (lambda text: text + "\n[roles", "Could not read"),
        (
            lambda text: text.replace("[roles.swarm_device]\nallow = []", "[roles.swarm_device]"),
            "is invalid",
        ),
        (lambda text: text.replace("proposed = [", "not_proposed = ["), "is invalid"),
    ],
)
def test_a_bad_policy_file_is_refused(
    tmp_path: Path, edit: Callable[[str], str], match: str
) -> None:
    path = _write(tmp_path, edit(_shipped_text()))

    with pytest.raises(GuardPolicyError, match=match):
        load_guard_policy(path)


def test_a_device_role_without_a_proposed_list_is_refused(tmp_path: Path) -> None:
    start = _shipped_text().index("proposed = [")
    end = _shipped_text().index("]", start) + 1
    text = _shipped_text()[:start] + _shipped_text()[end:]

    with pytest.raises(GuardPolicyError, match="has no proposed list"):
        load_guard_policy(_write(tmp_path, text))


def test_a_missing_policy_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(GuardPolicyError, match="Could not read"):
        load_guard_policy(tmp_path / "nowhere.toml")


# ──────────────────────────────────────────────────────────────────────────────
# Every example manifest's [guard] builds a policy
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("filename", ["minimal.toml", "local.toml", "full.toml"])
def test_every_example_manifests_guard_table_builds_a_policy(filename: str) -> None:
    manifest = load_manifest(_REPO_ROOT / "docs" / "manifests" / filename)
    section = manifest.guard
    path = manifest.resolve_path(Path(section.policy_file)) if section.policy_file else None

    policy = load_guard_policy(path, section)

    assert set(policy.roles) == POLICY_ROLES

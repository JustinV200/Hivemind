"""Load the Guard policy: the shipped TOML or an operator's file, with `[guard]` applied on top.

The Guard policy is data (ADR-0039): each policy role's default capability set, a hive-wide deny
list and what a denial at each enforcement point escalates to. Every Hive starts from the file
shipped in `hivemind.guard.defaults`; an operator may replace it (`[guard] policy_file`) and may
overlay it from the manifest (`[guard.roles.<role>]` replaces a role's list, `deny` adds denials,
`[guard.escalation]` sets a point's action). `load_guard_policy` reads the file, applies the
overlay and checks everything before anything runs: the roles are exactly the policy roles, only
the `device` role has a `proposed` list and it stays inside its `allow`, every entry parses as a
capability (the manifest could only check its shape), `{scratch}` is the one placeholder and only
an `allow` list may name it, and every point and action name exists. A policy file has the shape
of a manifest's `[guard]` table less `policy_file`, so both are validated by the same models.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by the composition roots
    (`hivemind.cli.compose.deps` with the manifest's `[guard]`, `hivemind.cli.in_cell.deps` with
    the shipped policy alone) and by `hivemind.wardens.deps.WardenDeps`'s default. Calls into
    `hivemind.manifest.schema.guard` (the table shapes), `hivemind.guard.access` (the scratch
    placeholder), `hivemind.guard.capabilities` and this package's `models`, `points` and `table`.

Key invariants:
    - It either returns a fully checked `GuardPolicy` or raises `GuardPolicyError` naming the
      source and the offending entry; it never returns a partly built policy.
    - A manifest overlay can replace a role's list but never adds a role, and its `deny` only
      ever adds to the file's, so a manifest cannot remove a denial the policy file makes.

See Also:
    - docs/adr/0039-capability-model-attenuation-and-enforcement-points.md for the policy model.
    - hivemind.guard.defaults for the shipped policy.toml.
    - hivemind.manifest.schema.guard for GuardSection, the shape both sources share.
    - hivemind.supervision.policy.load_policy for the same shipped-or-file pattern.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable
from enum import Enum
from importlib.resources import files
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from pydantic import ValidationError

from hivemind.guard.access import SCRATCH_PLACEHOLDER, fill_scratch
from hivemind.guard.capabilities import Capability, CapabilitySet
from hivemind.guard.errors import GuardPolicyError, InvalidCapabilityError
from hivemind.guard.policy.models import EscalationAction
from hivemind.guard.policy.points import EnforcementPoint
from hivemind.guard.policy.table import DEVICE_ROLE, POLICY_ROLES, GuardPolicy, RoleDefaults
from hivemind.manifest.schema.guard import GuardRoleSection, GuardSection

DEFAULT_POLICY_FILENAME = "policy.toml"  # The shipped policy, inside _DEFAULTS_PACKAGE.
# The data-only package the shipped policy lives in, addressed by dotted name so it resolves the
# same from a checkout and from an installed wheel (see that package's own docstring).
_DEFAULTS_PACKAGE = "hivemind.guard.defaults"
_SAMPLE_SCRATCH_ROOT = PurePosixPath("/scratch")  # Fills {scratch} when checking an entry parses.

__all__ = ["DEFAULT_POLICY_FILENAME", "load_guard_policy"]


def load_guard_policy(path: Path | None = None, section: GuardSection | None = None) -> GuardPolicy:
    """Build the Guard policy from a policy file, with a manifest's `[guard]` table on top.

    Args:
        path: The operator's `[guard] policy_file`, already resolved against the manifest's own
            directory; None reads the policy shipped in `hivemind.guard.defaults`.
        section: The manifest's `[guard]` table: its role tables replace those roles' lists,
            its `deny` adds to the file's, its `escalation` entries win. None applies nothing.
            Its own `policy_file` is not read here; the caller resolves it into `path`.

    Returns:
        The checked, frozen GuardPolicy.

    Raises:
        GuardPolicyError: The file cannot be read, is not TOML, is not a policy's shape or names
            a `policy_file` of its own; a role is missing or unknown; `proposed` appears on a
            role other than `device`, is missing there, or is wider than its `allow`; an entry
            is not a capability or names an unknown placeholder; or a point or action name is
            unknown. The message names the source and the offending entry.
    """
    document, source = _read_document(path)
    # The file must define exactly the policy roles before an overlay may name any of them.
    _check_role_names(document.roles, source)
    if section is None:
        return _build(document, f"the Guard policy at {source}")
    return _build(_overlay(document, section), f"the Guard policy at {source} with [guard] applied")


def _read_document(path: Path | None) -> tuple[GuardSection, str]:
    """Read and shape-check a policy file (or the shipped one); return it with its source label."""
    source = str(path) if path is not None else f"the shipped {DEFAULT_POLICY_FILENAME}"
    # The shipped file is read through importlib.resources, so a wheel and a checkout agree; an
    # unreadable file or bad TOML becomes one GuardPolicyError naming where it came from.
    try:
        text = (
            path.read_text(encoding="utf-8")
            if path is not None
            else (files(_DEFAULTS_PACKAGE) / DEFAULT_POLICY_FILENAME).read_text(encoding="utf-8")
        )
        raw = tomllib.loads(text)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise GuardPolicyError(f"Could not read the Guard policy from {source}: {exc}") from exc
    try:
        document = GuardSection.model_validate(raw)
    except ValidationError as exc:
        raise GuardPolicyError(f"The Guard policy at {source} is invalid: {exc}") from exc
    # Only a manifest names a replacement file; a file naming another would chain without end.
    if document.policy_file:
        raise GuardPolicyError(
            f"The Guard policy at {source} names a policy_file of its own; only a manifest's "
            "[guard] table may name one."
        )
    return document, source


def _check_role_names(roles: Iterable[str], source: str) -> None:
    """Require a policy file to define every policy role and nothing else."""
    names = set(roles)
    missing, unknown = sorted(POLICY_ROLES - names), sorted(names - POLICY_ROLES)
    if missing or unknown:
        raise GuardPolicyError(
            f"The Guard policy at {source} must define exactly the roles "
            f"{sorted(POLICY_ROLES)}; missing {missing}, unknown {unknown}."
        )


def _overlay(document: GuardSection, section: GuardSection) -> GuardSection:
    """Apply a manifest's `[guard]` table on top of a shape-checked policy document."""
    # A manifest may change a role, never invent one: the policy file fixed the role set.
    unknown = sorted(set(section.roles) - set(document.roles))
    if unknown:
        raise GuardPolicyError(
            f"[guard.roles] names unknown roles {unknown}; the policy roles are "
            f"{sorted(document.roles)}."
        )
    roles = dict(document.roles)
    # An override replaces a role's allow list; its proposed list only when it gives one.
    for name, override in section.roles.items():
        proposed = override.proposed if override.proposed is not None else roles[name].proposed
        roles[name] = GuardRoleSection(allow=override.allow, proposed=proposed)
    # Denials only ever add up (a manifest cannot lift the file's); escalation entries win.
    return GuardSection(
        deny=(*document.deny, *section.deny),
        roles=roles,
        escalation={**document.escalation, **section.escalation},
    )


def _build(document: GuardSection, where: str) -> GuardPolicy:
    """Parse every entry and name of a (possibly overlaid) policy document into a GuardPolicy."""
    # Everything is parsed here, once, so a bad entry stops the Hive at start rather than at the
    # first enforcement point that happens to read it.
    roles = {name: _role_defaults(name, table, where) for name, table in document.roles.items()}
    escalation = {
        _member(EnforcementPoint, point, "enforcement point", where): _member(
            EscalationAction, action, "escalation action", where
        )
        for point, action in document.escalation.items()
    }
    return GuardPolicy(
        roles=MappingProxyType(roles),
        deny=_parse_fixed(document.deny, f"the deny list of {where}"),
        escalation=MappingProxyType(escalation),
    )


def _role_defaults(name: str, table: GuardRoleSection, where: str) -> RoleDefaults:
    """Parse one role's lists, keeping `{scratch}` entries as checked templates."""
    role_where = f"role {name!r} of {where}"
    fixed: list[Capability] = []
    templates: list[str] = []
    for entry in table.allow:
        # A {scratch} entry is checked by filling in a sample root, then kept as written.
        if SCRATCH_PLACEHOLDER in entry:
            _parse_entry(fill_scratch(entry, _SAMPLE_SCRATCH_ROOT), entry, role_where)
            templates.append(entry)
        else:
            fixed.append(_parse_entry(entry, entry, role_where))
    allow = CapabilitySet(capabilities=frozenset(fixed))
    return RoleDefaults(
        allow=allow,
        scratch_templates=tuple(templates),
        proposed=_proposed(name, table.proposed, allow, role_where),
    )


def _proposed(
    name: str, entries: tuple[str, ...] | None, allow: CapabilitySet, where: str
) -> CapabilitySet | None:
    """Parse the device role's `proposed` list, refusing one anywhere else or wider than allow."""
    # Only a device is approved, so only the device role says what an approval grants.
    if name != DEVICE_ROLE:
        if entries is not None:
            raise GuardPolicyError(f"{where} has a proposed list; only {DEVICE_ROLE!r} has one.")
        return None
    if entries is None:
        raise GuardPolicyError(f"{where} has no proposed list; an approval needs a default.")
    proposed = _parse_fixed(entries, f"the proposed list of {where}")
    wider = sorted(str(capability) for capability in proposed if not allow.allows(capability))
    if wider:
        raise GuardPolicyError(f"the proposed list of {where} exceeds its allow list: {wider}.")
    return proposed


def _parse_fixed(entries: Iterable[str], where: str) -> CapabilitySet:
    """Parse a list that names no lease (a deny or proposed list): no placeholder allowed."""
    capabilities: list[Capability] = []
    for entry in entries:
        if SCRATCH_PLACEHOLDER in entry:
            raise GuardPolicyError(
                f"{entry!r} in {where} names {SCRATCH_PLACEHOLDER}; only a role's allow list may."
            )
        capabilities.append(_parse_entry(entry, entry, where))
    return CapabilitySet(capabilities=frozenset(capabilities))


def _parse_entry(text: str, entry: str, where: str) -> Capability:
    """Parse one entry (`text`, with any placeholder filled), naming `entry` if it fails."""
    # A brace left after filling {scratch} is a placeholder the policy does not have ({home}).
    if "{" in text or "}" in text:
        raise GuardPolicyError(
            f"{entry!r} in {where} names an unknown placeholder; {SCRATCH_PLACEHOLDER} is the "
            "only one."
        )
    try:
        return Capability.parse(text)
    except InvalidCapabilityError as exc:
        raise GuardPolicyError(f"{entry!r} in {where} is not a capability: {exc.reason}.") from exc


def _member[EnumT: Enum](enum_type: type[EnumT], name: str, what: str, where: str) -> EnumT:
    """Return the member of `enum_type` whose value is `name`, or raise naming the choices."""
    try:
        return enum_type(name)
    except ValueError as exc:
        choices = ", ".join(str(member.value) for member in enum_type)
        raise GuardPolicyError(
            f"{where} names an unknown {what} {name!r}; expected one of {choices}."
        ) from exc

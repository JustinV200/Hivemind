"""Carry a command's shared options on its context, past the five-parameter limit.

Codingrules 5.1 caps a function at five parameters, and typer turns each parameter of a command
function into one option. The commands that talk to the Landing Board (the Hive Entrance's HTTP
contract) share several options on top of their own (the manifest, ``--password-stdin``, a remote
profile, a confirmation), so those are declared once here as ``CarriedOption``s:
``carried_command`` and ``carried_group`` build a typer command or group class that appends them,
each one's value left on the context's ``meta`` (which click shares between a group and its
subcommands), and the command reads it back with ``carried_flag``, ``carried_text`` or
``carried_path``. ``hivemind.cli.run.RunCommand`` is the same idea, written out by hand for
``hive run`` before this module existed.

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli.landing``. Used by ``hivemind.cli.entrance``,
    ``hivemind.cli.remote``, ``hivemind.cli.run`` and ``hivemind.cli.readback.inbox``. Calls into
    ``typer`` only.

Key invariants:
    - Every option's value is stored under its own ``key``; two options never share one.
    - An option that was not given reads back as its default.

See Also:
    - hivemind.cli.run.RunCommand for the hand-written precedent.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import typer
from typer.core import TyperCommand, TyperGroup, TyperOption

__all__ = [
    "PASSWORD_STDIN",
    "CarriedOption",
    "carried_command",
    "carried_flag",
    "carried_group",
    "carried_path",
    "carried_text",
]


@dataclass(frozen=True, slots=True)
class CarriedOption:
    """One option carried on the context instead of a function parameter.

    Attributes:
        key: Where its value is kept in ``ctx.meta``: ``hivemind.cli.<name>``, unique.
        decls: Its flag spellings, e.g. ``("--password-stdin",)``.
        help: Its help text.
        is_flag: A boolean switch (False unless given) rather than an option taking a value.
        default: A value option's default when not given; None means "not given".
    """

    key: str
    decls: tuple[str, ...]
    help: str
    is_flag: bool = False
    default: str | None = None

    def as_click(self) -> TyperOption:
        """Build the click option that leaves this option's value on the context."""
        return TyperOption(
            param_decls=list(self.decls),
            is_flag=self.is_flag,
            default=False if self.is_flag else self.default,
            expose_value=False,
            callback=_keeper(self.key),
            help=self.help,
        )


PASSWORD_STDIN = CarriedOption(
    key="hivemind.cli.password_stdin",
    decls=("--password-stdin",),
    help="Read the operator password from stdin, one line per password, instead of prompting.",
    is_flag=True,
)


def carried_command(*options: CarriedOption) -> type[TyperCommand]:
    """Return a typer command class that also declares ``options``.

    Args:
        *options: The options to carry on the context.

    Returns:
        A ``TyperCommand`` subclass for ``app.command(..., cls=...)``.
    """

    class CarriedCommand(TyperCommand):
        """A command that declares the carried options after its own."""

        def __init__(self, name: str | None, **settings: object) -> None:
            """Build the command as typer does, then append the carried options."""
            # typer passes every other setting by keyword, exactly as TyperCommand takes them.
            super().__init__(name, **cast("dict[str, Any]", settings))
            self.params.extend(option.as_click() for option in options)

    return CarriedCommand


def carried_group(*options: CarriedOption) -> type[TyperGroup]:
    """Return a typer group class that also declares ``options`` (read by its subcommands too).

    Args:
        *options: The options to carry on the context.

    Returns:
        A ``TyperGroup`` subclass for ``typer.Typer(cls=...)``.
    """

    class CarriedGroup(TyperGroup):
        """A group that declares the carried options after its own."""

        def __init__(self, **settings: object) -> None:
            """Build the group as typer does, then append the carried options."""
            super().__init__(**cast("dict[str, Any]", settings))
            self.params.extend(option.as_click() for option in options)

    return CarriedGroup


def carried_flag(ctx: typer.Context, option: CarriedOption) -> bool:
    """Read a carried switch.

    Args:
        ctx: The command's context (or any context nested under the group that carries it).
        option: The switch.

    Returns:
        Whether it was given.
    """
    return bool(ctx.meta.get(option.key, False))


def carried_text(ctx: typer.Context, option: CarriedOption) -> str | None:
    """Read a carried value option.

    Args:
        ctx: The command's context.
        option: The option.

    Returns:
        Its value, its default when not given, or None.
    """
    value = ctx.meta.get(option.key, option.default)
    return None if value is None else str(value)


def carried_path(ctx: typer.Context, option: CarriedOption) -> Path | None:
    """Read a carried option that names a file.

    Args:
        ctx: The command's context.
        option: The option.

    Returns:
        Its value as a path, or None when neither given nor defaulted.
    """
    text = carried_text(ctx, option)
    return None if text is None else Path(text)


def _keeper(key: str) -> Callable[[typer.Context, object, object], None]:
    """Return the click callback that stores an option's value under ``key``."""

    def keep(ctx: typer.Context, _param: object, value: object) -> None:
        """Store the parsed value on the context, shared with nested commands."""
        ctx.meta[key] = value

    return keep

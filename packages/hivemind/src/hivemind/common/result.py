"""Define Ok/Err, a tiny Result type used only at the LLM boundary.

Codingrules section 9 reserves this pattern for exactly one place: calling an ``LLMProvider`` (the
vendor-neutral protocol every model adapter implements), where a call failing is a normal, expected
outcome, not a bug, and the caller needs to inspect *why* without an exception unwinding the stack.
Everywhere else in the Hive, a failure is raised as a ``HiveMindError`` subclass (section 10);
reaching for ``Result`` outside the LLM boundary is a rules violation, not a style choice.

Fits into the Hive:
    Layer 0 (primitives; imports nothing internal beyond waggle and this package's own errors).
    Used by hivemind.llm's provider calls; not expected to appear anywhere else.

Key invariants:
    - Ok and Err are frozen and slotted: once constructed, a Result value never changes.
    - unwrap() on an Err, and unwrap_err() on an Ok, both raise ResultUnwrapError rather than
      returning a sentinel a caller could mistake for real data.

See Also:
    - .claude/codingrules.md section 9 for the "Result only at the LLM boundary" rule.
    - hivemind.common.errors for the HiveMindError root ResultUnwrapError descends from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, NoReturn

from hivemind.common.errors import HiveMindError

__all__ = ["Err", "Ok", "Result", "ResultUnwrapError"]


class ResultUnwrapError(HiveMindError):
    """Raise when unwrap() is called on an Err, or unwrap_err() is called on an Ok."""

    code: ClassVar[str] = "hivemind.result_unwrap_error"


@dataclass(frozen=True, slots=True)
class Ok[T]:
    """A successful Result, holding the value a call produced."""

    value: T

    def is_ok(self) -> bool:
        """Report that this Result is a success.

        Returns: True, always.
        """
        return True

    def unwrap(self) -> T:
        """Return the wrapped value.

        Returns: The value this Ok holds.
        """
        return self.value

    def unwrap_err(self) -> NoReturn:
        """Raise, since an Ok holds no error to return.

        Raises:
            ResultUnwrapError: Always; this is an Ok, not an Err.
        """
        raise ResultUnwrapError(f"called unwrap_err() on Ok({self.value!r})")


@dataclass(frozen=True, slots=True)
class Err[E]:
    """A failed Result, holding the error a call produced."""

    error: E

    def is_ok(self) -> bool:
        """Report that this Result is a failure.

        Returns: False, always.
        """
        return False

    def unwrap(self) -> NoReturn:
        """Raise, since an Err holds no success value to return.

        Raises:
            ResultUnwrapError: Always; this is an Err, not an Ok.
        """
        raise ResultUnwrapError(f"called unwrap() on Err({self.error!r})")

    def unwrap_err(self) -> E:
        """Return the wrapped error.

        Returns: The error this Err holds.
        """
        return self.error


type Result[T, E] = Ok[T] | Err[E]

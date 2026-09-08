"""Define WaggleError, the root exception for the waggle package.

Waggle (the Hive's shared wire protocol and primitive package) cannot import
``hivemind.common.errors.HiveMindError`` because ``hivemind`` sits above ``waggle`` in the layer
table (codingrules section 4): waggle is usable by pollen, the lightweight device connector, which
may depend on nothing beyond waggle itself. Section 10 of the coding rules calls this out by name:
waggle gets its own error root instead of inheriting from the hivemind one. This file is not one
of the four modules roadmap step 0.5 names explicitly; it is added because ``waggle/ids.py``
needs a typed error for a malformed id and section 3's layout puts an ``errors.py`` in every
package that needs one.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen), inside the waggle package.
    Raised by waggle.ids when a candidate id string is not well-formed; caught by whichever
    caller asked to parse it.

Key invariants:
    - WaggleError never inherits from hivemind.common.errors.HiveMindError, directly or
      indirectly, so waggle keeps zero import dependency on hivemind.

See Also:
    - .claude/codingrules.md section 10 for the "waggle has its own root" rule.
    - hivemind.common.errors for the equivalent root on the hivemind side.
"""

from __future__ import annotations

__all__ = ["InvalidIdError", "WaggleError"]


class WaggleError(Exception):
    """Root of every error the waggle package raises.

    Subclasses name a specific failure (see InvalidIdError below); catching WaggleError catches
    anything waggle itself can raise.
    """


class InvalidIdError(WaggleError):
    """Raised when a candidate id string is not a well-formed id of the expected IdKind.

    Raised by waggle.ids.parse_id and waggle.ids.timestamp_of when the prefix, length, or
    character set of a candidate id does not match what a real id produced by waggle.ids.new_id
    would look like.
    """

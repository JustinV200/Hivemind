"""Mint and judge a Night Veil Cell's randomized hostname.

A Night Veil Cell is location-blind (codingrules 8.7), and one of its blind defaults is "randomized
hostname per boot": a hostname that is the same for every Cell of an image, or the Hive Stand's own,
or anything the Queen already knows (a Cell's id, say), is a name a Night Veil task's traffic could
carry out of the Cell and the Hive be recognised by. So the shape of the name is fixed in one place,
here, and both halves use it. `new_night_veil_hostname` mints one, `night-veil-` and eight random
hex digits (four bytes from the operating system's secure generator), which is the shape the
image's own boot service sets (`images/night-veil-ubuntu/night-veil-hostname.sh`) and the shape a
backend gives the Cell it provisions, so a Cell has a random name whichever of the two ran last.
`judge_hostname` is the attestation's pure check of the name the Cell reports: not empty, not a
name an image ships with, not the Hive Stand's own, and of exactly that shape. Because the shape
includes the randomness, a hostname built from a Cell's id, from the image's name or from a Docker
container's default fails it.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hive.night_veil.judges`. `judge_hostname` is called by
    `hive.night_veil.session_probe.SessionNightVeilProbe`; `new_night_veil_hostname` by the
    backends that provision a Night Veil Cell (`hive.backends.docker`). Calls into
    `hive.night_veil.results` and the standard library only.

Key invariants:
    - `judge_hostname` is pure and total; its `detail` is a fixed word, never the hostname the Cell
      reported, because the result is recorded on the trail.
    - A minted hostname always passes the shape check, and two are equal only by a one in 2**32
      chance.
    - Green needs a name of the random shape that is neither a shipped default nor the Hive Stand's.

See Also:
    - images/night-veil-ubuntu/README.md "Location-blind defaults" for the boot service's half.
    - hivemind.hive.backends.docker.backend for the backend half.
"""

from __future__ import annotations

import re
import secrets

from hivemind.hive.night_veil.results import CheckResult, verdict

HOSTNAME_PREFIX = "night-veil-"  # What every Night Veil Cell's hostname starts with, then hex.
_RANDOM_BYTES = 4  # Four bytes, eight hex digits: short enough for a hostname label, 2**32 names.
# The whole name, and nothing else: the prefix and the exact number of lowercase hex digits.
_SHAPE = re.compile(rf"{re.escape(HOSTNAME_PREFIX)}[0-9a-f]{{{_RANDOM_BYTES * 2}}}")
# Names a Cell has when nothing randomized it: the container default for no name, the stock Ubuntu
# cloud image's, and this image's own. They fail the shape too; naming them says why in the trail.
SHIPPED_HOSTNAMES = frozenset({"localhost", "ubuntu", "night-veil-ubuntu"})

__all__ = ["HOSTNAME_PREFIX", "SHIPPED_HOSTNAMES", "judge_hostname", "new_night_veil_hostname"]


def new_night_veil_hostname() -> str:
    """Mint a fresh random hostname of the shape a Night Veil Cell boots with.

    Returns:
        `night-veil-` and eight lowercase hex digits from the operating system's secure random
        source, so it carries nothing the Queen knows (not the Cell's id, not the time).
    """
    return f"{HOSTNAME_PREFIX}{secrets.token_hex(_RANDOM_BYTES)}"


def judge_hostname(reported: str, *, stand_hostname: str = "") -> CheckResult:
    """Judge that the hostname a Night Veil Cell reports is random and links back to nothing.

    Args:
        reported: What the Cell printed for its hostname (the kernel's own value), "" if the read
            failed.
        stand_hostname: The Hive Stand's own hostname, which the Cell must not carry; "" when it is
            not known, in which case only the shape is judged.

    Returns:
        PASS for a name of the random shape; FAIL for no name, a name an image ships with, the Hive
        Stand's name, or any other shape (a Cell id, a Docker container's default, a chosen name).
    """
    # Hostnames are case-insensitive; compare as the resolver does, so a different case of a shipped
    # or the Stand's name does not slip past by spelling.
    name = reported.strip().lower()
    if not name:
        return verdict(passed=False, detail="no_hostname")
    if name in SHIPPED_HOSTNAMES:
        return verdict(passed=False, detail="image_default_hostname")
    if stand_hostname and name == stand_hostname.strip().lower():
        return verdict(passed=False, detail="hive_stand_hostname")
    if _SHAPE.fullmatch(name) is None:
        return verdict(passed=False, detail="not_random_shape")
    return verdict(passed=True, detail="random_hostname")

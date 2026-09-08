"""Configure structlog for a composition root and hand every subsystem a bound logger.

Logs are for humans debugging a running Hive (codingrules section 12): JSON lines in production
for machine parsing, a readable console renderer in development. Configuration is a deliberate,
one-time act performed by a composition root, a CLI entry point, the Hive Entrance's startup, or a
test acting as one, never a side effect of importing this module (codingrules section 5.5:
"importing any module must be free of ... logger configuration"). Every subsystem then calls
``get_logger(__name__)`` to get its own bound logger without configuring anything itself.

Fits into the Hive:
    Layer 0 (primitives; imports nothing internal beyond waggle). Called by every composition
    root to configure logging once at startup, and by every subsystem to get its own logger.

Key invariants:
    - Importing this module has no side effect: structlog is only configured inside
      configure_logging, never at import time.
    - Log event names passed to a bound logger are lowercase and dotted, e.g. "cell.ready"
      (codingrules section 12), not prose sentences.

See Also:
    - .claude/codingrules.md section 12 for the logging rules this module implements.
"""

from __future__ import annotations

import logging
from typing import cast

import structlog

__all__ = ["configure_logging", "get_logger"]


def configure_logging(*, json_output: bool, level: str) -> None:
    """Configure structlog's global processor chain for this process.

    Must be called exactly once, by a composition root, before any subsystem logs anything that
    should reach a human or a log aggregator.

    Args:
        json_output: True for JSON lines (production, machine-parsed); False for a
            human-readable console renderer (local development).
        level: A standard-library logging level name, e.g. "DEBUG", "INFO", "WARNING".

    Returns:
        None.

    Raises:
        ValueError: `level` is not a recognised logging level name.
    """
    # logging.getLevelName maps a name to its numeric level, but also does the reverse (number to
    # name); checking the result is an int is how we tell a real name from an unrecognised one.
    numeric_level = logging.getLevelName(level.upper())
    if not isinstance(numeric_level, int):
        raise ValueError(f"unknown logging level {level!r}")

    # Shared up to the renderer: merge bound context (e.g. cell_id set earlier in a call chain),
    # stamp the level and an ISO-8601 UTC timestamp, and render an exception safely if one is
    # attached to the event.
    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    # JSON in production so a log aggregator can parse it; a colored console renderer in
    # development because a human reading a terminal wants prose, not a JSON blob.
    renderer = (
        structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.typing.FilteringBoundLogger:
    """Return a bound logger for one subsystem, ready to use without further configuration.

    Args:
        name: The calling module's name; the conventional call is ``get_logger(__name__)``.

    Returns:
        A structlog bound logger. Its output is shaped by whatever configure_logging set up;
        before that call it falls back to structlog's own default configuration.
    """
    # structlog.get_logger returns Any at the type level (its shape depends on the global config
    # set by configure_logging above); this cast is the one place that dynamic typing is resolved
    # into the concrete type every caller actually gets back.
    return cast(structlog.typing.FilteringBoundLogger, structlog.get_logger(name))

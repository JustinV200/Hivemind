"""Tests for hivemind.common.logging: configure_logging and get_logger.

Each test calls configure_logging itself, since codingrules section 5.5 makes a test its own
composition root; none of them depend on configuration left over from another test.

Fits into the Hive:
    Mirrors src/hivemind/common/logging.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.common.logging for the module under test.
"""

from __future__ import annotations

import pytest
import structlog

from hivemind.common.logging import configure_logging, get_logger


def test_configure_logging_rejects_an_unknown_level() -> None:
    with pytest.raises(ValueError, match="unknown logging level"):
        configure_logging(json_output=False, level="not-a-level")


def test_configure_logging_accepts_json_output() -> None:
    configure_logging(json_output=True, level="WARNING")


def test_get_logger_emits_the_event_name_and_bound_fields() -> None:
    configure_logging(json_output=False, level="DEBUG")
    logger = get_logger(__name__)

    with structlog.testing.capture_logs() as captured:
        logger.info("hivemind.test_event", cell_id="cell_ABC")

    assert captured == [
        {"event": "hivemind.test_event", "cell_id": "cell_ABC", "log_level": "info"}
    ]


def test_get_logger_respects_the_configured_minimum_level() -> None:
    # WARNING is above DEBUG, so an info() call should be filtered out entirely rather than
    # captured -- this is what make_filtering_bound_logger(numeric_level) is configured for.
    configure_logging(json_output=False, level="WARNING")
    logger = get_logger(__name__)

    with structlog.testing.capture_logs() as captured:
        logger.info("hivemind.should_be_filtered")

    assert captured == []

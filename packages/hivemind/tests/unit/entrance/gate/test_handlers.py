"""Test hivemind.entrance.gate.handlers: every refusal is the documented ErrorBody, even a 404.

The published document declares an ``ErrorBody`` for every refusal, 404 ("not found, or not served
on this listener") included. A loopback-only route on the remote listener is a 404 because it was
never mounted, so it is the router that answers it, not a route; roadmap step 10.5c's conformance
client found the router answering with FastAPI's own ``{"detail": ...}`` body instead.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from typing import ClassVar

from builders.entrance.serving import RigOptions, serving

from hivemind.common.errors import InvariantViolationError, NotFoundError
from hivemind.entrance.errors import EntranceError
from hivemind.entrance.gate import (
    NOT_FOUND_CODE,
    CapabilityDeniedError,
    ErrorBody,
    error_body,
    status_for,
)
from hivemind.entrance.gate.handlers import INTERNAL_CODE_DETAIL


class _TeapotError(EntranceError, NotFoundError):
    """A refusal that names its own status, over the category it also belongs to."""

    code: ClassVar[str] = "hivemind.entrance.teapot"
    http_status: ClassVar[int] = 418


async def test_a_route_absent_from_a_listener_answers_the_documented_error_body() -> None:
    async with serving(RigOptions(remote=True)) as rig:
        unknown = await rig.client().http.get("/v1/nowhere")
        loopback_only = await rig.client(remote=True).http.post(
            "/v1/entrance/invites", json={"label": "phone"}
        )

    for response in (unknown, loopback_only):
        assert response.status_code == 404
        assert ErrorBody.model_validate(response.json()).error == NOT_FOUND_CODE


async def test_a_method_the_path_does_not_take_answers_405_with_its_allow_header() -> None:
    async with serving() as rig:
        response = await rig.client().http.delete("/v1/goals")

    assert response.status_code == 405
    body = ErrorBody.model_validate(response.json())
    assert body.error == "hivemind.entrance.method_not_allowed"
    assert body.detail == "The request was refused: Method Not Allowed."
    assert "POST" in response.headers["allow"]


def test_an_error_that_names_its_own_status_is_answered_with_it() -> None:
    assert status_for(_TeapotError("short and stout")) == 418
    assert status_for(NotFoundError("gone")) == 404  # A category alone still decides.


def test_error_body_shapes_a_refusal_as_its_http_answer_would_be() -> None:
    status, denied = error_body(CapabilityDeniedError("entrance:answer"))
    broken_status, broken = error_body(InvariantViolationError("an internal detail"))

    assert (status, denied.error, denied.capability) == (
        403,
        "hivemind.entrance.capability_denied",
        "entrance:answer",
    )
    # A 500 keeps its code but never its own words.
    assert (broken_status, broken.detail) == (500, INTERNAL_CODE_DETAIL)

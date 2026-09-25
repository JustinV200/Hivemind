"""Test hivemind.entrance.app: one route table, two applications, and the checks around both.

Codingrules 8.11 and 8.15: the remote application never holds a loopback-only row (a 404, not a
403), the credentials the Observation Hive holds reach no mutating route beyond the Queen's inbox
and the caller's own session and push, every response carries the Content-Security-Policy, and the
loopback listener answers a foreign ``Host`` or any forwarding header with a bare 403. The tests
run against real listeners (uvicorn on loopback ports) over a real Queen.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest
from builders.entrance.serving import (
    PUBLIC_ORIGIN,
    RIG_SECTION,
    ProgramGrant,
    RigOptions,
    serving,
)

from hivemind.entrance.app import OPENAPI_PATH, route_table
from hivemind.entrance.auth.session import Listener
from hivemind.entrance.gate import (
    API_PREFIX,
    CONTENT_SECURITY_POLICY,
    LOOPBACK_ONLY,
    RouteEffect,
    RouteSpec,
)
from hivemind.entrance.landing_board import render_document
from hivemind.guard import Capability, CapabilitySet

# What the Observation Hive's device is granted: it reads, submits goals, answers, and hears push.
UI_CAPABILITIES = ("observe", "entrance:submit", "entrance:answer", "entrance:push")
# The only routes changing the Entrance itself that UI credentials reach: narrowing its own
# standing, and a person's yes or no to a request held for them (which then enters the inbox).
UI_DOOR_ROUTES = frozenset(
    {
        ("POST", "/v1/devices/{device_id}/lock"),
        ("POST", "/v1/entrance/confirmations/{pending_id}/confirm"),
        ("POST", "/v1/entrance/confirmations/{pending_id}/cancel"),
    }
)
_PARAMETER = re.compile(r"\{[a-z_]+\}")  # A path parameter in a row's template.


def _reachable(held: tuple[str, ...]) -> list[RouteSpec]:
    """Every authenticated row a device holding ``held`` passes the capability check of."""
    granted = CapabilitySet.parse(*held)
    return [
        route
        for route in route_table().routes
        if route.access.authenticated
        and (
            route.access.capability is None
            or granted.allows(Capability.parse(route.access.capability))
        )
    ]


def _concrete(path: str) -> str:
    """Fill every path parameter with a placeholder id."""
    return _PARAMETER.sub("device_0000000000000000000000000A", path)


def test_route_table_declares_listeners_and_access_for_every_row() -> None:
    routes = route_table().routes

    keys = [(route.method, route.path) for route in routes]

    assert len(keys) == len(set(keys)), "two rows share a method and path"
    for route in routes:
        assert route.path.startswith(API_PREFIX)
        assert Listener.LOOPBACK in route.listeners
        assert route.access.authenticated or route.access.capability is None
    # Exactly ADR-0041's loopback-only set, so a route joins or leaves it only on purpose: invite
    # minting (and cancelling, and registering a device offline by its key), approval, denial,
    # unlock, capability widening, revocation, reopening and operator add.
    assert {(route.method, route.path) for route in routes if route.listeners == LOOPBACK_ONLY} == {
        ("POST", "/v1/entrance/invites"),
        ("DELETE", "/v1/entrance/invites/{device_id}"),
        ("POST", "/v1/entrance/register"),
        ("POST", "/v1/entrance/pending/{device_id}/approve"),
        ("POST", "/v1/entrance/pending/{device_id}/deny"),
        ("POST", "/v1/devices/{device_id}/unlock"),
        ("POST", "/v1/devices/{device_id}/capabilities"),
        ("POST", "/v1/devices/{device_id}/revoke"),
        ("POST", "/v1/entrance/open"),
        ("POST", "/v1/entrance/operators"),
    }


def test_ui_credentials_reach_no_mutating_route_beyond_inbox_session_and_push() -> None:
    reachable = _reachable(UI_CAPABILITIES)

    doors = {(route.method, route.path) for route in reachable if route.effect is RouteEffect.DOOR}

    assert doors == UI_DOOR_ROUTES
    for route in reachable:
        if route.method == "GET":
            assert route.effect is RouteEffect.READ, route.path


def test_observe_alone_reaches_only_reads_and_its_own_session() -> None:
    reachable = _reachable(("observe",))

    effects = {route.effect for route in reachable}

    assert effects <= {RouteEffect.READ, RouteEffect.SESSION}


async def test_an_observe_only_device_is_refused_every_route_it_does_not_hold() -> None:
    held = CapabilitySet.parse("observe")
    denied = [
        route
        for route in route_table().routes
        if route.access.capability is not None
        and route.mounted_when is None
        and not held.allows(Capability.parse(route.access.capability))
    ]
    # Every refusal counts toward the denial-burst lock; this test counts refusals, not locks.
    patient = RIG_SECTION.model_copy(update={"lockout_denials": len(denied) + 1})
    async with serving(RigOptions(section=patient)) as rig:
        client, session = await rig.program(ProgramGrant(capabilities=("observe",)))

        statuses = {
            (route.method, route.path): (
                await client.call(session, route.method, _concrete(route.path))
            ).status_code
            for route in denied
        }

    assert set(statuses.values()) == {403}, statuses


async def test_every_loopback_only_row_is_not_served_on_the_remote_listener() -> None:
    loopback_only = [route for route in route_table().routes if route.listeners == LOOPBACK_ONLY]
    async with serving(RigOptions(remote=True)) as rig:
        remote = rig.client(remote=True)
        local = rig.client()

        remote_statuses = [
            (await remote.http.request(route.method, _concrete(route.path))).status_code
            for route in loopback_only
        ]
        local_statuses = [
            (await local.http.request(route.method, _concrete(route.path))).status_code
            for route in loopback_only
        ]

    assert set(remote_statuses) == {404}
    assert 404 not in local_statuses  # Mounted on loopback: the gate answers (401) instead.


async def test_every_response_carries_the_content_security_policy() -> None:
    async with serving(RigOptions(remote=True)) as rig:
        http = rig.client().http
        remote = rig.client(remote=True).http

        responses = [
            await http.get(OPENAPI_PATH),
            await http.get("/v1/devices"),  # 401: no session.
            await http.get("/v1/nowhere"),  # 404.
            await http.post("/v1/auth/challenge", json={"device_id": 7}),  # 422.
            await http.get("/v1/devices", headers={"Host": "evil.example"}),  # Loopback gate.
            await remote.get("/v1/entrance/invites"),  # Never mounted remotely.
        ]

    for response in responses:
        assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
        assert "default-src 'self'" in response.headers["content-security-policy"]


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "hive.example.net"},
        {"Host": "127.0.0.1:1"},
        {"X-Forwarded-For": "203.0.113.9"},
        {"Forwarded": "for=203.0.113.9"},
    ],
)
async def test_the_loopback_listener_refuses_a_foreign_host_or_any_forwarding_header(
    headers: dict[str, str],
) -> None:
    async with serving() as rig:
        response = await rig.client().http.get(OPENAPI_PATH, headers=headers)

    assert response.status_code == 403
    assert response.content == b""


async def test_both_listeners_serve_the_committed_document_unchanged() -> None:
    async with serving(RigOptions(remote=True)) as rig:
        local = await rig.client().http.get(OPENAPI_PATH)
        remote = await rig.client(remote=True).http.get(OPENAPI_PATH)

    assert local.content == remote.content == render_document()


async def test_both_listeners_serve_the_observation_hive_build_and_every_route_wins(
    tmp_path: Path,
) -> None:
    # A build file on a route's own path must never shadow the route: the build is mounted last.
    (tmp_path / "index.html").write_text("<title>Observation Hive</title>", encoding="utf-8")
    (tmp_path / "v1").mkdir()
    (tmp_path / "v1" / "openapi.json").write_text("{}", encoding="utf-8")
    async with serving(RigOptions(remote=True, web_root=tmp_path)) as rig:
        pages = [await rig.client(remote=side).http.get("/") for side in (False, True)]
        document = await rig.client().http.get(OPENAPI_PATH)

    assert [page.status_code for page in pages] == [200, 200]
    assert all("Observation Hive" in page.text for page in pages)
    assert all(page.headers["content-security-policy"] == CONTENT_SECURITY_POLICY for page in pages)
    assert document.content == render_document()


async def test_cors_allows_public_url_alone_and_only_on_the_remote_listener() -> None:
    preflight = {
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,x-hive-signature",
    }
    async with serving(RigOptions(remote=True)) as rig:
        remote = rig.client(remote=True).http
        allowed = await remote.options("/v1/goals", headers={**preflight, "Origin": PUBLIC_ORIGIN})
        foreign = await remote.options(
            "/v1/goals", headers={**preflight, "Origin": "https://evil.example"}
        )
        local = await rig.client().http.options(
            "/v1/goals", headers={**preflight, "Origin": PUBLIC_ORIGIN}
        )

    assert allowed.headers.get("access-control-allow-origin") == PUBLIC_ORIGIN
    assert "access-control-allow-origin" not in foreign.headers
    assert "access-control-allow-origin" not in local.headers


async def test_the_interactive_documentation_routes_do_not_exist() -> None:
    async with serving() as rig:
        http: httpx.AsyncClient = rig.client().http

        statuses = [(await http.get(path)).status_code for path in ("/docs", "/redoc")]

    assert statuses == [404, 404]


async def test_an_address_past_its_rate_is_answered_429_before_any_route() -> None:
    strict = RIG_SECTION.model_copy(update={"rate_limit_per_address": 2})
    async with serving(RigOptions(section=strict)) as rig:
        http = rig.client().http

        statuses = [(await http.get(OPENAPI_PATH)).status_code for _ in range(3)]
        last = await http.get(OPENAPI_PATH)

    assert statuses == [200, 200, 429]
    assert last.headers["content-security-policy"] == CONTENT_SECURITY_POLICY

"""Drive the built Observation Hive in a real Chromium, one step at a time, from an e2e test.

The Observation Hive (the React app the Hive Entrance, the Hive's one HTTP door, serves) can only be
judged in a browser: its passkey ceremony, its live streams and its layout at phone width do not
exist in a unit test. ``Browser`` starts the Node driver (``packages/observation-web/e2e/
observation_run.mjs``, Playwright over Chromium with a virtual authenticator, so no human taps a
passkey prompt) as a child process and speaks to it in lines of JSON: the spec first, then one step
per line, one answer per line. Because the driver is a child process read through asyncio pipes, the
test's own event loop keeps serving the Hive while the browser works, which a blocking ``run`` would
starve. ``Step`` is one answer: what the step observed, the requests the page made while it ran,
and the proof the document was never reloaded. ``unavailable`` says why a run cannot happen here
(no Node, no Playwright, no build), so the test skips with a reason instead of failing.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by
    tests/e2e/test_phase12_observation_hive.py; the driver lives in packages/observation-web/e2e.

Key invariants:
    - The child process is killed when the block exits, whatever the test did.
    - A failed step raises with the driver's own message and the screenshot taken at the failure.
    - Reports hold method and path only, never a password, a token or a request body.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

WEB_ROOT = Path(__file__).resolve().parents[3] / "observation-web"
DIST = WEB_ROOT / "dist"
DRIVER = WEB_ROOT / "e2e" / "observation_run.mjs"
GLOBAL_PLAYWRIGHT = Path("/opt/node-tools/node_modules/playwright")
CHROMIUM = Path(os.environ.get("HIVE_CHROMIUM", "/opt/pw-browsers/chromium"))
MODULE_ENV = "HIVE_PLAYWRIGHT_MODULE"
STEP_TIMEOUT_S = (
    90.0  # One step's own waits are shorter; this only frees a test from a hung driver.
)
_END_TIMEOUT_S = 30.0  # Closing Chromium and writing the report.

__all__ = ["DIST", "Browser", "Step", "unavailable"]


def unavailable() -> str | None:
    """Return why the browser run cannot happen on this machine, or None when it can."""
    if shutil.which("node") is None:
        return "Node is not installed"
    if not (DIST / "index.html").is_file():
        return f"the Observation Hive is not built ({DIST}/index.html); run `pnpm build` first"
    if not CHROMIUM.exists():
        return f"no Chromium at {CHROMIUM}; set HIVE_CHROMIUM"
    local = WEB_ROOT / "node_modules" / "playwright"
    if not (os.environ.get(MODULE_ENV) or local.is_dir() or GLOBAL_PLAYWRIGHT.is_dir()):
        return f"Playwright for Node was not found; set {MODULE_ENV}"
    return None


@dataclass(frozen=True, slots=True)
class Step:
    """One driver answer: what a step saw, and what the page did while it ran."""

    op: str
    ok: bool
    result: dict[str, Any]
    requests: tuple[tuple[str, str], ...]
    loads: int
    marker: str | None
    screenshot: str
    error: str | None = None
    sockets: tuple[str, ...] = field(default=())
    overflow_x: bool | None = None

    @classmethod
    def parse(cls, line: dict[str, Any]) -> Step:
        """Build a step from its JSON line."""
        asked = tuple((str(r["method"]), str(r["path"])) for r in line.get("requests", ()))
        return cls(
            op=str(line["op"]),
            ok=bool(line["ok"]),
            result=dict(line.get("result") or {}),
            requests=asked,
            loads=int(line.get("loads", 0)),
            marker=line.get("marker"),
            screenshot=str(line.get("screenshot", "")),
            error=line.get("error"),
            sockets=tuple(line.get("sockets", ())),
            overflow_x=line.get("overflowX"),
        )


class Browser:
    """A running driver: send it steps, read what each one saw."""

    def __init__(self, process: asyncio.subprocess.Process, out_dir: Path) -> None:
        """Hold the driver process and the directory its screenshots land in."""
        self._process = process
        self.out_dir = out_dir
        self.steps: list[Step] = []

    async def step(self, op: str, **args: object) -> Step:
        """Run one step in the page and return what it saw.

        Args:
            op: The driver's step name (``join``, ``open``, ``send_chat`` ...).
            args: The step's arguments, as the driver reads them.

        Returns:
            The step's answer.

        Raises:
            AssertionError: The step failed in the page; the message carries the driver's error.
        """
        line = await self.ask({"op": op, **args})
        step = Step.parse(line)
        self.steps.append(step)
        if not step.ok:
            raise AssertionError(
                f"browser step {op} failed ({self.out_dir / step.screenshot}):\n{step.error}"
            )
        return step

    async def ask(self, message: dict[str, Any]) -> dict[str, Any]:
        """Write one line to the driver and read its one-line answer."""
        stdin, stdout = self._process.stdin, self._process.stdout
        assert stdin is not None and stdout is not None
        stdin.write((json.dumps(message) + "\n").encode())
        # Waits on the child process: the Hive keeps serving in this loop meanwhile.
        await stdin.drain()
        async with asyncio.timeout(STEP_TIMEOUT_S):
            raw = await stdout.readline()
        if not raw:
            raise AssertionError("the browser driver exited early; see its stderr above")
        answer: dict[str, Any] = json.loads(raw)
        return answer

    async def end(self) -> dict[str, Any]:
        """Close the browser; return the driver's whole report."""
        stdin, stdout = self._process.stdin, self._process.stdout
        assert stdin is not None and stdout is not None
        stdin.write(b'{"op":"end"}\n')
        await stdin.drain()
        async with asyncio.timeout(_END_TIMEOUT_S):
            raw = await stdout.readline()
        report: dict[str, Any] = json.loads(raw)["report"]
        return report


@asynccontextmanager
async def launched(base_url: str, out_dir: Path, password: str) -> AsyncIterator[Browser]:
    """Start the driver on the Hive at ``base_url``; kill it when the block exits.

    Args:
        base_url: The Entrance's address as the browser must name it (``localhost``, the
            loopback listener's passkey relying party).
        out_dir: Where screenshots and ``report.json`` are written; it must exist.
        password: The operator's password, typed into the login form.

    Yields:
        The browser, ready for its first step.
    """
    process = await asyncio.create_subprocess_exec(
        "node",
        str(DRIVER),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=None,
        cwd=WEB_ROOT,
    )
    try:
        browser = Browser(process, out_dir)
        spec = {"baseUrl": base_url, "password": password, "outDir": str(out_dir)}
        await browser.ask(spec)
        yield browser
    finally:
        with suppress(ProcessLookupError):
            process.kill()
        await process.wait()

# Phase 6 exit check on the Windows Hive Stand

Phase 6's exit criteria (`.claude/roadmap.md`, "Exit criteria" after "## Phase 6") require the
fixture-site login goal to succeed "via Playwright" on the Windows Hive Stand. The Linux sandbox
these steps were built in has no Windows host, so this half of the criterion has to be run by
hand, once, on an actual Windows machine (the operator's own development host). Everything else
the roadmap asks for -- a Linux Real Cell with a lease-started Xvfb, a `desktop-ubuntu` Virtual
Cell, the recording playback, the wrong-click rollback and Alarm -- is proven automatically by
`packages/hivemind/tests/e2e/test_phase6_exit_criteria.py` and does not need this runbook.

## What this proves

`TaskNeeds.browser_only` never starts a display (`hivemind.exoskeleton.attach.plan.plan_attach`'s
own `DisplaySource.NONE`): the only Exoskeleton peripheral it attaches is the browser, found by
`hivemind.exoskeleton.browser.locate`'s locator (Edge first on Windows, per ADR-0031) rather than
the pre-installed Chromium the Linux sandbox uses. That is the one Exoskeleton path a Windows Real
Cell can take at all, so it is also the one this check exercises: no Xvfb, no openbox, no
xdotool, no PulseAudio anywhere in it.

## Prerequisites

- A Windows checkout of this repository, on the branch this session worked from.
- [`uv`](https://docs.astral.sh/uv/) installed and on `PATH`.
- Microsoft Edge or Google Chrome installed (either already is, on almost any Windows 11 machine;
  the browser locator tries Edge first). Playwright drives it over CDP -- it is never launched by
  Playwright's own `playwright install`, so nothing needs to be downloaded.

## Step 1: build the environment

Open PowerShell in the repository root:

```powershell
uv sync --frozen --all-groups --all-packages --all-extras
```

This installs the `hivemind[browser]` extra (Playwright's Python package; not a browser binary)
alongside everything else. Never run `playwright install` -- the locator finds the system's own
Edge or Chrome instead.

## Step 2: run the automated check

```powershell
uv run pytest -q -m e2e packages/hivemind/tests/e2e/test_phase6_exit_criteria.py::test_browser_only_login_needs_no_x11_and_leaves_the_stand_as_found
```

This scripts the planner's reply and the Drone's tool calls the same way the rest of the suite
does (`tests/e2e/phase6_exit_helpers.py`; no model credentials needed) and serves the fixture
site over loopback http, so it needs nothing from the network. It runs the fixture-site login
goal on the Hive Stand with `TaskNeeds.browser_only = true`, through the real Queen, Warden,
Capping gate, flight recorder and Drone, driving Edge or Chrome for real.

### What a pass looks like

```text
packages/hivemind/tests/e2e/test_phase6_exit_criteria.py .                [100%]
1 passed in Ns
```

One test, no skips. A skip here (`Playwright is not installed here...`) means step 1 did not
finish; re-run it. No window appears while it runs: `browser_only` attaches no display
(`DisplaySource.NONE`), and ADR-0031's own rule is headed only in an attached display, headless
otherwise, so the browser it drives is headless here, on Windows exactly as on Linux.

If you would rather see the whole suite (it also needs `xvfb`/`openbox`/`xdotool`/`imagemagick`,
which do not exist on Windows, so the other two scenarios skip there with a clear reason; only
this one test is Windows-capable):

```powershell
uv run pytest -q -m e2e packages/hivemind/tests/e2e/test_phase6_exit_criteria.py
```

expect `1 passed, 2 skipped`.

## Step 3 (optional): watch it happen with a real model

The automated check above proves the mechanism with a scripted model. To see an actual model log
in to the fixture site, point a manifest at a local server (LM Studio, Ollama, or any
OpenAI-compatible server) the way `docs/manifests/local.toml` does. The Hive Stand's default
`[hive_stand] access_level` (`SCRATCH`) already grants `exoskeleton:browser`
(`hivemind.guard.access.ceiling_for`), so a browser-only need works with no manifest change
there; leave `[placement] prefer = "real"` (the default) so the goal stays on the Windows Hive
Stand rather than provisioning a Linux Virtual Cell it cannot drive. Serve the fixture site
yourself first (a second PowerShell window):

```powershell
python -m http.server 8080 --directory packages\hivemind\tests\fixtures\sites\login
```

Then, with a manifest as above (`hive.toml`) and the server's provider running:

```powershell
uv run hive run "Open http://127.0.0.1:8080/login.html, log in as alice / honeycomb (the fixture's own account, printed in that directory's README), and confirm the welcome page." --timeout 120
```

A pass prints a succeeded `GoalReport` and, in the site's own terminal, two `GET` lines
(`login.html` then `welcome.html`). `hive recordings list` then shows one recording for the run;
`hive recordings export <id> --out <directory>` writes its playback HTML, which should show two
frames (before and after) for every action, the same shape the automated check's own recording
takes.

Unlike step 2, the planner here is a real model deciding for itself what the goal needs: it may
ask for a full desktop rather than the browser alone, which fails closed on Windows with an
`EXOSKELETON_FAILED` Alarm (no Xvfb-equivalent to start there, codingrules section 8.7). That is
a planning choice, not a failure of this check; if it happens, say so explicitly in the goal text
("using only the browser, no other window") and try again. Step 2 is the exit criterion's own
proof either way.

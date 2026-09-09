"""Prove hivemind.wardens.autopilot imports no hivemind.llm, directly or transitively.

Codingrules section 4: "Any module under a directory named `autopilot/` may not import
`hivemind.llm`, directly or transitively; `lint-imports` enforces it." `lint-imports` is the
authoritative, whole-repo gate for this rule (run separately, see the dispatch's own report); this
test is the runtime-observable half of the same guarantee, importable and fast in the normal unit
test run.

Fits into the Hive:
    Mirrors src/hivemind/wardens/autopilot/ as a whole (codingrules section 3): a package-level
    invariant test, not a single module's.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/codingrules.md section 4 for the autopilot-never-imports-llm rule.
    - hivemind.wardens.autopilot for the package under test.
"""

from __future__ import annotations

import subprocess
import sys


def test_importing_wardens_autopilot_never_pulls_in_hivemind_llm() -> None:
    # A fresh interpreter, not this test process's own sys.modules: hivemind.llm may already be
    # imported by something else this test run touched first, which would make an in-process
    # check pass or fail by accident of test order rather than by what autopilot itself imports.
    #
    # A plain `import hivemind.wardens.autopilot` is not a precise enough probe: Python always
    # runs a package's own __init__.py before any of its submodules, so that line would also run
    # hivemind/wardens/__init__.py -- which legitimately imports hivemind.llm on behalf of
    # `hivemind.wardens.awake` and `.spawn` (Layer 5 modules the layer table allows to use it;
    # only the autopilot/ directory itself is barred). A failure there would be a false positive
    # against this test's own claim. `importlib.util.find_spec` locates hivemind.wardens without
    # executing it, and `module_from_spec` builds an empty, un-executed module object for it, so
    # inserting that stub into sys.modules lets `import hivemind.wardens.autopilot` proceed
    # straight to autopilot's own package and submodules -- isolating exactly the import graph
    # codingrules section 4 constrains.
    script = (
        "import sys\n"
        "import importlib.util\n"
        "spec = importlib.util.find_spec('hivemind.wardens')\n"
        "sys.modules['hivemind.wardens'] = importlib.util.module_from_spec(spec)\n"
        "import hivemind.wardens.autopilot\n"
        "assert 'hivemind.llm' not in sys.modules, sorted(sys.modules)\n"
    )
    # SAFETY: argument list, no shell, a fixed literal script and sys.executable -- nothing here
    # is untrusted input; the subprocess exists only to get a fresh sys.modules to check.
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

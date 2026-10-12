"""Start the runner when the package is run with `python -m ...quarantine_comb.runner`.

The Quarantine Comb executes the runner inside its sandbox as a module so the interpreter's own
import machinery finds the Hive's installed package; this file is the thin entry that hands the
command line to `main` and turns its return value into the process exit code.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.quarantine_comb.
    Run by the sandbox on the Comb's behalf. Calls into `runner.main` only.

Key invariants:
    - Importing this module as `__main__` is the only way it runs; there is no other caller.
    - The process ends with `os._exit` straight after the report is written, so nothing the tool
      registered to run at exit can run.

See Also:
    - hivemind.royal_jelly.quarantine_comb.runner.main for what the runner does.
"""

import os
import sys

from hivemind.royal_jelly.quarantine_comb.runner.main import main

if __name__ == "__main__":
    code = main(sys.argv[1:])
    sys.stdout.flush()
    sys.stderr.flush()
    # Leave without running exit handlers or waiting for threads: the tool's code has run in this
    # process, and an `atexit` function or a daemon thread it left behind must not outlive the
    # report.
    os._exit(code)

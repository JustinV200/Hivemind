# `hivemind.supervision.capping.leave`

Roadmap step 5.0c: the pure autopilot policy that decides whether a path a task's plan declared
(`waggle.messages.PlannedLeaving`, roadmap step 5.0b) actually gets to persist past its lease's
release. `decide(request, cell, declared, table) -> ALLOW | ASK | DENY`. A bee never decides this
alone (phase 5 preamble): `ALLOW` means the policy decided by itself; `ASK` means roadmap step
5.0d's `HumanCheck` (`hivemind.supervision.capping.checks.human`) raises a Question and blocks on
it; `DENY` means the write still applies (inside scratch, or outside it with `persist=False`) but
is restored on release exactly as it always was before phase 5.

## Modules

| Module | Provides |
|---|---|
| `model.py` | `PathClass`, `LeaveVerdict`, `LeaveRequest`, `LeaveCellFacts`: the policy's own data. |
| `classify.py` | `classify_path`: pure classification of a resolved path into a `PathClass`. |
| `executable.py` | `looks_executable`: a pure, conservative guess at whether a path is an executable. |
| `matcher.py` | `matches_leaving`: match a resolved path against a task's declared `leaves`. |
| `table.py` | `LeavePolicyTable`, `load_leave_policy`: `leave-policy.toml`, as data, validated. |
| `policy.py` | `decide`: the pure function tying the four modules above together. |
| `persist.py` | `decide_persist`: turns a verdict into persist/approved_by/reason for `apply.py`. |

`hivemind.supervision.capping.apply` is the one effectful caller: for every path an
`OUTSIDE_SCRATCH_WRITE` proposal touches, it reads the session, applies the diff, then calls
`matches_leaving` (declared?), `classify_path` (which class?) and `looks_executable` (executable?)
to build a `LeaveRequest`, and `decide` to get a verdict, before calling `LeaseView.
note_restore_path` with `persist`/`approved_by` set accordingly.

## Path classes

`keep_root` (under the manifest's `[hive_stand] keep_root`, roadmap step 5.0e -- `None` today,
so nothing classifies as `keep_root` yet), `home` (under the Cell's own home directory), `startup`
(a Windows Startup folder, a systemd unit directory, a shell rc file, LaunchAgents/LaunchDaemons,
...), `system` (`/etc`, `/usr`, `/bin`, `/opt`, `C:\Windows`, `C:\Program Files*`, ...), and
`other` -- a path `classify_path` recognises as none of the four. The roadmap names only the first
four; `other` is this dispatch's own conservative addition (a real path a plan could declare that
is neither home, system, startup nor keep_root -- a mount point, a second drive, a network share)
and is never treated more permissively than `system` in the shipped `leave-policy.toml`.

Precedence, most to least specific: `keep_root`, then `startup`, then `system`, then `home`;
anything left over is `other`.

## Matcher semantics

`matches_leaving(path, declared, os_family, home)` decides whether a resolved path is covered by
one of a task's declared `PlannedLeaving.pattern`s, conservatively (roadmap step 5.0c: "decide the
glob semantics conservatively"):

- **No `*` in the pattern**: the pattern covers itself and everything nested under it. This lets a
  task declare a directory once (`~/Projects/myapp`) and have every file it writes inside that
  directory persist, without enumerating each one; an exact file pattern only ever "covers" itself,
  since nothing can be nested under a file.
- **`*`**: matches any run of characters *within one path segment* (never crosses a `/` or `\`).
- **`**`**: matches any run of characters, including separators, so it can span segments.
- **`~`**: expands against the Cell's own home directory, textually, before either rule above is
  applied.

Comparison always goes through `PureWindowsPath`/`PurePosixPath` (chosen by the Cell's own
`OsFamily`, never the host process's actual OS), so Windows comparisons are case-insensitive and
POSIX comparisons stay case-sensitive, matching each platform's own filesystem convention. The
matcher never touches the filesystem: "covers" is decided from the two path strings alone.

## Design choices

- **`decide`'s fourth argument.** Roadmap step 5.0c's own sentence names three arguments
  (`request, cell, declared`); `decide` also takes `table: LeavePolicyTable` explicitly, the same
  way `hivemind.supervision.capping.tiers.checks_for` takes a `TierSpec` explicitly rather than
  reading one off a shared table -- codingrules section 5.5 forbids module-level state, and a
  fourth explicit parameter (still within the section 5.1 parameter limit) is what keeps this
  function pure and independently testable without a fixture that reaches into a loaded-once
  global.
- **The `other` path class.** Not named in the roadmap's own four; added because a real absolute
  pattern can plausibly name a path that is none of the other three (a second drive, a mount
  point, a network share). Its shipped default (`ASK` on the Hive Stand, `DENY` on a borrowed
  device) matches `system`'s own row exactly: unrecognised is never safer than recognised-and-
  sensitive.
- **`is_executable` has no real permission-bit check.** Neither `CellSession` nor a unified diff
  carries a POSIX mode bit; `looks_executable` guesses from the suffix and (POSIX) a `#!` shebang
  in the content actually being written. A later phase that wires a real `stat` through
  `CellSession` can replace this guess without changing `decide`'s own shape.

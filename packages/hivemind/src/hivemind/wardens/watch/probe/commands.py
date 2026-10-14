"""Define the fixed commands a Warden's own session runs to look at its Cell, and their bounds.

A Warden watching a Cell it is local to (the Hive Stand's own, later a colonized device's) looks
through its own `CellSession`, the only way anything runs a command on a Cell (codingrules section
4). What it may run is a closed list written here, never text from a request, a model or a caller:
one POSIX shell script, assembled from named fragments (one per thing to read) and always the same
for the same kinds of observation, and one more, fixed, that reads the end of some files the first
one listed. The scripts only read kernel tables under `/proc`, the filesystem's own free-space
figures (`df`), the directory listings under the roots the operator named (`find`) and the tails of
regular files found there (`tail`); they create, change and delete nothing, and `/proc` is not a
place anything can be written by them. Everything variable reaches a script as a positional
parameter, quoted inside it: the roots, the directory `df` describes and the files to tail can
never become part of the shell's own text. Every volume a command could make large is bounded in
the command itself (`head -c`), so a Cell with a million files or processes costs a bounded amount
of output, and every command has a named timeout, after which the session kills it and the sample
is skipped.

Every section of output starts with a marker line that carries a random mark, chosen afresh for
each run and handed to the script as its first parameter. A file name, a process name or a log line
is whatever the machine holds, and may contain a newline and a line that looks like a marker; it
cannot know the mark, so it cannot end a section early or start a fake one.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    watch.probe sub-package. Used by `hivemind.wardens.watch.probe.session.SessionWatchProbe`
    only. Calls into waggle's swarm messages only.

Key invariants:
    - Nothing here writes: every fragment is a `cat`, a `getconf`, a `df`, a `find` with `-printf`
      and no action, or a `tail`.
    - The script text is a function of the observation kinds alone; no value from outside is ever
      formatted into it.
    - `find` never follows a symlink below a root, lists only regular files, and `tail` is not run
      on a path that has turned into a symlink since it was listed.
    - Linux is the one platform with collectors (`/proc`, GNU findutils): on another platform the
      sections come out empty and the observation says so.

See Also:
    - docs/adr/0054-access-levels-watch-mode-and-the-patrol.md for the four observations.
    - hivemind.wardens.watch.probe.system and .files for the parsers of what these print.
    - pollen.executors.observe for the packet's own collectors, which answer the same question
      on a device the Warden is not local to.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from waggle.messages.swarm import ObservationKind

USER_HZ = 100  # Linux counts process times in USER_HZ ticks, a fixed 100 on every mainstream CPU.
MARK_PREFIX = "@@"  # Opens every marker line; the run's random token follows it.
PROCESS_BYTES = 4_194_304  # At most 4 MiB of /proc/<pid>/stat: thousands of processes, no more.
LISTING_BYTES = 1_048_576  # At most 1 MiB of file listing per root, about ten thousand files.
LISTING_DEPTH = 6  # Directory levels below a root; deeper trees are not logs or recent work.
TAIL_BYTES = 16_384  # How much of the end of a log file is read, as bytes (a line may be long).
TAIL_LINES = 20  # The newest lines kept from each log file.
MAX_LOG_FILES = 20  # The newest log files read per sample.
RECENT_WINDOW_S = 300.0  # The first look at the roots reports files changed in the last 5 minutes.
SYSTEM_TIMEOUT_S = 45.0  # Seconds the script may take: /proc and a bounded find are milliseconds.
TAIL_TIMEOUT_S = 20.0  # Seconds the tail of up to MAX_LOG_FILES small reads may take.
EXEC_ENV = {
    "LC_ALL": "C"
}  # One locale, so every number and date prints the way the parsers read it.

# find counts a root's own files as depth 1; a root's direct files are the first level.
_FIND_DEPTH = LISTING_DEPTH + 1

# Printed before each section: a blank line first, so a marker starts a line even when the section
# before it ended in a NUL-terminated record with no newline.
_MARKER = "printf '\\n%s %s\\n' \"$mark\""

# Each fragment prints one named section. Errors from a process that exits mid-read, or a tool this
# platform lacks, are discarded (`2>/dev/null`): a section that comes out empty is a section the
# platform did not give, and the observation says so.
_FRAGMENTS: dict[str, str] = {
    "stat": "cat /proc/stat",
    "uptime": "cat /proc/uptime",
    "pagesize": "getconf PAGESIZE",
    "procs": f"cat /proc/[0-9]*/stat | head -c {PROCESS_BYTES}",
    "loadavg": "cat /proc/loadavg",
    "meminfo": "cat /proc/meminfo",
    "netdev": "cat /proc/net/dev",
    "df": 'df -Pk -- "$dir"',
    # One section per root (root1, root2, ...): GNU find's -printf prints "mtime<TAB>size<TAB>path"
    # and a NUL, the one byte a path cannot hold, so no file name can split or forge a record.
    # -H follows the root itself if it is a link, and nothing below it.
    "roots": (
        'n=0; for root in "$@"; do n=$((n+1)); '
        f'{_MARKER} "root$n"; '
        f"find -H \"$root\" -maxdepth {_FIND_DEPTH} -type f -printf '%T@\\t%s\\t%p\\0' "
        f"| head -c {LISTING_BYTES}; done"
    ),
}

# Which sections each kind of observation needs, in the order they are printed.
_SECTIONS_FOR: dict[ObservationKind, tuple[str, ...]] = {
    ObservationKind.PROCESS_LIST: ("stat", "uptime", "pagesize", "procs"),
    ObservationKind.RESOURCE_USE: ("stat", "uptime", "loadavg", "meminfo", "netdev", "df"),
    ObservationKind.LOGS: ("roots",),
    ObservationKind.FILE_CHANGES: ("roots",),
}

# The order sections are printed in, whatever order the kinds were asked for in.
_SECTION_ORDER = tuple(_FRAGMENTS)

# The second script: for each file given, a marker that carries the file's position among them
# (never its name, which could hold anything) and the end of the file, if it is still a plain file.
_TAIL_SCRIPT = "\n".join(
    (
        "mark=$1; shift; n=0",
        'for file in "$@"; do',
        "n=$((n+1))",
        f'{_MARKER} "tail$n"',
        'if [ -f "$file" ] && [ ! -L "$file" ]; then',
        f'tail -c {TAIL_BYTES} -- "$file" 2>/dev/null',
        "fi",
        "done",
    )
)

__all__ = [
    "EXEC_ENV",
    "LISTING_BYTES",
    "MARK_PREFIX",
    "MAX_LOG_FILES",
    "PROCESS_BYTES",
    "RECENT_WINDOW_S",
    "SYSTEM_TIMEOUT_S",
    "TAIL_BYTES",
    "TAIL_LINES",
    "TAIL_TIMEOUT_S",
    "USER_HZ",
    "sections_for",
    "system_argv",
    "tail_argv",
]


def sections_for(kinds: Iterable[ObservationKind]) -> tuple[str, ...]:
    """Return the sections the script prints for `kinds`, once each, in a fixed order.

    Args:
        kinds: The kinds of observation asked for.

    Returns:
        Every section any of them needs, deduplicated (the process list and resource use both need
        `stat` and `uptime`; logs and file changes share one listing).
    """
    needed = {name for kind in kinds for name in _SECTIONS_FOR[kind]}
    return tuple(name for name in _SECTION_ORDER if name in needed)


def system_argv(
    sections: Iterable[str], mark: str, directory: str, roots: Sequence[str]
) -> tuple[str, ...]:
    """Build the argv that prints `sections` of the machine's state, each under its marker.

    The script is the same for the same sections; the mark, the directory whose filesystem `df`
    describes and the roots reach it as parameters, never as text in the script.

    Args:
        sections: Names from `sections_for`; an unknown name is a bug and raises KeyError.
        mark: This run's random token, prefixed with `MARK_PREFIX`.
        directory: The directory `df` reports on.
        roots: Absolute directories to list; each becomes the section `root<position from 1>`.

    Returns:
        The command and its arguments, for `ExecSpec.argv`.
    """
    lines = ["mark=$1; dir=$2; shift 2"]
    for name in sections:
        # printf, not echo: the marker's text and its newline are the same on every shell.
        lines.append(f"{_MARKER} {name}; {{ {_FRAGMENTS[name]}; }} 2>/dev/null")
    return ("sh", "-c", "\n".join(lines), "sh", mark, directory, *roots)


def tail_argv(mark: str, paths: Iterable[str]) -> tuple[str, ...]:
    """Build the argv that prints the end of each of `paths`, each under its marker.

    Args:
        mark: This run's random token, prefixed with `MARK_PREFIX`.
        paths: Absolute files the first script's own listing found under a root, in the order their
            markers (`tail1`, `tail2`, ...) will be read back.

    Returns:
        The command and its arguments, for `ExecSpec.argv`.
    """
    return ("sh", "-c", _TAIL_SCRIPT, "sh", mark, *paths)

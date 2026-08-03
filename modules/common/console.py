"""One vocabulary for everything this system prints to a terminal.

Every stage used to invent its own: three rule widths, `->` next to `→`, warnings that
hung their continuation lines under a different column each time, and labels that lined
up only within the one function that wrote them. Reading a full pipeline run meant
re-learning the layout at every stage boundary. This module is the single place that
decides what a heading, a field, a warning and a result look like, so a run reads as one
document instead of seven.

The grammar is small on purpose:

* :func:`header` / :func:`section` -- where one piece of work starts;
* :func:`field` -- a ``label: value`` line inside a stage, aligned to a fixed column so
  consecutive fields from unrelated call sites still line up;
* :func:`summary` -- a block of fields written together, aligned to *its own* widest
  label (for the long-labelled end-of-run reports);
* :func:`item` -- one entry of a list underneath a field;
* :func:`warn` / :func:`error` / :func:`note` and the tag helpers -- everything else.

The tags in use are ``ok``, ``info``, ``warn``, ``error``, ``fail`` (a stage or a piece
of work that did not finish), ``skip`` / ``stale`` / ``stop`` (the runner's decisions
about a stage it did not run) and ``retry``. Adding a tag is adding a word to the
vocabulary: it belongs here, in this list, not only at the call site.

Output is deliberately plain ASCII. A Windows console in a legacy code page mangles box
drawing and arrows, and the messages most worth reading here (NO GPU IN USE, a stale
cache, a missing dependency) are exactly the ones nobody can afford to see mangled.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
from typing import Iterable


def _prepare(stream) -> None:
    """Make ``stream`` fit to carry this system's output, if it needs anything.

    Stroke names are Chinese (``切球``, ``未知球種``), and on Windows they only survive
    to a real console -- redirect the run to a file or a pipe and the interpreter falls
    back to the ANSI code page, which turns them into mojibake or, worse, raises
    UnicodeEncodeError halfway through a stage that has already done its work.

    This happens at import, so it reaches every entry point without each one having to
    remember. That makes it a side effect on whoever imports this package, which is why
    it is conditional: a stream that is already UTF-8 on a console -- the normal case --
    is left exactly as it was found.
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return  # not a real text stream (pytest's capture, a doctest harness)

    options: dict[str, object] = {}
    if (getattr(stream, "encoding", "") or "").replace("-", "").lower() != "utf8":
        options.update(encoding="utf-8", errors="replace")
    try:
        if not stream.isatty():
            # Redirected: the default block buffering hides a 25-minute run's output
            # until it ends, and drops it entirely if the run is interrupted.
            options["line_buffering"] = True
    except (AttributeError, ValueError, OSError):
        pass

    if options:
        try:
            reconfigure(**options)
        except (AttributeError, ValueError, OSError):
            pass


for _stream in (sys.stdout, sys.stderr):
    _prepare(_stream)


def interactive() -> bool:
    """Whether stdout is a terminal that can be redrawn -- see :func:`hold_line`."""
    try:
        return bool(sys.stdout.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def columns() -> int:
    """Usable width of the current terminal, or 80 when there is nothing to ask."""
    return max(shutil.get_terminal_size((80, 24)).columns, 40)

#: Width of every horizontal rule. 72 keeps a heading intact in an 80-column console.
RULE_WIDTH = 72

#: One level of indentation. Stage bodies sit at level 1, list items at level 2.
INDENT = "  "

#: Column the message after a ``[tag]`` starts at. Sized for the longest tag, ``[error]``.
TAG_WIDTH = 8

#: Column a :func:`field` value starts at, counted from the start of the label. Sized for
#: the longest label in the pipeline (``dense scan``, ``scoreboard``).
FIELD_WIDTH = 12


#: Characters a progress bar has left on the current line without terminating it. Any
#: line printed while that is non-zero would land *on top of* the bar, which is how a
#: per-segment line used to end up spliced into the middle of one.
_pending_line = 0


def hold_line(length: int) -> None:
    """A progress bar has drawn ``length`` characters and not ended the line."""
    global _pending_line, _printed
    _pending_line = length
    _printed = True


def release_line() -> None:
    """The owner of the current line has ended it itself."""
    global _pending_line, _printed
    _pending_line = 0
    _printed = True


def pending() -> int:
    """How much of the current line is still occupied.

    A progress bar draws itself rather than going through :func:`_print`, so it has to
    ask how wide the thing it is about to overwrite is. Its *own* last render is not the
    answer: the runner's stage bar and a phase bar inside that stage share one line, and
    whichever draws the shorter line has to cover the other one's tail.
    """
    return _pending_line


def _erase_pending_line() -> None:
    """Wipe a live progress bar so the next line starts clean; it redraws on its next update."""
    global _pending_line
    if _pending_line:
        print("\r" + " " * _pending_line + "\r", end="", flush=True)
        _pending_line = 0


#: Whether anything has been printed yet, so :func:`section` can space itself off the
#: text above without opening a run on a blank line.
_printed = False


def _print(text: str = "", stream=None) -> None:
    """The one place this system writes a line of terminal output."""
    global _printed
    _erase_pending_line()
    print(text, file=stream)
    _printed = True


def _write(text: str, indent: int = 0, stream=None) -> None:
    """Print ``text`` at ``indent``, indenting continuation lines to match."""
    pad = INDENT * indent
    for line in str(text).split("\n"):
        _print(f"{pad}{line}".rstrip(), stream)


def blank() -> None:
    _print()


def rule(char: str = "-") -> None:
    _print(char * RULE_WIDTH)


def header(title: str, subtitle: str | None = None) -> None:
    """Top-level banner: what this whole invocation is doing."""
    rule("=")
    _print(title)
    if subtitle:
        _print(subtitle)
    rule("=")


def section(title: str) -> None:
    """One piece of work inside a run -- a pipeline stage, or a phase of one.

    Written as a rule with the title inline so a long log stays scannable without
    spending three lines on every boundary, and spaced off whatever came before it --
    but only when there *is* something before it, so a single-stage run does not open
    on an empty line.
    """
    if _printed:
        blank()
    _print(f"== {title} ".ljust(RULE_WIDTH, "="))


def _labelled(label: str, value, width: int, indent: int) -> None:
    """``label: value``, continuation lines hanging under the value column."""
    pad = INDENT * indent
    # A label that fills the column pushes its value out rather than losing the space
    # between them: `keep ranges:5` is not a line anyone should have to read.
    width = max(width, len(label) + 2)
    head = f"{label}:".ljust(width)
    lines = str(value).split("\n")
    _print(f"{pad}{head}{lines[0]}".rstrip())
    for line in lines[1:]:
        _print(f"{pad}{' ' * width}{line}".rstrip())


def field(label: str, value, indent: int = 1) -> None:
    """A ``label: value`` line aligned to :data:`FIELD_WIDTH`.

    Fixed width, not fitted: these are written one at a time from call sites that never
    see each other, and they still have to line up on screen.
    """
    _labelled(label, value, FIELD_WIDTH, indent)


def summary(rows: Iterable[tuple[str, object]], indent: int = 1) -> None:
    """A block of fields aligned to its own widest label.

    For end-of-run reports, whose labels are sentences rather than words and would push
    every :func:`field` in the process out to an unreadable column. Rows whose value is
    ``None`` are dropped, so a caller can list optional lines inline.
    """
    kept = [(str(label), value) for label, value in rows if value is not None]
    if not kept:
        return
    width = max(len(label) for label, _ in kept) + 2
    for label, value in kept:
        _labelled(label, value, width, indent)


def item(text: str, indent: int = 2) -> None:
    """One entry of a list under a field -- per-segment lines and the like."""
    _write(text, indent)


def note(text: str, indent: int = 1) -> None:
    """Plain prose inside a stage body. No tag: this is the un-alarming case."""
    _write(text, indent)


def tag(name: str, text: str, indent: int = 1, stream=None) -> None:
    """``[name] text``, with continuation lines hanging under the text column.

    The hanging indent is why this exists: multi-line warnings were the worst offender
    in the old output, each one guessing its own continuation column.
    """
    head = f"[{name}]".ljust(TAG_WIDTH)
    hang = " " * TAG_WIDTH
    lines = str(text).split("\n")
    pad = INDENT * indent
    _print(f"{pad}{head}{lines[0]}".rstrip(), stream)
    for line in lines[1:]:
        _print(f"{pad}{hang}{line}".rstrip(), stream)


def ok(text: str, indent: int = 1) -> None:
    tag("ok", text, indent)


def info(text: str, indent: int = 1) -> None:
    tag("info", text, indent)


def warn(text: str, indent: int = 1) -> None:
    tag("warn", text, indent)


def error(text: str, indent: int = 0) -> None:
    """An error, on stderr, so a piped run keeps it out of the parsed output."""
    tag("error", text, indent, stream=sys.stderr)


def fail(text: str, indent: int = 1) -> None:
    """Work that did not finish. On stderr for the same reason :func:`error` is.

    Separate from :func:`error` because it reads differently -- an error is a thing that
    went wrong, a failure is a named piece of work that stopped -- but it must land on
    the same stream, or ``2> run.err`` on a pipeline run captures everything except
    which stage died.
    """
    tag("fail", text, indent, stream=sys.stderr)


@contextlib.contextmanager
def muted():
    """Swallow Python-level writes to stdout inside the block.

    For third-party model loaders that chat about their own internals -- rtmlib's
    ``load <path>.onnx with onnxruntime backend``, onnxruntime's ``Skip loading CUDA and
    cuDNN DLLs`` -- in the middle of a stage's report. Both are plain ``print`` calls,
    which is the only reason this can stay at the Python level.

    It must stay there. Muting the file descriptor instead (``os.dup2`` onto devnull)
    breaks on a Windows console: ``sys.stdout`` is a ``_WindowsConsoleIO`` holding a
    console handle, and the moment fd 1 is not a console any more its ``WriteConsoleW``
    fails with ``WinError 1``, taking down the next thing that prints -- including the
    handler trying to report it.

    A C++ writer therefore goes unmuted, and should be silenced at its own source (see
    ``set_default_logger_severity`` in ``modules.pose.estimator``). Only stdout is
    touched: a library with something to say about a *failure* says it on stderr or by
    raising, and neither is worth hiding to tidy up a heading.

    ``redirect_stdout`` swaps a process-global, so this mutes *every* thread for the
    duration, not just the one that opened the block. Keep it around single-threaded
    setup work -- model construction, an import -- and never around a worker pool.
    """
    with contextlib.redirect_stdout(io.StringIO()):
        yield


def duration(seconds: float) -> str:
    """A wall-clock duration a human reads at a glance: ``8.4s``, ``4m 12s``, ``1h 05m``."""
    seconds = max(float(seconds), 0.0)
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def count(n: int, singular: str, plural: str | None = None) -> str:
    """``1 segment`` / ``4 segments`` -- said the same way everywhere."""
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"

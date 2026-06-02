"""Centralised logging configuration for ESFEX.

Both ``cli.py`` and ``runner.Orchestrator._setup_logging`` used to
install their own console handler on the root logger. Each ``logger.info``
call then fired through *both* handlers and the user saw every line
twice — the "Year 2049 completed" duplicate in your incident log was
exactly this. Funnelling both call sites through ``setup_console_logging``
fixes that: the handler carries a marker attribute, and a second call
reconfigures the existing handler instead of attaching another one.

Three console-verbosity modes are recognised, controlled by the
``general.console_log_level`` preference (or the ``--verbose`` CLI flag):

* ``"basic"`` (default) — only milestones plus WARNING/ERROR. Designed
  for the GUI run console: the user sees overall progress without
  being flooded by per-bus / per-window / per-scenario chatter.
* ``"verbose"`` — INFO and above, no milestone filter. Equivalent to
  the previous default behaviour.
* ``"debug"`` — everything, including DEBUG. Useful for development.

What counts as a milestone in ``basic`` mode:

1. Any record at WARNING or above (always shown).
2. Any INFO record emitted by a logger in ``_MILESTONE_LOGGERS``
   (currently the orchestrator and the CLI front-end).
3. Any INFO record explicitly tagged with ``extra={"milestone": True}``.

The file handler attached by ``setup_file_logging`` is always at DEBUG
so the on-disk log file remains complete regardless of console mode.
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

# Console handler is tagged with this attribute so we can find and
# reconfigure it across calls instead of duplicating it.
_CONSOLE_MARKER = "_esfex_console_handler"

# Loggers whose INFO records are always considered milestones (so they
# survive the ``basic`` filter). Add new module names here as the
# codebase grows ones that genuinely speak for "overall progress".
_MILESTONE_LOGGERS = frozenset({
    "esfex.runner",
    "esfex.cli",
})

_FMT = logging.Formatter(
    "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


class _BasicFilter(logging.Filter):
    """Filter used in ``basic`` console mode — only optimization progress.

    Lets through:
      * ERROR records (a fatal failure must always be visible).
      * INFO records explicitly tagged with ``extra={"milestone": True}``.
      * INFO records emitted by a logger in ``_MILESTONE_LOGGERS``.

    Explicitly blocks WARNING — basic mode is "show me only the run
    progress; I'll re-run with verbose if I want diagnostics". The
    file handler is independent and still records every level, so
    nothing is lost — just hidden from the live console.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # ERROR and above always shown.
        if record.levelno >= logging.ERROR:
            return True
        # WARNING is deliberately swallowed in basic mode.
        if record.levelno >= logging.WARNING:
            return False
        if getattr(record, "milestone", False):
            return True
        if record.name in _MILESTONE_LOGGERS:
            return True
        # Also pass through child loggers like esfex.runner.foo
        parent = record.name.rsplit(".", 1)[0]
        if parent in _MILESTONE_LOGGERS:
            return True
        return False


def _apply_warnings_policy(level: str) -> None:
    """Make Python ``warnings`` honour the console verbosity.

    Without this, ``warnings.warn`` calls inside numpy/pandas/etc.
    leak straight to stderr and bypass our logging filter entirely —
    the user opted into ``basic`` but still saw a wall of FutureWarning,
    DeprecationWarning, RuntimeWarning, etc.

    Behaviour:
      * ``basic``  → suppress all warnings completely (filterwarnings
        ignore), AND route any stragglers through logging so the
        basic filter catches them too.
      * ``verbose`` / ``debug`` → restore the default warning policy
        and stop hijacking warnings into the log; libraries print
        warnings the normal way.

    Idempotent — safe to call any time the console mode is reconfigured.
    """
    import warnings
    if level == "basic":
        warnings.filterwarnings("ignore")
        logging.captureWarnings(True)
    else:
        warnings.resetwarnings()
        logging.captureWarnings(False)


def _normalize_level(level: str) -> str:
    norm = (level or "basic").strip().lower()
    if norm not in {"basic", "verbose", "debug"}:
        # Unknown values fall back to basic — better quiet than noisy.
        norm = "basic"
    return norm


def setup_console_logging(
    level: str = "basic",
    *,
    stream=None,
    force: bool = False,
) -> logging.Handler:
    """Idempotent console handler setup. Returns the handler.

    Calling this a second time updates the existing handler in place;
    it does NOT add a second one.

    ``force=True`` means "this caller's level is authoritative — apply
    it even if a previous call already set the handler". The CLI
    front-end passes ``force=True`` when ``--verbose`` is used so the
    later runner-internal call doesn't silently downgrade it back to
    whatever the yaml says.

    ``force=False`` (default) means "set the level only if no caller
    has claimed it yet". That lets runner._setup_logging supply the
    yaml-driven default without overriding an explicit CLI flag.
    """
    norm = _normalize_level(level)
    root = logging.getLogger()

    handler: Optional[logging.Handler] = None
    for h in root.handlers:
        if getattr(h, _CONSOLE_MARKER, False):
            handler = h
            break

    first_install = handler is None
    if handler is None:
        handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
        handler.setFormatter(_FMT)
        setattr(handler, _CONSOLE_MARKER, True)
        setattr(handler, "_esfex_level_locked", False)
        root.addHandler(handler)

    locked = getattr(handler, "_esfex_level_locked", False)
    if first_install or force or not locked:
        # Reset filters every time so a "verbose" after a "basic" call
        # actually drops the milestone filter.
        handler.filters.clear()
        if norm == "basic":
            handler.setLevel(logging.INFO)
            handler.addFilter(_BasicFilter())
        elif norm == "verbose":
            handler.setLevel(logging.INFO)
        else:  # debug
            handler.setLevel(logging.DEBUG)
        # Warnings policy lives next to the level decision so the
        # console never disagrees with the warnings module about how
        # noisy the user wants things to be.
        _apply_warnings_policy(norm)
        if force:
            setattr(handler, "_esfex_level_locked", True)

    # Root must let records through to the (possibly DEBUG) file
    # handler. The console handler's own level + filter decide what
    # the user actually sees.
    root.setLevel(logging.DEBUG)

    return handler


def setup_file_logging(log_file) -> logging.Handler:
    """Idempotent DEBUG file handler attached to root.

    Tags the handler with the target path so a second call for the
    same file is a no-op (a different file installs a second handler).
    """
    target_marker = f"_esfex_file_handler:{log_file}"
    root = logging.getLogger()
    for h in root.handlers:
        if getattr(h, target_marker, False):
            return h
    handler = logging.FileHandler(str(log_file))
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(_FMT)
    setattr(handler, target_marker, True)
    root.addHandler(handler)
    return handler


def log_milestone(logger: logging.Logger, message: str, *args, **kwargs) -> None:
    """Helper: log an INFO message tagged as a milestone.

    Equivalent to ``logger.info(message, *args, extra={"milestone": True}, **kw)``.
    Useful for milestone records emitted by loggers NOT in
    ``_MILESTONE_LOGGERS`` (e.g. plugin code that wants its INFO line
    to survive ``basic`` mode without renaming its logger).
    """
    extra = kwargs.pop("extra", {}) or {}
    extra.setdefault("milestone", True)
    logger.info(message, *args, extra=extra, **kwargs)

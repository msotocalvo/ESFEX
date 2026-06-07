"""Shared helpers for wizard close / cleanup behaviour.

Most wizards in this directory inherit from QDialog directly and used
to vary in how they handle close (pressing Esc, clicking the X, or
explicit Cancel):

* 8 of 10 wizards had no ``closeEvent`` at all, so background workers
  spawned by their steps (network fetchers, computation threads) kept
  running after the dialog disappeared.
* The map widget would also keep its draw mode active when a wizard
  using polygon drawing was closed without finishing.

``cleanup_wizard`` centralises the cleanup that the two well-behaved
wizards (``demand_estimation_wizard``, ``ev_wizard``) already did
manually. Each wizard's ``closeEvent`` should call it before delegating
to ``super().closeEvent(event)``.
"""

from __future__ import annotations

import logging


def stop_thread(thread, wait_ms: int = 4000) -> None:
    """Stop a ``QThread`` and block (bounded) until it has finished.

    Destroying a ``QThread`` whose ``run()`` is still executing is
    undefined behaviour in Qt — it prints
    ``QThread: Destroyed while thread is still running`` and typically
    aborts the whole process. Worker-owning widgets must therefore stop
    their threads *and wait* before those threads can be garbage
    collected (on dialog close, or when a new run replaces the old one).

    Stop order:

    1. Cooperative cancel — call ``cancel()`` / ``requestInterruption()``
       and set a ``_cancelled`` flag if the worker exposes one. Workers
       that poll these exit on their own, which is the clean path.
    2. ``quit()`` the thread's event loop (a no-op for the ``run()``
       override workers here, but harmless and correct for event-loop
       threads).
    3. ``wait(wait_ms)``. If the worker is blocked in a long native call
       (a DuckDB query, a big K-means) it may ignore the cancel flag; on
       timeout we ``terminate()`` as a last resort. Terminating is unsafe
       in general, but it is strictly better than letting a running
       thread be destroyed — and it only happens on teardown.

    Defensive throughout: a thread that is ``None``, already finished, or
    whose underlying C++ object is gone is a silent no-op.
    """
    log = logging.getLogger(__name__)
    if thread is None:
        return
    try:
        if not thread.isRunning():
            return
    except RuntimeError:
        # Underlying C++ QThread already deleted — nothing to stop.
        return

    for attr in ("cancel", "requestInterruption"):
        fn = getattr(thread, attr, None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass
    try:
        thread._cancelled = True
    except Exception:
        pass
    try:
        thread.quit()
    except Exception:
        pass
    try:
        if not thread.wait(wait_ms):
            log.warning(
                "Worker %r ignored cancellation for %dms; terminating",
                thread, wait_ms,
            )
            thread.terminate()
            thread.wait(1000)
    except RuntimeError:
        # C++ object vanished mid-wait; treat as stopped.
        pass


def cleanup_wizard(wizard) -> None:
    """Cancel running workers and reset the shared map widget.

    Looks at two attributes commonly present on wizard instances:

    * ``self._steps`` (list) — each step may expose a ``cancel_all``
      method; we call it if present so worker threads stop.
    * ``self._map_widget`` (MapWidget | None) — if the wizard was
      using polygon-draw mode, we turn it off and clear the polygon.

    Both lookups are defensive: a wizard that doesn't use one of these
    will just skip that branch. Exceptions raised by individual cleanup
    calls are logged at debug level and swallowed — a wizard shutting
    down must never raise from closeEvent, since that prevents Qt from
    actually closing the dialog.
    """
    log = logging.getLogger(__name__)

    steps = getattr(wizard, "_steps", None) or []
    for step in steps:
        cancel = getattr(step, "cancel_all", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:
                log.debug(
                    "cancel_all() raised on step %r during wizard close",
                    step, exc_info=True,
                )

    mw = getattr(wizard, "_map_widget", None)
    if mw is not None:
        for method_name in (
            "disable_domain_polygon_draw",
            "clear_domain_polygon",
        ):
            fn = getattr(mw, method_name, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    log.debug(
                        "%s() raised on map widget during wizard close",
                        method_name, exc_info=True,
                    )

    # Many existing wizards already have a wizard-specific cleanup
    # method that hooks into their own draw modes / overlays. We invoke
    # it after the generic cleanup so the wizard's own knowledge wins
    # for state that the helper doesn't know about.
    for hook_name in ("_cleanup_map", "_cleanup"):
        hook = getattr(wizard, hook_name, None)
        if callable(hook):
            try:
                hook()
            except Exception:
                log.debug(
                    "%s() raised during wizard close", hook_name,
                    exc_info=True,
                )

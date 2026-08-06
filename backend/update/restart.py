"""Exiting with a code the launcher understands.

The launcher runs the server as a child process and reads its exit code:
`RESTART_EXIT_CODE` means "apply anything staged and start me again",
anything else means the user is finished. Using the exit code rather than,
say, a sentinel file keeps the decision in one place and works identically
on Windows and Linux.

Shutdown is done through a registered hook rather than by importing app.py,
which would be circular. app.py registers one during startup; if none is
registered (tests, or a bare `uvicorn app:app` with no launcher) the exit
still happens, it just skips hardware cleanup that has nothing to clean.
"""
from __future__ import annotations

import logging
import os
import threading

log = logging.getLogger("autodarts.update")

RESTART_EXIT_CODE = 42
SHUTDOWN_EXIT_CODE = 0

_shutdown_hook = None


def set_shutdown_hook(callback) -> None:
    global _shutdown_hook
    _shutdown_hook = callback


def _exit_after_response(code: int, delay: float) -> None:
    """Give the HTTP response time to reach the browser, then go.

    `os._exit` is deliberate. A normal exit waits on uvicorn's graceful
    shutdown, which waits on the open WebSocket connections every UI holds -
    so the process would linger exactly when the user has asked it to
    restart. The hook below has already released the cameras, the serial
    port and the LED thread, which is the cleanup that actually matters;
    what remains is sockets the OS reclaims anyway.
    """
    import time

    time.sleep(delay)
    if _shutdown_hook is not None:
        try:
            _shutdown_hook()
        except Exception:  # noqa: BLE001 - never block the restart on cleanup
            log.exception("shutdown hook failed during restart")
    os._exit(code)


def request_restart(delay: float = 1.0) -> None:
    log.info("restart requested - exiting with code %s", RESTART_EXIT_CODE)
    threading.Thread(
        target=_exit_after_response,
        args=(RESTART_EXIT_CODE, delay),
        name="update-restart",
        daemon=True,
    ).start()

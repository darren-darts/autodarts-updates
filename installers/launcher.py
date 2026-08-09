"""Autodarts launcher: apply any staged update, then run the app.

Installed at the root of the install tree, *outside* `app/`. That placement
is the whole point rather than an accident:

* **The code performing the swap must not live in the directory being
  swapped.** Importing a module and then replacing the file it came from is
  a good way to produce behaviour nobody can reproduce.
* **Windows will not let you replace files that are open.** Python source
  files are read and closed at import, but the app also holds camera
  handles, a serial port and a listening socket. Doing the swap before the
  app starts sidesteps file locking entirely instead of racing it.

So the sequence is: swap, then start. Never both at once.

This file is part of the install, not the update payload - it changes rarely
and is refreshed by re-running the installer. A release that requires a
newer launcher declares it via `min_runtime` in its manifest, which the
in-app updater checks *before* downloading, so an install can never end up
with an app its launcher cannot start.

Stdlib only, by rule. It has to work when `app/` is mid-swap or broken.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / "app"
PREVIOUS = ROOT / "app.previous"
INCOMING = ROOT / "app.incoming"
STAGING = ROOT / "staging"
CONFIG = ROOT / "config"
LOGS = ROOT / "logs"
PENDING = STAGING / "pending.json"
RESULT = ROOT / "last_update.json"

HOST = os.environ.get("AUTODARTS_HOST", "0.0.0.0")
PORT = os.environ.get("AUTODARTS_PORT", "8000")

# A freshly applied update that dies within this many seconds is treated as
# broken and rolled back. Long enough for uvicorn to bind a port and import
# OpenCV on a Pi; short enough that a genuine crash much later (hours into a
# game) is correctly left alone rather than blamed on the update.
CRASH_WINDOW_SECONDS = 25

# The app exits with this code to mean "apply what is staged and start me
# again". Must match update/restart.py.
RESTART_EXIT_CODE = 42
MAX_CONSECUTIVE_RESTARTS = 3


def log(message: str) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {message}"
    print(line, flush=True)
    try:
        with open(LOGS / "launcher.log", "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def record(status: str, version: str = "", detail: str = "") -> None:
    """Leave a breadcrumb the app's Updates page reads back on next start."""
    try:
        RESULT.write_text(
            json.dumps(
                {"status": status, "version": version, "detail": detail, "at": time.time()},
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def python_executable() -> str:
    """The bundled runtime if there is one, else whatever is running us."""
    for candidate in (
        ROOT / "runtime" / "python.exe",
        ROOT / "runtime" / "bin" / "python3",
        ROOT / "runtime" / "Scripts" / "python.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def read_pending() -> dict | None:
    try:
        data = json.loads(PENDING.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("version") else None


MAX_APPLY_ATTEMPTS = 3


def swap(source: Path, message: str) -> str:
    """Replace app/ with `source`, keeping the outgoing copy for rollback.

    Ordering matters. The old app is moved aside before the new one is moved
    in, so at no point is there no app at all *and* nothing to restore from:
    if the second move fails, `app.previous` is still intact and gets put
    straight back.
    """
    log(message)
    if PREVIOUS.exists():
        shutil.rmtree(PREVIOUS, ignore_errors=True)
    try:
        if APP.exists():
            rename_with_retry(APP, PREVIOUS)
        try:
            rename_with_retry(source, APP)
        except OSError:
            if PREVIOUS.exists() and not APP.exists():
                PREVIOUS.rename(APP)
            raise
    except OSError as exc:
        log(f"  swap failed: {exc}")
        return str(exc)
    return ""


def apply_pending() -> str:
    """Apply a staged update if one is waiting. Returns the version applied, or ""."""
    pending = read_pending()
    if not pending:
        return ""

    version = str(pending.get("version", "?"))
    staged = Path(pending.get("staged") or (STAGING / version))

    if not (staged / "VERSION.json").is_file():
        log(f"Staged update {version} is incomplete - discarding it.")
        shutil.rmtree(staged, ignore_errors=True)
        PENDING.unlink(missing_ok=True)
        record("failed", version, "The downloaded update was incomplete.")
        return ""

    # Move the staged tree next to app/ first. A rename within the same
    # directory is atomic on both Windows and Linux, whereas moving across
    # filesystems is a copy that can fail halfway - and staging/ may well be
    # on a different volume if someone redirected it.
    if INCOMING.exists():
        shutil.rmtree(INCOMING, ignore_errors=True)
    try:
        shutil.move(str(staged), str(INCOMING))
    except OSError as exc:
        log(f"Could not stage update {version}: {exc}")
        record("failed", version, str(exc))
        PENDING.unlink(missing_ok=True)
        return ""

    error = swap(INCOMING, f"Applying update {version}...")
    if error:
        # Most causes are transient - a virus scanner mid-scan, or a server
        # that has not finished releasing its handles. Throwing away a
        # correctly downloaded update over that would be a poor trade, so the
        # staged tree is put back and retried on the next start. A genuinely
        # stuck install (permissions, a stray process) gives up after a few
        # tries rather than retrying forever.
        attempts = int(pending.get("attempts", 0)) + 1
        try:
            shutil.move(str(INCOMING), str(staged))
        except OSError:
            shutil.rmtree(INCOMING, ignore_errors=True)
            attempts = MAX_APPLY_ATTEMPTS

        if attempts >= MAX_APPLY_ATTEMPTS:
            shutil.rmtree(staged, ignore_errors=True)
            PENDING.unlink(missing_ok=True)
            record("failed", version, f"Could not replace the application folder: {error}")
            log(f"Giving up on update {version} after {attempts} attempts.")
        else:
            pending["attempts"] = attempts
            PENDING.write_text(json.dumps(pending, indent=2), encoding="utf-8")
            record("failed", version, f"Will retry on the next start: {error}")
            log(f"Update {version} will be retried on the next start ({attempts}/{MAX_APPLY_ATTEMPTS}).")
        return ""

    PENDING.unlink(missing_ok=True)
    log(f"Update {version} applied.")
    record("applied", version)
    return version


def rollback(version: str) -> None:
    if not PREVIOUS.exists():
        log("Nothing to roll back to.")
        record("failed", version, "The update would not start and there was no previous version to restore.")
        return
    log(f"Update {version} would not start - rolling back.")
    if INCOMING.exists():
        shutil.rmtree(INCOMING, ignore_errors=True)
    try:
        if APP.exists():
            rename_with_retry(APP, INCOMING)
        rename_with_retry(PREVIOUS, APP)
        shutil.rmtree(INCOMING, ignore_errors=True)
        record("rolled_back", version, "The update did not start, so the previous version was restored.")
        log("Rolled back to the previous version.")
    except OSError as exc:
        log(f"Rollback failed: {exc}")
        record("failed", version, f"Rollback failed: {exc}")


def environment() -> dict:
    env = dict(os.environ)
    env["AUTODARTS_ROOT"] = str(ROOT)
    env["AUTODARTS_CONFIG_DIR"] = str(CONFIG)
    env["AUTODARTS_STAGING_DIR"] = str(STAGING)
    env["PYTHONUNBUFFERED"] = "1"

    # The app imports its own modules top-level (`import settings_store`), so
    # app/backend has to be importable. It is put on PYTHONPATH rather than
    # made the working directory *deliberately*: Windows refuses to rename a
    # directory that is any running process's current directory, so a server
    # started inside app/ makes app/ un-swappable and every update fails with
    # a bare "Access is denied". Found exactly that way, on a real install.
    backend = str(APP / "backend")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{backend}{os.pathsep}{existing}" if existing else backend
    return env


# Started via -c rather than `-m uvicorn app:app`, because the install root
# contains a directory literally called `app`. With the root as the working
# directory, `app:app` resolved to that directory as a namespace package and
# uvicorn failed with 'Attribute "app" not found in module "app"'. Inserting
# app/backend at the *front* of sys.path makes app.py win unambiguously,
# regardless of what the working directory happens to contain.
BOOTSTRAP = (
    "import sys, uvicorn; "
    "sys.path.insert(0, sys.argv[1]); "
    "uvicorn.run('app:app', host=sys.argv[2], port=int(sys.argv[3]))"
)


def start_server() -> subprocess.Popen:
    command = [
        python_executable(), "-c", BOOTSTRAP,
        str(APP / "backend"), HOST, str(PORT),
    ]
    log(f"Starting the server on {HOST}:{PORT}")
    # cwd is the install root, never inside app/ - see environment() above.
    return subprocess.Popen(command, cwd=str(ROOT), env=environment())


def tie_children_to_this_process() -> None:
    """Windows: make the server die whenever this launcher does.

    `TerminateProcess` - which is what Task Manager's End Task and
    `Popen.terminate()` both do - runs no handlers, so the launcher gets no
    chance to clean up and the uvicorn child is orphaned. An orphaned server
    keeps the cameras, the serial port and port 8000, and holds `app/` open
    so the *next* update fails with "Access is denied".

    A Job Object with KILL_ON_JOB_CLOSE is the OS-level answer: children
    inherit the job, and when the last handle to it closes - including
    because the launcher was killed outright - Windows terminates everything
    in it. Best-effort; if any of it fails the launcher still works exactly
    as before, just without the safety net.
    """
    if os.name != "nt":
        return  # systemd kills the whole control group; nothing to do
    try:
        import ctypes
        from ctypes import wintypes

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
        JobObjectExtendedLimitInformation = 9

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        # Declaring these matters: ctypes defaults a return value to C int,
        # which truncates a 64-bit HANDLE on 64-bit Windows. The calls then
        # appear to succeed while operating on a mangled handle - which is
        # exactly how this failed the first time, silently.
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            log(f"(CreateJobObject failed: {ctypes.get_last_error()})")
            return

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
        ):
            log(f"(SetInformationJobObject failed: {ctypes.get_last_error()})")
            return
        if not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
            log(f"(AssignProcessToJobObject failed: {ctypes.get_last_error()})")
            return

        # Deliberately leaked: the job must outlive this function, and closing
        # the handle is precisely what kills the processes in it.
        global _JOB_HANDLE
        _JOB_HANDLE = job
    except Exception as exc:  # noqa: BLE001 - never let this stop the app starting
        log(f"(could not set up process cleanup: {exc})")


_JOB_HANDLE = None


def stop_child(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    log("Stopping the server...")
    process.terminate()
    try:
        process.wait(10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(5)


def rename_with_retry(source: Path, destination: Path, attempts: int = 12) -> None:
    """Rename, tolerating Windows briefly holding handles after process exit.

    Even with nothing living inside app/, a just-exited process, an on-access
    virus scanner or the search indexer can hold a handle for a moment. The
    operation is retried rather than treated as fatal, because failing here
    means an update that downloaded correctly is thrown away.
    """
    last: OSError | None = None
    for attempt in range(attempts):
        try:
            source.rename(destination)
            return
        except OSError as exc:
            last = exc
            time.sleep(0.25 * (attempt + 1))
    raise last if last else OSError("rename failed")


def main() -> int:
    for directory in (CONFIG, STAGING, LOGS):
        directory.mkdir(parents=True, exist_ok=True)

    tie_children_to_this_process()

    first_run = True
    # A guard against a restart loop: if the app asked to restart and came
    # straight back asking again, something is wrong and spinning forever
    # would be worse than stopping with a message in the log.
    consecutive_restarts = 0

    while True:
        applied = apply_pending()

        if not (APP / "backend" / "app.py").is_file():
            # Only reachable if a swap left the tree broken; the rollback
            # path normally prevents it, but an app/ that cannot start is
            # worth one last recovery attempt before giving up.
            if PREVIOUS.exists():
                log("app/ is missing or damaged - restoring the previous version.")
                shutil.rmtree(APP, ignore_errors=True)
                PREVIOUS.rename(APP)
            else:
                log("app/ is missing and cannot be recovered. Please reinstall Autodarts.")
                return 1

        started = time.time()
        process = start_server()

        if first_run and os.environ.get("AUTODARTS_NO_BROWSER") != "1":
            time.sleep(2.5)
            if process.poll() is None:
                webbrowser.open(f"http://localhost:{PORT}/")
        first_run = False

        try:
            code = process.wait()
        except KeyboardInterrupt:
            # An orphaned server keeps app/ locked as its handles stay open,
            # and the next update then fails with "Access is denied" - which
            # is exactly how this was found. The launcher must never leave a
            # child behind, however it is itself stopped.
            stop_child(process)
            return 0
        elapsed = time.time() - started

        if applied and code != 0 and code != RESTART_EXIT_CODE and elapsed < CRASH_WINDOW_SECONDS:
            rollback(applied)
            log("Restarting on the restored version.")
            continue

        if code == RESTART_EXIT_CODE:
            consecutive_restarts += 1
            if consecutive_restarts > MAX_CONSECUTIVE_RESTARTS:
                log("Too many restarts in a row - stopping. See the log above.")
                return 1
            log("Restart requested.")
            continue

        return code


if __name__ == "__main__":
    sys.exit(main())

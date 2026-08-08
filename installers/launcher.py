"""ShepDarts launcher: apply any staged update, then run the app.

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
and is refreshed by re-running the installer.

A release that needs a dependency this runtime does not have declares it via
`min_runtime` in its manifest. What happens with that differs by platform,
and both halves matter for updates to actually reach everyone:

* **Windows**: the bundled interpreter has pip stripped out after it is
  built (~25MB saved - see tools/build_runtime.py), so it cannot gain a
  dependency at all. `update/client.py` checks `min_runtime` *before*
  downloading and refuses outright, pointing at a new installer instead.
* **Pi**: the runtime is a venv built from the OS's own python3, so pip is
  right there. `update/client.py` does NOT gate this case - it is handled
  here instead, in `sync_runtime_dependencies`, right after a successful
  swap. A dependency install that fails is rolled back exactly like a failed
  file swap, so this can never leave a half-updated app running.

Stdlib only, by rule. It has to work when `app/` is mid-swap or broken.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

IS_WINDOWS = os.name == "nt"

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
RUNTIME_INFO = ROOT / "runtime" / "RUNTIME.json"
PIP_INSTALL_TIMEOUT_SECONDS = 600  # generous: opencv/numpy are slow, but rare

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


def unswap() -> None:
    """Undo a successful swap() - put PREVIOUS back as APP.

    Used when the swap itself worked but something the new app/ needs then
    failed - see sync_runtime_dependencies. That has to be treated exactly
    like a swap failure: retried, and given up on after MAX_APPLY_ATTEMPTS,
    never left running a half-updated app. Leaving the rejected app parked at
    INCOMING (rather than deleting it) matters because apply_pending's retry
    path expects exactly that name when it re-stages a failed attempt.
    """
    if not PREVIOUS.exists():
        return  # nothing to restore - leave APP as it is
    if APP.exists():
        rename_with_retry(APP, INCOMING)
    rename_with_retry(PREVIOUS, APP)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sync_runtime_dependencies(pending: dict) -> str:
    """POSIX only: pip-install into runtime/ if requirements.txt changed.

    Returns "" on success (including "nothing needed doing"), or an error
    message that apply_pending treats the same way it treats a failed swap.

    Compares the file ON DISK after the swap against what RUNTIME.json last
    recorded, rather than trusting `pending["requirements_sha256"]` at face
    value - self-healing if that marker is ever missing or stale, where
    trusting metadata would stay wrong until something else fixed it by hand.
    Most updates touch no dependency at all, so the common case is one hash
    comparison and nothing else - not a pip invocation every time.
    """
    requirements = APP / "backend" / "requirements.txt"
    if not requirements.is_file():
        return ""  # nothing declared - nothing to install

    pip = ROOT / "runtime" / "bin" / "pip"
    if not pip.is_file():
        return ""  # no managed venv here (e.g. a hand-run checkout) - not ours to touch

    current_hash = _sha256_file(requirements)
    try:
        info = json.loads(RUNTIME_INFO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        info = {}
    if info.get("requirements_sha256") == current_hash:
        return ""  # already satisfied

    log(f"Dependencies changed - installing into {pip.parent.parent}...")
    try:
        result = subprocess.run(
            [
                str(pip), "install", "--upgrade", "--quiet", "--no-input",
                "--disable-pip-version-check", "-r", str(requirements),
            ],
            capture_output=True, text=True, timeout=PIP_INSTALL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"pip install did not finish within {PIP_INSTALL_TIMEOUT_SECONDS}s"
    if result.returncode != 0:
        # The last non-blank line is normally pip's actual complaint ("No
        # matching distribution found for ...") - the lines above it are
        # usually a resolver trace nobody needs to see in a status message.
        lines = [line for line in (result.stderr or result.stdout).splitlines() if line.strip()]
        return f"pip install failed: {lines[-1] if lines else 'unknown error'}"

    info["requirements_sha256"] = current_hash
    info.setdefault("platform", "posix")
    # A dependency sync satisfies whatever min_runtime asked for it, so this
    # is what lets a future _check_runtime (if that gate is ever extended to
    # POSIX) see the install as caught up rather than stuck at whatever
    # install-pi.sh happened to write once, forever.
    info["version"] = pending.get("min_runtime") or info.get("version", "0.0.0")
    try:
        RUNTIME_INFO.write_text(json.dumps(info, indent=2), encoding="utf-8")
    except OSError as exc:
        return f"installed dependencies but could not record it: {exc}"
    log("Dependencies installed.")
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
    if not error and not IS_WINDOWS:
        error = sync_runtime_dependencies(pending)
        if error:
            log(f"  dependency install failed: {error}")
            unswap()  # leaves the rejected app parked at INCOMING, as swap's own failure does

    if error:
        # Most causes are transient - a virus scanner mid-scan, a server that
        # has not finished releasing its handles, or (Pi only, since the
        # check above) a dependency download that dropped mid-transfer.
        # Throwing away a correctly downloaded update over that would be a
        # poor trade, so the staged tree is put back and retried on the next
        # start. A genuinely stuck install (permissions, a stray process, a
        # dependency that will never install) gives up after a few tries
        # rather than retrying forever.
        attempts = int(pending.get("attempts", 0)) + 1
        try:
            shutil.move(str(INCOMING), str(staged))
        except OSError:
            shutil.rmtree(INCOMING, ignore_errors=True)
            attempts = MAX_APPLY_ATTEMPTS

        if attempts >= MAX_APPLY_ATTEMPTS:
            shutil.rmtree(staged, ignore_errors=True)
            PENDING.unlink(missing_ok=True)
            record("failed", version, f"Could not finish applying the update: {error}")
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


# The main screen should look like an appliance, not a web page. A phone
# starting a game can put the play view into its fullscreen layout, but it
# cannot call the browser's Fullscreen API on another machine - that needs a
# click on the machine itself - so the address bar would survive. Opening the
# display fullscreen in the first place is what removes it for good.
#
# Only Chromium-family browsers take a flag for this; anything else falls back
# to a normal window, where the app's own fullscreen button still works. Set
# AUTODARTS_BROWSER_WINDOWED=1 for a plain window (handy while developing).
CHROMIUM_ON_WINDOWS = (
    r"Google\Chrome\Application\chrome.exe",
    r"Microsoft\Edge\Application\msedge.exe",
)
CHROMIUM_ON_LINUX = ("chromium-browser", "chromium", "google-chrome", "google-chrome-stable")

CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def fullscreen_browser() -> str:
    """Path to a browser that can be told to start fullscreen, or ""."""
    if os.environ.get("AUTODARTS_BROWSER_WINDOWED") == "1":
        return ""
    if os.name == "nt":
        roots = ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")
        for root in filter(None, (os.environ.get(name, "") for name in roots)):
            for relative in CHROMIUM_ON_WINDOWS:
                candidate = Path(root) / relative
                if candidate.is_file():
                    return str(candidate)
        return ""
    for name in CHROMIUM_ON_LINUX:
        found = shutil.which(name)
        if found:
            return found
    return ""


def open_display(url: str) -> None:
    browser = fullscreen_browser()
    if not browser:
        webbrowser.open(url)
        return
    try:
        # Broken out of the job object deliberately. That job exists so the
        # *server* can never outlive this launcher; the display is the user's
        # own window and closing the app should not shoot it down mid-throw.
        # If the flag is refused the fallback below still opens the page.
        subprocess.Popen(
            [browser, "--start-fullscreen", url],
            creationflags=CREATE_BREAKAWAY_FROM_JOB if os.name == "nt" else 0,
        )
    except OSError as exc:
        log(f"(could not start the browser fullscreen: {exc})")
        webbrowser.open(url)


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

        JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x0800
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
        # BREAKAWAY_OK is what lets the browser be launched outside the job -
        # see open_display(). Everything that does not ask to break away, the
        # server included, still dies with this launcher.
        info.BasicLimitInformation.LimitFlags = (
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK
        )
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
                log("app/ is missing and cannot be recovered. Please reinstall ShepDarts.")
                return 1

        started = time.time()
        process = start_server()

        if first_run and os.environ.get("AUTODARTS_NO_BROWSER") != "1":
            time.sleep(2.5)
            if process.poll() is None:
                open_display(f"http://localhost:{PORT}/")
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

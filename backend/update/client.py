"""Check, download and stage an update. Nothing here modifies the running app.

The staged tree is built into `staging/<version>/` and only becomes live when
the launcher swaps it in at the next start (see apply.py). Until then every
step is reversible by deleting one directory.

Three properties worth stating up front, because they are what make this
cheap enough to run on a home PC over a domestic connection:

* **Unchanged files are never downloaded.** A file whose hash matches the
  one already installed is copied from the current app directory. In a
  typical bug-fix release that is nearly everything, so the transfer is the
  two or three files that actually changed.
* **Interrupted downloads resume.** Staged files are verified by hash on the
  way back in, so a second attempt skips whatever the first one completed.
* **Nothing is trusted until it is verified.** The channel pointer and the
  manifest are both signature-checked, the manifest is additionally pinned
  by hash from the pointer, and every file is hash-checked after download.
  A failure at any point aborts and leaves the installed app untouched.
"""
from __future__ import annotations

import gzip
import json
import logging
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

import paths

from . import manifest as manifest_mod
from . import origin, signing, version as version_mod

log = logging.getLogger("autodarts.update")

PENDING_NAME = "pending.json"
HTTP_TIMEOUT = httpx.Timeout(15.0, read=60.0)
USER_AGENT = "autodarts-updater/1"

# States surfaced to the UI.
IDLE = "idle"
CHECKING = "checking"
UP_TO_DATE = "up_to_date"
AVAILABLE = "available"
DOWNLOADING = "downloading"
READY = "ready"
ERROR = "error"
DISABLED = "disabled"


class UpdateError(Exception):
    """Anything that stops an update, phrased for a non-technical user."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def base_url() -> str:
    """Update origin, with a settings override used only for local testing."""
    try:
        import settings_store

        configured = settings_store.load().get(origin.SETTINGS_KEY, {}).get("base_url")
    except Exception:
        configured = None
    url = (configured or origin.UPDATE_BASE_URL or "").strip()
    return url.rstrip("/")


GZIP_MAGIC = b"\x1f\x8b"


def _decompress(blob: bytes, path: str) -> bytes:
    """Return the file's real bytes, whether or not the host expanded it.

    Blobs are stored gzipped with a `.gz` key. Most static hosts serve that
    verbatim, but some (and some CDNs) recognise the extension and send
    `Content-Encoding: gzip`, at which point httpx transparently decompresses
    and this function receives the *expanded* bytes. Both are correct
    behaviour by the host; the client just has to cope with either, or the
    same release works on one host and fails on another.

    Sniffing the gzip magic number is safe rather than a guess, because the
    caller verifies the SHA-256 of whatever comes back against the manifest -
    a wrong branch here cannot pass that check.
    """
    if not blob.startswith(GZIP_MAGIC):
        return blob
    try:
        return gzip.decompress(blob)
    except (OSError, EOFError) as exc:
        raise UpdateError(f"Downloaded data for {path} was corrupt.") from exc


def _auto_check() -> bool:
    try:
        import settings_store

        return bool(settings_store.load().get(origin.SETTINGS_KEY, {}).get("auto_check", True))
    except Exception:
        return True


def runtime_version() -> version_mod.Version | None:
    """The bundled runtime's version, or None in a source checkout.

    None means "cannot tell", and the min_runtime gate is skipped - a
    checkout manages its own venv and is blocked from updating anyway.
    """
    path = paths.install_root() / "runtime" / "RUNTIME.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return version_mod.Version.parse_or_zero(str(data.get("version", "0.0.0")))


@dataclass
class Available:
    """A verified, newer release that has not been downloaded yet."""

    manifest: manifest_mod.Manifest
    missing: tuple[manifest_mod.FileEntry, ...] = field(default_factory=tuple)

    @property
    def version(self) -> str:
        return self.manifest.version

    @property
    def download_bytes(self) -> int:
        """What will actually cross the network - not the payload size."""
        return sum(entry.csize or entry.size for entry in self.missing)

    def to_dict(self) -> dict:
        return {
            "version": self.manifest.version,
            "channel": self.manifest.channel,
            "released": self.manifest.released,
            "notes": self.manifest.notes,
            "file_count": len(self.manifest.files),
            "changed_files": len(self.missing),
            "download_bytes": self.download_bytes,
            "total_bytes": self.manifest.total_size,
        }


class Updater:
    """Owns update state for the process. One instance, created below.

    Every mutation happens under `_lock`; the download runs on its own
    thread so an HTTP stall can never block the event loop, and progress is
    published through a callback the routes layer turns into a WebSocket
    broadcast.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state = IDLE
        self._message = ""
        self._available: Available | None = None
        self._progress = 0.0
        self._downloaded = 0
        self._download_total = 0
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._last_check = 0.0
        self._on_change = None

    # ---------------------------------------------------------------- state

    def set_listener(self, callback) -> None:
        """Called (with no arguments) whenever status changes."""
        self._on_change = callback

    def _notify(self) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change()
        except Exception:  # noqa: BLE001 - a UI notification must never break an update
            log.exception("update status listener failed")

    def _set(self, state: str, message: str = "") -> None:
        with self._lock:
            self._state = state
            self._message = message
        self._notify()

    def status(self) -> dict:
        with self._lock:
            current, channel = version_mod.read()
            pending = read_pending()
            return {
                "current_version": current,
                "channel": version_mod.current_channel() or channel,
                "auto_check": _auto_check(),
                "state": self._state,
                "message": self._message,
                "progress": round(self._progress, 4),
                "downloaded_bytes": self._downloaded,
                "download_total_bytes": self._download_total,
                "available": self._available.to_dict() if self._available else None,
                "pending": pending,
                "last_check": self._last_check or None,
                "can_update": can_update(),
                "reason": update_blocked_reason(),
            }

    # ---------------------------------------------------------------- fetch

    def _get(self, client: httpx.Client, path: str, cache_bust: bool = False) -> bytes:
        url = f"{base_url()}/{path.lstrip('/')}"
        if cache_bust:
            # The channel pointer is the one object whose job is to change,
            # and it sits behind a CDN we do not control - GitHub Pages sends
            # Cache-Control: max-age=600, and a CDN ignores a request's
            # no-cache header for publicly cacheable responses. A unique
            # query string is the only reliable way to force a fresh copy,
            # because it forms part of the cache key. Blobs and manifests are
            # immutable by construction, so they are left cacheable.
            url = f"{url}?_={int(time.time())}"
        try:
            response = client.get(url)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise UpdateError(
                f"Update server returned {exc.response.status_code} for {path}",
                status=exc.response.status_code,
            ) from exc
        except httpx.HTTPError as exc:
            raise UpdateError(f"Could not reach the update server: {exc}") from exc
        return response.content

    def _fetch_signed(
        self, client: httpx.Client, path: str, cache_bust: bool = False
    ) -> tuple[bytes, dict]:
        """Fetch `path` and `path.sig`, verify, return (raw bytes, parsed)."""
        raw = self._get(client, path, cache_bust)
        signature = self._get(client, f"{path}.sig", cache_bust).decode("ascii", "replace").strip()
        try:
            signing.verify(raw, signature)
        except signing.SignatureError as exc:
            raise UpdateError(
                f"The downloaded {path} is not correctly signed and was rejected ({exc})."
            ) from exc
        try:
            return raw, json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise UpdateError(f"{path} is not valid JSON") from exc

    def check(self) -> dict:
        """Look for a newer release. Returns the same shape as status()."""
        blocked = update_blocked_reason()
        if blocked:
            self._set(DISABLED, blocked)
            return self.status()

        self._set(CHECKING, "Checking for updates...")
        try:
            channel = version_mod.current_channel()
            with httpx.Client(
                timeout=HTTP_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"},
            ) as client:
                try:
                    _, pointer = self._fetch_signed(
                        client, f"channels/{channel}.json", cache_bust=True
                    )
                except UpdateError as exc:
                    # A missing channel pointer is a publishing state, not a
                    # fault: nothing has been released on this channel yet.
                    # "Update server returned 404 for channels/stable.json" is
                    # true but reads like a broken app to whoever is looking
                    # at it, and the first person to see it will be a family
                    # member, not the person who publishes.
                    if exc.status == 404:
                        raise UpdateError(
                            f"No {channel} release has been published yet. "
                            f"You are on version {version_mod.current()}."
                        ) from exc
                    raise

                latest_raw = str(pointer.get("version", ""))
                try:
                    latest = version_mod.Version.parse(latest_raw)
                except ValueError as exc:
                    raise UpdateError(f"Update server named an invalid version {latest_raw!r}") from exc

                current = version_mod.current()
                if latest <= current:
                    with self._lock:
                        self._available = None
                        self._last_check = time.time()
                    self._set(UP_TO_DATE, f"You are on the latest version ({current}).")
                    return self.status()

                manifest_raw, manifest_data = self._fetch_signed(
                    client, f"releases/{latest_raw}/manifest.json"
                )

                # The pointer names an exact manifest by hash. Verifying that
                # binding is what stops a valid-but-different signed manifest
                # (an older release, or one from the other channel) being
                # served in its place.
                expected = str(pointer.get("manifest_sha256", ""))
                actual = manifest_mod.sha256_bytes(manifest_raw)
                if expected and expected != actual:
                    raise UpdateError(
                        "The update manifest does not match what the release "
                        "channel points at, so it was rejected."
                    )

                try:
                    parsed = manifest_mod.Manifest.from_dict(manifest_data)
                except manifest_mod.ManifestError as exc:
                    raise UpdateError(f"The update manifest was rejected: {exc}") from exc

                if parsed.version != latest_raw:
                    raise UpdateError("Update manifest version does not match the channel pointer.")

                self._check_runtime(parsed)

            missing = tuple(e for e in parsed.files if not self._have(e, parsed.version))
            with self._lock:
                self._available = Available(parsed, missing)
                self._last_check = time.time()
            self._set(AVAILABLE, f"Version {parsed.version} is available.")
        except UpdateError as exc:
            self._set(ERROR, str(exc))
        except Exception as exc:  # noqa: BLE001 - never let a check crash the app
            log.exception("update check failed")
            self._set(ERROR, f"Update check failed: {exc}")
        return self.status()

    def _check_runtime(self, parsed: manifest_mod.Manifest) -> None:
        """Refuse an update that needs a runtime this install does not have.

        Applying it would produce an app that cannot start - the worst
        possible outcome, since the user is then stuck with no working
        version and no obvious way back. Better to say so and keep running.
        """
        required = version_mod.Version.parse_or_zero(parsed.min_runtime)
        installed = runtime_version()
        if installed is not None and required > installed:
            raise UpdateError(
                f"Version {parsed.version} needs a newer app runtime "
                f"({required} > {installed}), which an in-app update cannot "
                f"install. Download the latest installer to move up to it."
            )

    # -------------------------------------------------------------- staging

    def _staged_root(self, version: str) -> Path:
        return paths.staging_dir() / version

    def _have(self, entry: manifest_mod.FileEntry, version: str) -> bool:
        """True if this exact content is already on disk (installed or staged)."""
        for candidate in (self._staged_root(version) / entry.path, paths.APP_DIR / entry.path):
            try:
                if candidate.is_file() and candidate.stat().st_size == entry.size:
                    digest, _ = manifest_mod.sha256_file(candidate)
                    if digest == entry.sha256:
                        return True
            except OSError:
                continue
        return False

    def start_download(self) -> dict:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self.status()
            if self._available is None:
                self._set(ERROR, "No update has been found yet - check first.")
                return self.status()
            self._cancel.clear()
            self._thread = threading.Thread(
                target=self._download_worker, name="update-download", daemon=True
            )
            self._thread.start()
        return self.status()

    def cancel(self) -> dict:
        self._cancel.set()
        return self.status()

    def _download_worker(self) -> None:
        with self._lock:
            available = self._available
        if available is None:
            return

        target = self._staged_root(available.version)
        try:
            self._download_total = available.download_bytes
            self._downloaded = 0
            self._progress = 0.0
            self._set(DOWNLOADING, f"Downloading version {available.version}...")

            target.mkdir(parents=True, exist_ok=True)
            with httpx.Client(
                timeout=HTTP_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            ) as client:
                for entry in available.manifest.files:
                    if self._cancel.is_set():
                        raise UpdateError("Update cancelled.")
                    self._materialise(client, entry, target)

            self._verify_tree(available.manifest, target)
            write_pending(available.manifest, target)
            self._set(
                READY,
                f"Version {available.manifest.version} is ready. "
                f"Restart the app to finish installing it.",
            )
        except UpdateError as exc:
            shutil.rmtree(target, ignore_errors=True)
            self._set(ERROR, str(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("update download failed")
            shutil.rmtree(target, ignore_errors=True)
            self._set(ERROR, f"Download failed: {exc}")

    def _materialise(
        self, client: httpx.Client, entry: manifest_mod.FileEntry, target: Path
    ) -> None:
        """Put one file into the staged tree, downloading only if we must."""
        # safe_relpath already ran at parse time; re-resolving and checking
        # containment costs nothing and means a bug in that check cannot turn
        # into a write outside the staging directory.
        destination = (target / entry.path).resolve()
        if not str(destination).startswith(str(target.resolve())):
            raise UpdateError(f"Refusing to write outside the staging area: {entry.path}")

        if destination.is_file() and destination.stat().st_size == entry.size:
            digest, _ = manifest_mod.sha256_file(destination)
            if digest == entry.sha256:
                return  # resumed from an earlier attempt

        destination.parent.mkdir(parents=True, exist_ok=True)

        local = paths.APP_DIR / entry.path
        if local.is_file() and local.stat().st_size == entry.size:
            digest, _ = manifest_mod.sha256_file(local)
            if digest == entry.sha256:
                shutil.copy2(local, destination)
                return  # unchanged since the installed version - no transfer

        blob = self._get(client, manifest_mod.blob_key(entry.sha256))
        self._downloaded += len(blob)
        if self._download_total:
            self._progress = min(1.0, self._downloaded / self._download_total)
        self._notify()

        content = _decompress(blob, entry.path)

        if manifest_mod.sha256_bytes(content) != entry.sha256:
            raise UpdateError(
                f"Downloaded {entry.path} did not match its expected checksum "
                f"and was discarded."
            )

        # Write via a temporary file in the same directory, then replace, so
        # an interruption cannot leave a truncated file that looks complete
        # to the resume check on the next attempt.
        temporary = destination.with_name(destination.name + ".part")
        temporary.write_bytes(content)
        temporary.replace(destination)

    def _verify_tree(self, parsed: manifest_mod.Manifest, target: Path) -> None:
        """Re-hash the whole staged tree before declaring it ready.

        Every file was checked as it was written, so this is belt and braces
        - but it is cheap relative to a download, it catches disk-level
        corruption between write and swap, and the swap it guards is the one
        step that is awkward to undo.
        """
        for entry in parsed.files:
            candidate = target / entry.path
            if not candidate.is_file():
                raise UpdateError(f"Staged update is missing {entry.path}.")
            digest, size = manifest_mod.sha256_file(candidate)
            if digest != entry.sha256 or size != entry.size:
                raise UpdateError(f"Staged file {entry.path} failed verification.")

        expected = {entry.path for entry in parsed.files}
        for candidate in target.rglob("*"):
            if candidate.is_file():
                relative = candidate.relative_to(target).as_posix()
                if relative != PENDING_NAME and relative not in expected:
                    # Left over from an earlier, different attempt. Harmless
                    # to delete: everything the manifest needs is verified
                    # present above.
                    candidate.unlink(missing_ok=True)

    def clear_pending(self) -> dict:
        pending = read_pending()
        if pending:
            shutil.rmtree(paths.staging_dir() / pending.get("version", ""), ignore_errors=True)
        pending_path().unlink(missing_ok=True)
        with self._lock:
            self._available = None
        self._set(IDLE, "Pending update discarded.")
        return self.status()


# ------------------------------------------------------------ pending marker


def pending_path() -> Path:
    return paths.staging_dir() / PENDING_NAME


def write_pending(parsed: manifest_mod.Manifest, staged: Path) -> None:
    paths.staging_dir().mkdir(parents=True, exist_ok=True)
    pending_path().write_text(
        json.dumps(
            {
                "version": parsed.version,
                "channel": parsed.channel,
                "notes": parsed.notes,
                "staged": str(staged),
                "created": time.time(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def read_pending() -> dict | None:
    try:
        data = json.loads(pending_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("version") else None


# --------------------------------------------------------------- gating


def update_blocked_reason() -> str:
    """Why in-app updating is unavailable, or "" if it is available."""
    if not paths.is_installed():
        return (
            "This is a source checkout, not an installed copy, so in-app "
            "updates are disabled - use git instead."
        )
    if not base_url():
        return "No update server is configured for this build."
    if not signing.is_configured():
        return "This build has no update signing key, so updates cannot be verified."
    return ""


def can_update() -> bool:
    return not update_blocked_reason()


updater = Updater()

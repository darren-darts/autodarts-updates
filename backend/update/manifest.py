"""The release manifest: what a version consists of, file by file.

**Why content-addressed blobs.** Every file in a release is stored in the
bucket under its own SHA-256 (`blobs/ab/abcdef...`), not under its path. Two
consequences, and they are the whole reason updates are small:

* A file that did not change between versions has the same hash, so the
  client already has it and downloads nothing. A typical bug fix touches two
  or three Python files - tens of kilobytes - even though the full payload
  is a couple of hundred.
* Uploading is idempotent and append-only. Publishing never overwrites a
  blob an older manifest still points at, so old versions stay installable
  and a half-finished upload cannot corrupt a release that already works.

The manifest lists every file in the version with its hash and size, so the
client can compute exactly what it is missing, show a real byte count before
committing to the download, and verify each file after fetching it.

**Path safety is enforced here, at parse time.** These paths come off the
network and get written to disk, so a manifest is rejected outright if any
entry could escape the staging directory. This is checked once, centrally,
rather than trusted at each use site.
"""
from __future__ import annotations

import hashlib
import json
import posixpath
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

SCHEMA = 1

# Windows reserves these as device names in any directory, extension or not:
# writing to "aux.py" opens a device rather than creating a file.
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9._-]+$")


class ManifestError(Exception):
    """A manifest is malformed, or describes something we refuse to install."""


def safe_relpath(raw: str) -> str:
    """Return `raw` as a normalised relative POSIX path, or raise.

    Rejects absolute paths, drive letters, backslashes, `..` traversal,
    empty or dot components, control characters, and Windows device names.
    Deliberately strict: this is an allowlist of plain filename characters,
    because the payload is our own source tree and has no need for anything
    exotic. A legitimate file that trips this is a signal to rename the
    file, not to loosen the check.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ManifestError("empty path in manifest")
    path = raw.strip()
    # Rejected rather than translated to "/". Translating is safe (the `..`
    # check below still runs on the result) but it silently reinterprets the
    # path, and the release tool only ever emits POSIX separators - so a
    # backslash means something upstream is wrong and should be seen, not
    # quietly fixed up.
    if "\\" in path:
        raise ManifestError(f"backslash in manifest path: {raw!r}")
    if path.startswith("/") or path.startswith("~"):
        raise ManifestError(f"absolute path in manifest: {raw!r}")
    if ":" in path:
        raise ManifestError(f"drive or stream specifier in manifest path: {raw!r}")

    components = path.split("/")
    for component in components:
        if component in ("", ".", ".."):
            raise ManifestError(f"unsafe path component in manifest: {raw!r}")
        if not _SAFE_COMPONENT.match(component):
            raise ManifestError(f"unsupported characters in manifest path: {raw!r}")
        if component.split(".")[0].lower() in _WINDOWS_RESERVED:
            raise ManifestError(f"reserved device name in manifest path: {raw!r}")

    # Belt and braces: normalisation must not change anything, or the string
    # we validated is not the string that would be used.
    if posixpath.normpath(path) != path:
        raise ManifestError(f"non-normalised path in manifest: {raw!r}")
    return path


def sha256_file(path) -> tuple[str, int]:
    """(hex digest, size). Streamed, so payload size never bounds memory."""
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_bytes(payload: dict) -> bytes:
    """The exact bytes that get signed and verified.

    Signing a re-serialised dict only works if both sides serialise
    identically, so this pins sort order, separators and encoding. Every
    signature and verification goes through this function - there is no
    second place that turns a manifest into bytes.
    """
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def blob_key(sha256: str) -> str:
    """Bucket key for a blob. Two-character fan-out keeps listings sane.

    Blobs are stored gzipped, and the `.gz` is part of the key rather than a
    `Content-Encoding` header. Transparent HTTP decompression would work,
    but it makes Content-Length report the compressed size while the body
    arrives expanded, which quietly breaks progress accounting - and some
    CDNs re-compress or strip the header. Being explicit costs one call to
    `gzip.decompress` and removes the whole class of problem.

    The digest is always of the *uncompressed* file, so it verifies the
    thing that actually lands on disk, independent of how it travelled.
    """
    if len(sha256) != 64 or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ManifestError(f"not a sha256 digest: {sha256!r}")
    return f"blobs/{sha256[:2]}/{sha256}.gz"


@dataclass(frozen=True)
class FileEntry:
    path: str
    sha256: str
    size: int
    # Size of the stored gzipped blob. Carried so the UI can quote what will
    # actually be transferred; without it the only honest figure available
    # is the uncompressed total, which overstates a source-code update by
    # roughly 3.5x and makes a 40 KB download look like 150 KB.
    csize: int = 0

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size": self.size,
            "csize": self.csize,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FileEntry":
        if not isinstance(data, dict):
            raise ManifestError("file entry is not an object")
        path = safe_relpath(data.get("path", ""))
        sha256 = str(data.get("sha256", "")).lower()
        blob_key(sha256)  # validates the digest shape, raises if wrong
        try:
            size = int(data.get("size", -1))
            csize = int(data.get("csize", 0))
        except (TypeError, ValueError) as exc:
            raise ManifestError(f"bad size for {path!r}") from exc
        if size < 0 or csize < 0:
            raise ManifestError(f"negative size for {path!r}")
        return cls(path, sha256, size, csize)


@dataclass(frozen=True)
class Manifest:
    version: str
    channel: str
    released: str
    notes: str = ""
    files: tuple[FileEntry, ...] = field(default_factory=tuple)
    schema: int = SCHEMA
    # Bumped when a release needs a newer bundled runtime (a new pip
    # dependency, a new Python). The client refuses such an update and says
    # to reinstall, rather than applying it and failing to start.
    min_runtime: str = "0.0.0"
    # Lets the client notice that dependencies changed even when it cannot
    # act on it automatically, so the message can be specific.
    requirements_sha256: str = ""

    @property
    def total_size(self) -> int:
        return sum(entry.size for entry in self.files)

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "version": self.version,
            "channel": self.channel,
            "released": self.released,
            "notes": self.notes,
            "min_runtime": self.min_runtime,
            "requirements_sha256": self.requirements_sha256,
            "files": [entry.to_dict() for entry in self.files],
        }

    def canonical(self) -> bytes:
        return canonical_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict) -> "Manifest":
        if not isinstance(data, dict):
            raise ManifestError("manifest is not an object")
        schema = data.get("schema")
        if schema != SCHEMA:
            raise ManifestError(
                f"manifest schema {schema!r} is not supported by this version "
                f"(expected {SCHEMA}) - reinstall to get a newer updater"
            )
        raw_files = data.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            raise ManifestError("manifest lists no files")

        files = tuple(FileEntry.from_dict(item) for item in raw_files)
        seen = {entry.path for entry in files}
        if len(seen) != len(files):
            raise ManifestError("manifest lists the same path twice")

        return cls(
            version=str(data.get("version", "")),
            channel=str(data.get("channel", "")),
            released=str(data.get("released", "")),
            notes=str(data.get("notes", "")),
            files=files,
            schema=schema,
            min_runtime=str(data.get("min_runtime", "0.0.0")),
            requirements_sha256=str(data.get("requirements_sha256", "")),
        )

    @classmethod
    def build(cls, version: str, channel: str, notes: str, files, **kwargs) -> "Manifest":
        return cls(
            version=version,
            channel=channel,
            released=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            notes=notes,
            files=tuple(sorted(files, key=lambda e: e.path)),
            **kwargs,
        )


def channel_pointer(version: str, manifest_sha256: str) -> dict:
    """The small, frequently-fetched document that names the current release.

    Signed separately from the manifest it names. Without that, someone able
    to write to the bucket could not forge a release, but could still swap
    the pointer back to a genuine *older* one - re-exposing a fixed bug on
    every install. Including the manifest hash also pins the pointer to one
    exact manifest, so the two can never be mixed and matched.
    """
    return {
        "schema": SCHEMA,
        "version": version,
        "manifest_sha256": manifest_sha256,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

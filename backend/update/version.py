"""The installed app's own identity: version string and update channel.

VERSION.json sits at the root of the app payload, so it is replaced along
with everything else when an update is applied - which is exactly what makes
the new version number appear without any separate bookkeeping.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import total_ordering

import paths

VERSION_PATH = paths.APP_DIR / "VERSION.json"

DEFAULT_VERSION = "0.0.0"
DEFAULT_CHANNEL = "stable"
CHANNELS = ("stable", "beta")

_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")


@total_ordering
@dataclass(frozen=True)
class Version:
    """A semver subset: major.minor.patch with an optional pre-release tag.

    Comparison is numeric per component, never lexicographic - "0.10.0" is
    newer than "0.9.0", which a plain string compare gets backwards. A
    pre-release sorts *before* its own release ("1.2.0-beta.1" < "1.2.0"),
    matching semver, so promoting a beta to stable always reads as an
    upgrade rather than a downgrade.
    """

    major: int = 0
    minor: int = 0
    patch: int = 0
    prerelease: str = ""

    @classmethod
    def parse(cls, raw: str) -> "Version":
        match = _SEMVER.match((raw or "").strip())
        if not match:
            raise ValueError(f"not a valid version: {raw!r}")
        return cls(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            match.group(4) or "",
        )

    @classmethod
    def parse_or_zero(cls, raw: str) -> "Version":
        """For reading whatever is on disk, which we do not control."""
        try:
            return cls.parse(raw)
        except ValueError:
            return cls()

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        return f"{base}-{self.prerelease}" if self.prerelease else base

    def _key(self):
        # An empty pre-release must sort ABOVE any non-empty one, so flag it
        # with 1 vs 0 rather than relying on "" comparing low.
        return (self.major, self.minor, self.patch, 1 if not self.prerelease else 0, self.prerelease)

    def __lt__(self, other: "Version") -> bool:
        return self._key() < other._key()

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Version) and self._key() == other._key()

    def __hash__(self) -> int:
        return hash(self._key())


def read() -> tuple[str, str]:
    """(version, channel) as recorded in the payload. Never raises."""
    try:
        data = json.loads(VERSION_PATH.read_text(encoding="utf-8"))
        version = str(data.get("version") or DEFAULT_VERSION)
        channel = str(data.get("channel") or DEFAULT_CHANNEL)
    except (OSError, ValueError, TypeError):
        return DEFAULT_VERSION, DEFAULT_CHANNEL
    return version, (channel if channel in CHANNELS else DEFAULT_CHANNEL)


def current() -> Version:
    return Version.parse_or_zero(read()[0])


def current_channel() -> str:
    """The channel to check, with a settings override for opting into beta.

    Imported lazily: settings_store is not needed to answer "what am I", and
    the release tool reuses this module without a settings file present.
    """
    try:
        import settings_store

        configured = settings_store.load().get("updates", {}).get("channel")
    except Exception:
        configured = None
    if configured in CHANNELS:
        return configured
    return read()[1]

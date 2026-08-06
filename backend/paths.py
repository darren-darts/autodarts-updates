"""Filesystem layout - the one place that knows where things live.

Two layouts must both keep working:

    development                     installed
    claude-plan/                    Autodarts/
      backend/                        .autodarts-root      <- marker
      frontend/dist/                  runtime/             <- bundled Python
      config/                         app/                 <- REPLACED on update
      tools/                            backend/
                                        frontend/dist/
                                        VERSION.json
                                      config/              <- user data, preserved
                                      staging/             <- downloads in progress
                                      logs/

The installed layout deliberately puts ``config/`` *outside* ``app/``.
Applying an update swaps ``app/`` wholesale for a freshly downloaded
directory, so everything inside it is disposable by definition. Calibration
profiles, the player roster, selfies and settings are emphatically not
disposable - a family member losing a painstaking 3-camera calibration to a
bug-fix update would be far worse than the bug. Keeping the two apart at the
directory level makes that structural rather than a rule someone has to
remember.

Resolution is explicit, never guessed:

1. ``AUTODARTS_CONFIG_DIR`` / ``AUTODARTS_ROOT`` environment variables win.
   The launcher sets these, so an installed app never relies on inference.
2. Otherwise, a ``.autodarts-root`` marker file written by the installer in
   ``app/``'s parent identifies an installed tree.
3. Otherwise this is a source checkout: ``config/`` sits beside ``backend/``,
   exactly as it always has.

Rule 3 is why existing dev workflows are unaffected by any of this.
"""

from __future__ import annotations

import os
from pathlib import Path

# The directory containing backend/ and frontend/ - i.e. the app payload root.
APP_DIR = Path(__file__).resolve().parent.parent

# Written by the installer beside app/. Its presence is the unambiguous
# signal that we are running from an installed tree rather than a checkout.
INSTALL_MARKER = ".autodarts-root"


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser().resolve() if raw else None


def install_root() -> Path:
    """The directory that holds app/, config/, staging/ and runtime/.

    In a source checkout there is no such wrapper, so the checkout root
    itself stands in for it and config/ lands in its historical location.
    """
    explicit = _env_path("AUTODARTS_ROOT")
    if explicit is not None:
        return explicit
    if (APP_DIR.parent / INSTALL_MARKER).exists():
        return APP_DIR.parent
    return APP_DIR


def config_dir() -> Path:
    """Where user data lives. Never inside the updatable payload."""
    return _env_path("AUTODARTS_CONFIG_DIR") or (install_root() / "config")


def staging_dir() -> Path:
    """Scratch space for downloading and verifying an update before it is applied."""
    return _env_path("AUTODARTS_STAGING_DIR") or (install_root() / "staging")


def logs_dir() -> Path:
    return install_root() / "logs"


def frontend_dist() -> Path:
    return APP_DIR / "frontend" / "dist"


def is_installed() -> bool:
    """True when running from an installed tree, i.e. updates can be applied.

    A source checkout deliberately reports False: replacing a git working
    tree with a downloaded payload would destroy uncommitted work.
    """
    if _env_path("AUTODARTS_ROOT") is not None:
        return True
    return (APP_DIR.parent / INSTALL_MARKER).exists()


def ensure_dirs() -> None:
    for path in (config_dir(), staging_dir(), logs_dir()):
        path.mkdir(parents=True, exist_ok=True)


# Convenience constants for the existing stores. These are resolved once at
# import, which is correct because the environment is fixed before the app
# starts - the launcher sets it, then execs the server.
CONFIG_DIR = config_dir()
INSTALL_ROOT = install_root()
STAGING_DIR = staging_dir()

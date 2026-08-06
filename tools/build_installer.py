"""Build the thing you actually hand to someone.

    python tools/build_installer.py 1.0.0

Stages a complete install tree, then packages it:

    dist-release/stage/
      .autodarts-root      <- marks this as an installed tree
      launcher.py
      runtime/             <- from build_runtime.py
      app/                 <- the same payload the updater ships
        backend/ frontend/dist/ VERSION.json

If Inno Setup is present it produces `Autodarts-Setup-<version>.exe`.
Otherwise it falls back to a zip plus `Install.bat`, which does the same job
with a worse first impression - so the .exe is worth the one-time install of
Inno Setup on the release machine.

The staged `app/` is built from the *same* payload rules as a release
(release.toml `[payload]`), so what the installer contains and what an
update delivers cannot drift apart. That matters more than it sounds: if
they diverged, a freshly installed copy and an updated copy of the same
version would be different, and only one of them would be the one you
tested.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist-release"
STAGE = OUT / "stage"
RUNTIME = OUT / "runtime"

sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "backend"))

# Searched rather than hard-coded. Inno Setup pins its major version into the
# directory name ("Inno Setup 6", "Inno Setup 7"), and its installer offers a
# per-user location under AppData\Local\Programs that needs no administrator -
# which is the one most people end up accepting. Listing exact paths meant a
# perfectly good install went undetected and silently fell back to the zip.
ISCC_SEARCH_ROOTS = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs",
    Path(r"C:\Program Files (x86)"),
    Path(r"C:\Program Files"),
]

INSTALL_BAT = r"""@echo off
setlocal
title Autodarts installer

set "TARGET=%LOCALAPPDATA%\Autodarts"

echo Installing Autodarts to %TARGET%
echo.

if exist "%TARGET%\app" (
    echo Removing the previous version's program files...
    rmdir /s /q "%TARGET%\app"
)
if exist "%TARGET%\staging" rmdir /s /q "%TARGET%\staging"

if not exist "%TARGET%" mkdir "%TARGET%"

echo Copying files - this takes a minute...
xcopy /e /i /y /q "%~dp0runtime" "%TARGET%\runtime" >nul
xcopy /e /i /y /q "%~dp0app" "%TARGET%\app" >nul
copy /y "%~dp0launcher.py" "%TARGET%\launcher.py" >nul
copy /y "%~dp0.autodarts-root" "%TARGET%\.autodarts-root" >nul

if not exist "%TARGET%\config" mkdir "%TARGET%\config"

echo Creating a desktop shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Autodarts.lnk');" ^
  "$s.TargetPath='%TARGET%\runtime\pythonw.exe';" ^
  "$s.Arguments='\"%TARGET%\launcher.py\"';" ^
  "$s.WorkingDirectory='%TARGET%';$s.Save()"

echo.
echo Done. Start Autodarts from the desktop shortcut.
pause
"""


def log(message: str) -> None:
    print(message, flush=True)


def payload_files() -> list[tuple[str, Path]]:
    """Exactly the files a release ships, from the same config."""
    import release

    config = release.load_config() if (ROOT / "release.toml").is_file() else {
        "payload": {
            "include": [
                "VERSION.json",
                "backend/**/*.py",
                "backend/requirements.txt",
                "frontend/dist/**/*",
            ],
            "exclude": ["**/__pycache__/**", "**/*.pyc", "backend/.venv/**", "backend/dev_server.py"],
        }
    }
    return release.collect_files(config)


def stage(version: str, channel: str) -> None:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    (STAGE / "app").mkdir(parents=True)

    import json
    import release

    (ROOT / "VERSION.json").write_text(
        json.dumps({"version": version, "channel": channel}, indent=2) + "\n", encoding="utf-8"
    )

    # Stamp the update origin into the build being packaged. Without this an
    # installed copy has an empty UPDATE_BASE_URL and reports "no update
    # server is configured" forever - the installer would produce something
    # that can never update itself, which is the entire point of it.
    if (ROOT / "release.toml").is_file():
        base_url = release.configured_base_url(release.load_config())
        if base_url:
            release.write_origin(base_url)
            log(f"  update origin: {base_url}")
        else:
            log("  WARNING: release.toml has no s3.base_url - this build cannot self-update")
    else:
        log("  WARNING: no release.toml - this build cannot self-update")

    files = payload_files()
    for relative, source in files:
        target = STAGE / "app" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    log(f"  staged {len(files)} app files")

    if not RUNTIME.is_dir():
        raise SystemExit(
            f"No runtime at {RUNTIME}. Run: python tools/build_runtime.py"
        )
    shutil.copytree(RUNTIME, STAGE / "runtime")
    log("  staged the runtime")

    shutil.copy2(ROOT / "installer" / "launcher.py", STAGE / "launcher.py")
    (STAGE / ".autodarts-root").write_text(
        "This file marks an installed Autodarts tree. Do not delete it.\n", encoding="utf-8"
    )


def find_iscc() -> Path | None:
    """Locate the Inno Setup compiler, newest major version first."""
    found = shutil.which("iscc")
    if found:
        return Path(found)

    candidates: list[tuple[int, Path]] = []
    for root in ISCC_SEARCH_ROOTS:
        if not root.is_dir():
            continue
        for directory in root.glob("Inno Setup*"):
            exe = directory / "ISCC.exe"
            if exe.is_file():
                digits = "".join(c for c in directory.name if c.isdigit())
                candidates.append((int(digits or 0), exe))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def build_exe(version: str, iscc: Path) -> Path:
    log(f"  running {iscc}")
    result = subprocess.run(
        [
            str(iscc),
            f"/DAppVersion={version}",
            f"/DStageDir={STAGE}",
            str(ROOT / "installer" / "autodarts.iss"),
        ],
        cwd=str(ROOT / "installer"),
    )
    if result.returncode != 0:
        raise SystemExit("Inno Setup failed.")
    return OUT / f"Autodarts-Setup-{version}.exe"


def build_zip(version: str) -> Path:
    (STAGE / "Install.bat").write_text(INSTALL_BAT, encoding="utf-8")
    archive = OUT / f"Autodarts-{version}-windows.zip"
    log("  compressing (this takes a while - the runtime is large)")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in STAGE.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(STAGE))
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Autodarts Windows installer.")
    parser.add_argument("version")
    parser.add_argument("--channel", default="stable", choices=("stable", "beta"))
    parser.add_argument("--skip-build", action="store_true", help="reuse the existing frontend build")
    parser.add_argument("--zip", action="store_true", help="force the zip fallback")
    args = parser.parse_args()

    if os.name != "nt":
        raise SystemExit("This builds the Windows installer; use installer/install-pi.sh on a Pi.")

    OUT.mkdir(parents=True, exist_ok=True)

    if not args.skip_build:
        import release

        release.build_frontend()

    log("Staging the install tree...")
    stage(args.version, args.channel)

    iscc = None if args.zip else find_iscc()
    if iscc:
        log("Building the installer...")
        artefact = build_exe(args.version, iscc)
    else:
        if not args.zip:
            log("\n  Inno Setup not found - falling back to a zip.")
            log("  For a proper Setup.exe install it from https://jrsoftware.org/isdl.php\n")
        artefact = build_zip(args.version)

    size = artefact.stat().st_size / 1e6 if artefact.exists() else 0
    log(f"\nBuilt {artefact.name} ({size:.0f} MB)\n  {artefact}")
    log("\nThis is the one-time download. Everything after it is an in-app update.")


if __name__ == "__main__":
    main()

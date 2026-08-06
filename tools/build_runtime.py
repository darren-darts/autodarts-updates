"""Build the self-contained Python runtime that ships with the app.

    python tools/build_runtime.py                  # for this platform
    python tools/build_runtime.py --trim-only      # re-trim an existing build

The point is that nobody receiving this app should have to install Python,
pip, Node, or a compiler. On Windows that means shipping an interpreter;
on a Pi the OS already has a suitable python3, so a venv built at install
time is both smaller and better behaved.

**Windows: the embeddable distribution, not a venv.** A venv records an
absolute path to the Python that created it and stops working when moved,
so it cannot be built here and shipped somewhere else. The embeddable zip
is a complete, relocatable interpreter designed exactly for bundling. Two
adjustments make it usable:

* `pythonXY._pth` ships with `import site` commented out, which disables
  `site-packages` entirely - pip installs succeed and then nothing can be
  imported. It has to be re-enabled.
* pip is absent, so `get-pip.py` bootstraps it once.

The result is trimmed afterwards: pip, setuptools and the bundled test
suites are build-time tools, and leaving them in adds over 25 MB to
something friends and family have to download over home broadband.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist-release"
RUNTIME = OUT / "runtime"

PYTHON_VERSION = "3.11.9"
EMBED_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_VERSION}/"
    f"python-{PYTHON_VERSION}-embed-amd64.zip"
)
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

# Everything here is a build-time tool or a test suite, not something the
# app imports at runtime. Measured on a real build these account for roughly
# 25-30 MB of the download.
TRIM = [
    "Lib/site-packages/pip",
    "Lib/site-packages/setuptools",
    "Lib/site-packages/pkg_resources",
    "Lib/site-packages/wheel",
    "Lib/site-packages/numpy/tests",
    "Lib/site-packages/numpy/_core/tests",
    "Lib/site-packages/numpy/core/tests",
]
TRIM_GLOBS = ["**/__pycache__", "**/*.dist-info", "**/*.pyc"]


def log(message: str) -> None:
    print(f"  {message}", flush=True)


def download(url: str, target: Path) -> Path:
    if target.is_file():
        log(f"using cached {target.name}")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    log(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as response:
        target.write_bytes(response.read())
    return target


def build_windows(requirements: Path) -> Path:
    cache = OUT / "cache"
    archive = download(EMBED_URL, cache / f"python-{PYTHON_VERSION}-embed-amd64.zip")

    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(parents=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(RUNTIME)
    log(f"extracted the embeddable interpreter to {RUNTIME}")

    # Re-enable site-packages. Without this pip installs land in a directory
    # the interpreter will not look in, and every import fails at runtime
    # with nothing to indicate why.
    for pth in RUNTIME.glob("python*._pth"):
        text = pth.read_text(encoding="utf-8")
        if "#import site" in text:
            pth.write_text(text.replace("#import site", "import site"), encoding="utf-8")
            log(f"enabled site-packages in {pth.name}")
        if "Lib\\site-packages" not in text:
            with open(pth, "a", encoding="utf-8") as handle:
                handle.write("Lib\\site-packages\n")

    python = RUNTIME / "python.exe"
    get_pip = download(GET_PIP_URL, cache / "get-pip.py")
    log("bootstrapping pip")
    run([str(python), str(get_pip), "--no-warn-script-location"])

    log("installing dependencies (this is the slow part)")
    run([
        str(python), "-m", "pip", "install",
        "--no-warn-script-location",
        "--only-binary", ":all:",
        "-r", str(requirements),
    ])
    return python


def build_posix(requirements: Path) -> Path:
    """A venv from the system python3. Built on the target, not shipped.

    On a Pi the OS python3 is the right interpreter to use - it is built for
    that CPU and already has working wheels available for OpenCV and numpy.
    Shipping our own would mean cross-compiling for ARM, which is a great
    deal of work to end up with something worse.
    """
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(parents=True)
    run([sys.executable, "-m", "venv", str(RUNTIME)])
    python = RUNTIME / "bin" / "python3"
    run([str(python), "-m", "pip", "install", "--upgrade", "pip"])
    run([str(python), "-m", "pip", "install", "-r", str(requirements)])
    return python


def run(command: list[str]) -> None:
    result = subprocess.run(command)
    if result.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(command)}")


def trim() -> int:
    """Remove build-time tooling. Returns bytes saved."""
    saved = 0
    for relative in TRIM:
        target = RUNTIME / relative
        if target.exists():
            saved += directory_size(target)
            shutil.rmtree(target, ignore_errors=True)
    for pattern in TRIM_GLOBS:
        for target in RUNTIME.glob(pattern):
            if target.is_dir():
                saved += directory_size(target)
                shutil.rmtree(target, ignore_errors=True)
            elif target.is_file():
                saved += target.stat().st_size
                target.unlink(missing_ok=True)
    return saved


def directory_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def write_runtime_marker(python: Path) -> None:
    """Record what this runtime provides, so the updater can gate on it.

    `min_runtime` in a release manifest is compared against this. It is what
    stops an in-app update that needs a new pip dependency from being
    applied onto a runtime that does not have it - which would otherwise
    produce an app that downloads successfully and then cannot start.
    """
    import json

    requirements_hash = ""
    requirements = ROOT / "backend" / "requirements.txt"
    if requirements.is_file():
        sys.path.insert(0, str(ROOT / "backend"))
        from update.manifest import sha256_file

        requirements_hash = sha256_file(requirements)[0]

    (RUNTIME / "RUNTIME.json").write_text(
        json.dumps(
            {
                "version": os.environ.get("AUTODARTS_RUNTIME_VERSION", "1.0.0"),
                "python": PYTHON_VERSION if os.name == "nt" else sys.version.split()[0],
                "platform": "win-x64" if os.name == "nt" else "posix",
                "requirements_sha256": requirements_hash,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trim-only", action="store_true")
    args = parser.parse_args()

    requirements = ROOT / "backend" / "requirements.txt"
    if not requirements.is_file():
        raise SystemExit(f"missing {requirements}")

    OUT.mkdir(parents=True, exist_ok=True)

    if not args.trim_only:
        print(f"Building runtime for {'Windows' if os.name == 'nt' else 'POSIX'}...")
        python = build_windows(requirements) if os.name == "nt" else build_posix(requirements)
    else:
        python = RUNTIME / ("python.exe" if os.name == "nt" else "bin/python3")

    before = directory_size(RUNTIME)
    saved = trim()
    write_runtime_marker(python)
    after = directory_size(RUNTIME)

    print(
        f"\nRuntime built at {RUNTIME}"
        f"\n  before trim: {before / 1e6:.0f} MB"
        f"\n  trimmed:     {saved / 1e6:.0f} MB"
        f"\n  final:       {after / 1e6:.0f} MB"
    )


if __name__ == "__main__":
    main()

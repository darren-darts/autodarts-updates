#!/usr/bin/env bash
#
# Install or update ShepDarts on a Raspberry Pi (or any Debian-ish Linux).
#
#   curl -fsSL https://YOUR-BUCKET/updates/installers/install-pi.sh | bash
#
# Produces the same layout the Windows installer does, so the in-app updater
# works identically on both:
#
#   ~/autodarts/
#     .autodarts-root    launcher.py    runtime/  (venv)
#     app/               config/        staging/  logs/
#
# Unlike Windows, the interpreter is not shipped - the Pi's own python3 is
# used to build a venv here. Cross-compiling OpenCV and numpy for ARM to
# avoid that would be a lot of work to end up with something slower than the
# distribution's own optimised wheels.
#
# Safe to re-run: it replaces app/ and runtime/ and never touches config/.

set -euo pipefail

BASE_URL="${AUTODARTS_BASE_URL:-}"
CHANNEL="${AUTODARTS_CHANNEL:-stable}"
TARGET="${AUTODARTS_HOME:-$HOME/autodarts}"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
die()  { printf '\n\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

[ -n "$BASE_URL" ] || die "Set AUTODARTS_BASE_URL to your update URL, e.g.
  AUTODARTS_BASE_URL=https://your-bucket.s3.eu-west-2.amazonaws.com/updates bash install-pi.sh"

BASE_URL="${BASE_URL%/}"

say "Installing ShepDarts to $TARGET"

# ---------------------------------------------------------------- packages
# libgl1 and libglib2.0-0 are OpenCV's runtime shared libraries. Without
# them `import cv2` fails with a bare linker error that says nothing about
# which package is missing, which is a miserable way to start.
say "Checking system packages"
MISSING=()
for pkg in python3-venv python3-dev libgl1 libglib2.0-0; do
  dpkg -s "$pkg" >/dev/null 2>&1 || MISSING+=("$pkg")
done
if [ ${#MISSING[@]} -gt 0 ]; then
  info "installing: ${MISSING[*]}"
  sudo apt-get update -qq
  sudo apt-get install -y "${MISSING[@]}"
else
  info "all present"
fi

# ------------------------------------------------------------------ layout
mkdir -p "$TARGET"/{config,staging,logs}
echo "This file marks an installed ShepDarts tree. Do not delete it." > "$TARGET/.autodarts-root"

# ------------------------------------------------------- fetch the payload
# The installer pulls the current release through the *same* signed manifest
# the in-app updater uses, rather than a separately built tarball. One
# publishing path means a fresh install and an updated install of the same
# version are byte-for-byte identical.
say "Fetching the current $CHANNEL release"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

curl -fsSL "$BASE_URL/channels/$CHANNEL.json" -o "$TMP/channel.json" \
  || die "Could not reach $BASE_URL - check the URL and that the prefix is publicly readable."
VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$TMP/channel.json")"
info "version $VERSION"

curl -fsSL "$BASE_URL/releases/$VERSION/manifest.json" -o "$TMP/manifest.json"
curl -fsSL "$BASE_URL/releases/$VERSION/manifest.json.sig" -o "$TMP/manifest.json.sig"

rm -rf "$TARGET/app.new"
mkdir -p "$TARGET/app.new"

python3 - "$TMP/manifest.json" "$TARGET/app.new" "$BASE_URL" <<'PYTHON'
import gzip, hashlib, json, sys, urllib.request
from pathlib import Path

manifest_path, target, base = sys.argv[1], Path(sys.argv[2]), sys.argv[3].rstrip("/")
manifest = json.loads(Path(manifest_path).read_text())

# Signature verification happens in the app itself, which has the public key
# compiled in; here we can still check every file against the hash the
# manifest declares, which catches a truncated or corrupted download.
files = manifest["files"]
for index, entry in enumerate(files, 1):
    path = entry["path"]
    if path.startswith(("/", "~")) or ".." in path.split("/") or "\\" in path:
        raise SystemExit(f"refusing unsafe path in manifest: {path!r}")
    digest = entry["sha256"]
    url = f"{base}/blobs/{digest[:2]}/{digest}.gz"
    with urllib.request.urlopen(url, timeout=120) as response:
        data = gzip.decompress(response.read())
    if hashlib.sha256(data).hexdigest() != digest:
        raise SystemExit(f"checksum mismatch for {path}")
    out = target / path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"\r  {index}/{len(files)} files", end="", flush=True)
print()
PYTHON

rm -rf "$TARGET/app.previous"
[ -d "$TARGET/app" ] && mv "$TARGET/app" "$TARGET/app.previous"
mv "$TARGET/app.new" "$TARGET/app"
rm -rf "$TARGET/app.previous"

# ----------------------------------------------------------------- runtime
say "Building the Python environment"
if [ ! -x "$TARGET/runtime/bin/python3" ]; then
  python3 -m venv "$TARGET/runtime"
fi
"$TARGET/runtime/bin/pip" install --upgrade pip --quiet
"$TARGET/runtime/bin/pip" install -r "$TARGET/app/backend/requirements.txt" --quiet

# The hash is what launcher.py's sync_runtime_dependencies compares against on
# every future update - writing the real one now (matching what was just
# installed above) means the very next update does not redundantly reinstall
# everything just because this file said nothing about what was already done.
python3 - "$TARGET" <<'PYTHON'
import hashlib, json, sys
from pathlib import Path
root = Path(sys.argv[1])
requirements = root / "app" / "backend" / "requirements.txt"
digest = hashlib.sha256(requirements.read_bytes()).hexdigest() if requirements.is_file() else ""
(root / "runtime" / "RUNTIME.json").write_text(
    json.dumps({"version": "1.0.0", "platform": "posix", "requirements_sha256": digest}, indent=2)
)
PYTHON

curl -fsSL "$BASE_URL/installers/launcher.py" -o "$TARGET/launcher.py" 2>/dev/null \
  || cp "$TARGET/app/../launcher.py" "$TARGET/launcher.py" 2>/dev/null \
  || die "Could not obtain launcher.py - upload it to $BASE_URL/installers/launcher.py"

# ------------------------------------------------------------ systemd unit
say "Installing the autostart service"
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/autodarts.service" <<UNIT
[Unit]
Description=ShepDarts
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$TARGET
Environment=AUTODARTS_NO_BROWSER=1
ExecStart=$TARGET/runtime/bin/python3 $TARGET/launcher.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
UNIT

systemctl --user daemon-reload
systemctl --user enable autodarts.service >/dev/null 2>&1 || true
# Without lingering the service stops when the user logs out, which for a
# headless Pi next to a dartboard means it never runs at all.
loginctl enable-linger "$USER" >/dev/null 2>&1 || true

say "Installed ShepDarts $VERSION"
info "start:   systemctl --user restart autodarts"
info "logs:    journalctl --user -u autodarts -f"
info "open:    http://$(hostname -I | awk '{print $1}'):8000"
info ""
info "Updates from here on are done in the app itself, on the Updates page."

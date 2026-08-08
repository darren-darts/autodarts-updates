#!/usr/bin/env bash
#
# Test-install ShepDarts on a Raspberry Pi straight from this git checkout.
#
# This is the quick "get the current code onto a Pi and try it" path. It is
# deliberately different from installer/install-pi.sh, which pulls signed
# release manifests from a published update server (the production updater).
# Here we just run the app directly out of a clone.
#
# Usage - on the Pi (Raspberry Pi OS Bookworm or any recent Debian):
#
#   git clone -b pi-test https://github.com/darren-darts/autodarts-updates.git shepdarts
#   cd shepdarts
#   bash installer/install-pi-git.sh
#
# Re-run any time to pick up changes (after `git pull`). Safe to re-run: it
# rebuilds the venv in place and never touches config/.
#
set -euo pipefail

# This script lives in <root>/installer/, so the checkout root is one level up.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PORT="${SHEPDARTS_PORT:-8000}"
SERVICE="${SHEPDARTS_SERVICE:-shepdarts}"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
die()  { printf '\n\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

[ -f "$ROOT/backend/requirements.txt" ] \
  || die "Run this from inside the ShepDarts checkout: bash installer/install-pi-git.sh"

say "Installing ShepDarts from $ROOT"

# ---------------------------------------------------------------- packages
# The detection stack is Autodarts now, so no OpenCV/numpy here - a plain
# python3 venv is all the backend needs.
say "Checking system packages"
NEED=()
for pkg in python3 python3-venv python3-dev; do
  dpkg -s "$pkg" >/dev/null 2>&1 || NEED+=("$pkg")
done
if [ "${#NEED[@]}" -gt 0 ]; then
  info "installing: ${NEED[*]}"
  sudo apt-get update -qq
  sudo apt-get install -y "${NEED[@]}"
else
  info "all present"
fi

# ----------------------------------------------------------- python env
say "Building the Python environment (backend/.venv)"
if [ ! -x "$ROOT/backend/.venv/bin/python" ]; then
  python3 -m venv "$ROOT/backend/.venv"
fi
"$ROOT/backend/.venv/bin/pip" install --upgrade pip --quiet
"$ROOT/backend/.venv/bin/pip" install -r "$ROOT/backend/requirements.txt" --quiet
info "backend dependencies installed"

# -------------------------------------------------------------- web UI
# The repo ships a prebuilt frontend/dist, so a Pi needs no Node toolchain.
# If it is missing and npm happens to be available, build it; otherwise stop
# with a clear message rather than serving a blank page.
say "Checking the web UI"
if [ -f "$ROOT/frontend/dist/index.html" ]; then
  info "prebuilt UI present"
elif command -v npm >/dev/null 2>&1; then
  info "no prebuilt dist - building with npm"
  ( cd "$ROOT/frontend" && npm install && npm run build )
else
  die "frontend/dist is missing and npm is not installed.
  Pull a build of the repo that includes frontend/dist, or install Node
  (https://nodejs.org) and re-run so the UI can be built."
fi

# ------------------------------------------ machine-specific data (git-ignored)
mkdir -p "$ROOT/config" "$ROOT/logs"

# ------------------------------------------------------- systemd user service
say "Installing the autostart service ($SERVICE)"
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/$SERVICE.service" <<UNIT
[Unit]
Description=ShepDarts (Snakes & Ladders and friends)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT/backend
ExecStart=$ROOT/backend/.venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port $PORT
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
UNIT

systemctl --user daemon-reload
systemctl --user enable "$SERVICE.service" >/dev/null 2>&1 || true
# Without lingering the service stops when the user logs out, which for a
# headless Pi next to a dartboard means it never runs at all.
loginctl enable-linger "$USER" >/dev/null 2>&1 || true
systemctl --user restart "$SERVICE.service"

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
say "ShepDarts is installed and running"
info "open:    http://${IP:-<pi-ip>}:$PORT"
info "status:  systemctl --user status $SERVICE"
info "logs:    journalctl --user -u $SERVICE -f"
info "restart: systemctl --user restart $SERVICE"
info "update:  git -C \"$ROOT\" pull && bash \"$ROOT/installer/install-pi-git.sh\""

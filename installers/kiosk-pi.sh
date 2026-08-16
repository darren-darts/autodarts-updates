#!/usr/bin/env bash
#
# Turn a Raspberry Pi into a ShepDarts kiosk: with an HDMI monitor plugged in,
# Chromium opens the app full-screen at boot - no keyboard or mouse needed.
#
# This is separate from install-pi.sh on purpose. That script installs the app
# as a background service; this one adds the on-screen browser, which only makes
# sense on a Pi that has the desktop and a monitor.
#
#   bash installer/kiosk-pi.sh
#
# Requirements:
#   * Raspberry Pi OS *with Desktop* (Bookworm). Pi OS Lite has no desktop
#     session to open a window in - install the Desktop first.
#   * ShepDarts already installed and running (install-pi.sh), serving :8000.
#
# After running it, do the one thing that needs a keyboard, once:
#   sudo raspi-config -> System Options -> Boot / Auto Login -> Desktop Autologin
# so the Pi reaches the desktop on its own. Then `sudo reboot` and it is hands
# free from then on. Re-run this script any time to change the settings.
#
set -euo pipefail

URL="${SHEPDARTS_KIOSK_URL:-http://localhost:8000}"
KIOSK_DIR="$HOME/.shepdarts"
WRAPPER="$KIOSK_DIR/kiosk.sh"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
die()  { printf '\n\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- packages
say "Checking packages (Chromium + cursor hider)"
CHROME=""
for candidate in chromium-browser chromium; do
  command -v "$candidate" >/dev/null 2>&1 && CHROME="$candidate" && break
done
NEED=()
[ -z "$CHROME" ] && NEED+=(chromium-browser)
command -v unclutter >/dev/null 2>&1 || NEED+=(unclutter)
if [ "${#NEED[@]}" -gt 0 ]; then
  info "installing: ${NEED[*]}"
  sudo apt-get update -qq
  sudo apt-get install -y "${NEED[@]}" || true
fi
for candidate in chromium-browser chromium; do
  command -v "$candidate" >/dev/null 2>&1 && CHROME="$candidate" && break
done
[ -n "$CHROME" ] || die "Chromium is not installed and could not be installed automatically."
info "using $CHROME"

# ------------------------------------------------------------ launch wrapper
# Waits for the local server to answer before opening Chromium, so the kiosk
# never lands on a connection-error page during the boot race.
say "Writing the kiosk launcher ($WRAPPER)"
mkdir -p "$KIOSK_DIR"
cat > "$WRAPPER" <<WRAP
#!/usr/bin/env bash
# ShepDarts kiosk launcher - written by installer/kiosk-pi.sh. Re-run that to
# change it rather than editing here.
set -u
URL="$URL"
CHROME="$CHROME"

# Hide the mouse pointer (X11 sessions; harmless elsewhere).
command -v unclutter >/dev/null 2>&1 && unclutter -idle 0 >/dev/null 2>&1 &

# Stop the screen blanking / the monitor sleeping (X11 only; Wayland is handled
# by the compositor autostart below).
if [ "\${XDG_SESSION_TYPE:-}" = "x11" ] && command -v xset >/dev/null 2>&1; then
  xset s off; xset -dpms; xset s noblank
fi

# Wait (up to ~60s) for the app to come up so Chromium opens on the game, not
# on an error page.
for _ in \$(seq 1 60); do
  curl -sf "\$URL" >/dev/null 2>&1 && break
  sleep 1
done

# A previous unclean shutdown otherwise shows a "restore pages?" bar that needs
# a mouse to dismiss. Clear the flags before each launch.
PREFS="\$HOME/.config/chromium/Default/Preferences"
if [ -f "\$PREFS" ]; then
  sed -i 's/"exit_type":"[^"]*"/"exit_type":"Normal"/; s/"exited_cleanly":false/"exited_cleanly":true/' "\$PREFS" 2>/dev/null || true
fi

# --password-store=basic is what stops the "Enter password to unlock your login
# keyring" dialog appearing over the game.
#
# Chromium asks gnome-keyring for a place to keep saved passwords the moment it
# starts. Desktop Autologin means nobody typed a login password, so PAM never
# unlocked the login keyring - and Chromium's request is what makes the keyring
# demand one. On a kiosk with no keyboard that dialog cannot be dismissed at
# all, and it sits on top of the app forever.
#
# "basic" keeps the passwords in Chromium's own profile instead of the keyring,
# so nothing is ever asked to unlock. That is a real (if small) reduction in how
# well saved passwords are protected at rest - which is the right trade here,
# because this profile exists to show one page on the local network and should
# never have a password saved in it in the first place. The system keyring is
# left alone for every other program on the Pi.
exec "\$CHROME" \\
  --kiosk "\$URL" \\
  --password-store=basic \\
  --noerrdialogs --disable-infobars --disable-session-crashed-bubble \\
  --disable-features=Translate --disable-pinch --overscroll-history-navigation=0 \\
  --check-for-update-interval=31536000 \\
  --autoplay-policy=no-user-gesture-required \\
  --ozone-platform-hint=auto
WRAP
chmod +x "$WRAPPER"

# --------------------------------------------------- autostart registration
# The desktop compositor differs across Pi OS images (wayfire on Pi 4-era
# Bookworm, labwc on newer/Pi 5, LXDE on older). Writing all three autostart
# hooks is harmless where a given one is unused, and means this works without
# having to detect which session is active.
say "Registering autostart"

# wayfire
WF="$HOME/.config/wayfire.ini"
if [ -f "$WF" ] || command -v wayfire >/dev/null 2>&1; then
  mkdir -p "$(dirname "$WF")"; touch "$WF"
  grep -q '^\[autostart\]' "$WF" || printf '\n[autostart]\n' >> "$WF"
  grep -q '^shepdarts ' "$WF"  || sed -i "/^\[autostart\]/a shepdarts = $WRAPPER" "$WF"
  grep -q '^screensaver ' "$WF" || sed -i "/^\[autostart\]/a screensaver = false" "$WF"
  grep -q '^dpms ' "$WF"        || sed -i "/^\[autostart\]/a dpms = false" "$WF"
  info "wayfire:  $WF"
fi

# labwc
LABWC="$HOME/.config/labwc/autostart"
mkdir -p "$(dirname "$LABWC")"; touch "$LABWC"
grep -q 'shepdarts' "$LABWC" || echo "$WRAPPER &" >> "$LABWC"
info "labwc:    $LABWC"

# XDG autostart (LXDE and anything honouring the spec)
XDG="$HOME/.config/autostart/shepdarts-kiosk.desktop"
mkdir -p "$(dirname "$XDG")"
cat > "$XDG" <<DESKTOP
[Desktop Entry]
Type=Application
Name=ShepDarts Kiosk
Comment=Open ShepDarts full-screen on the attached monitor
Exec=$WRAPPER
Terminal=false
X-GNOME-Autostart-enabled=true
DESKTOP
info "xdg:      $XDG"

say "Kiosk configured"
info "url:       $URL"
info "keyring:   bypassed - Chromium will not ask for a keyring password"
info "try it now: $WRAPPER    (run from the desktop, or just reboot)"
info ""
info "Last step, needs a keyboard once so boot needs none afterwards:"
info "  sudo raspi-config -> System Options -> Boot / Auto Login -> Desktop Autologin"
info "  sudo reboot"
info ""
info "To undo: delete $XDG, remove the 'shepdarts' line from $WF / $LABWC."

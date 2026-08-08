# Testing ShepDarts on a Raspberry Pi

The quickest way to get the current build onto a Pi and try the games
(including the new **Snakes & Ladders**). This runs the app straight from a git
checkout - it does **not** use the signed-release updater in
`installer/install-pi.sh`.

## Requirements

- Raspberry Pi OS Bookworm (or any recent Debian/Ubuntu) - ships Python 3.11.
- The Pi on the same network as the phone/laptop you'll open the UI from.
- No cameras or Node toolchain needed: dart detection is Autodarts' job, and the
  web UI is shipped prebuilt in `frontend/dist`.

## One-time install

On the Pi:

```bash
git clone -b pi-test https://github.com/darren-darts/autodarts-updates.git shepdarts
cd shepdarts
bash installer/install-pi-git.sh
```

> The `pi-test` branch of the updates repo holds this source build. The repo's
> `main` branch is the published-updates artifacts and is left untouched.

It installs `python3-venv`, builds a virtual environment, installs the backend
dependencies, and starts ShepDarts as a **user service** on port 8000. When it
finishes it prints the address, e.g.

```
open:  http://192.168.1.42:8000
```

Open that from any device on the same network.

## Updating to a newer build

```bash
cd shepdarts
git pull
bash installer/install-pi-git.sh
```

## Handy commands

```bash
systemctl --user status shepdarts     # is it running?
journalctl --user -u shepdarts -f     # live logs
systemctl --user restart shepdarts    # restart it
```

## Notes

- **Dart detection** comes from Autodarts (its Board Manager on `:3180`). Install
  that separately with `bash <(curl -sL get.autodarts.io)`. Without it you can
  still drive games from the phone remote and the on-screen override, which is
  enough to try out gameplay.
- `config/` (settings, player roster, calibration, TLS cert) lives only on the
  Pi and is never committed.
- To run it by hand instead of as a service:
  `bash run.sh` from the repo root.

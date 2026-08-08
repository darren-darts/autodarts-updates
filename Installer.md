# Building and publishing ShepDarts

A practical runbook, not a design document — see [DISTRIBUTION.md](DISTRIBUTION.md)
for *why* the update system works the way it does. This is just the commands,
in order, for the two things you'll actually do: build a new installer, and
publish an update.

**Your project's release target is already configured**, publishing to the
`darren-darts/autodarts-updates` GitHub repo (`release.toml`, git-ignored),
served at `https://darren-darts.github.io/autodarts-updates`. The "one-time
setup" section below has already been done on this machine — it's here for
the day you set up on a new PC, not something to repeat.

---

## Quick reference

Once everything is set up, these are the only two commands you'll use
regularly:

```powershell
# Ship a code change to everyone already installed (Windows AND Pi):
python tools/release.py 1.1.0 -m "What changed"

# Only when Windows needs a brand new Setup.exe (new machine, or a new
# pip dependency — see "When do I need a new Windows installer?" below):
python tools/build_installer.py 1.1.0
```

A Raspberry Pi never needs a new installer for a normal update — see the
[Raspberry Pi](#raspberry-pi) section.

---

## Windows

### 1. What you need installed

| Tool | Why | This PC |
|---|---|---|
| [Python 3.11](https://www.python.org/downloads/) (with the `py` launcher) | Runs the backend and every `tools/*.py` script | ✅ already installed |
| [Node.js LTS](https://nodejs.org/) | Builds the Vue frontend | ✅ already installed |
| [Git](https://git-scm.com/download/win) | Publishing pushes to GitHub | ✅ already installed |
| [Inno Setup 6 or 7](https://jrsoftware.org/isdl.php) | Builds a proper `Setup.exe` instead of a zip | ✅ already installed |
| A GitHub account with a [personal access token](https://github.com/settings/tokens) (classic, `repo` scope) | Git push over HTTPS no longer accepts a plain password | Needed the first time you push |

Inno Setup is optional — `tools/build_installer.py` falls back to a zip +
`Install.bat` if it isn't found — but the `.exe` is smaller, gives a Start
Menu entry and an uninstaller, and draws a milder SmartScreen warning.

### 2. One-time project setup — *already done, reference only*

This is what makes publishing possible at all. Skip this section unless
you're starting fresh on a new machine or rotating the signing key.

**a) The signing key** — proves an update genuinely came from you, no matter
who can write to the hosting repo:

```powershell
python tools/release.py --keygen
```

Writes the private key to `~/.autodarts/release_ed25519` and pastes the
public half into `backend/update/trusted_keys.py` automatically.
**Back the private key up somewhere offline** — if it's lost, no installed
copy can ever be sent another update; the only way back is a fresh install
for everyone.

> On this PC that file already exists at `~/.autodarts/release_ed25519`, and
> `trusted_keys.py` already has its public half committed. Running `--keygen`
> again will refuse, on purpose — it won't overwrite an existing key.

**b) The updates repo** — a small **public** repo holding only the built
app, never your source:

1. Create `autodarts-updates` at <https://github.com/new> under the
   `darren-darts` account, with a README so it has a `main` branch.
2. *Settings → Pages → Source: Deploy from a branch → `main` / `(root)`*.
3. `release.toml` (git-ignored, already present here) points at it.

**c) First publish**, which creates the repo's real content:

```powershell
python tools/release.py 0.1.0 --channel beta -m "First build"
```

Git prompts for GitHub credentials the first time — use the personal access
token as the password.

### 3. Build and publish the first installer

This is what a brand new family member downloads once. Two separate
outputs, both required:

- **The update channel** (`tools/release.py`) — a tiny signed pointer plus
  content-addressed blobs, pushed to the Pages repo. This is what every
  installed copy checks against forever after.
- **The Setup.exe** (`tools/build_installer.py`) — the actual download,
  built locally and uploaded by hand to GitHub *Releases* (a different
  feature of the same repo — git hard-limits committed files at 100MB, so
  the 50-80MB installer can't live in the Pages branch itself).

```powershell
# Build the bundled Python runtime - only needed once, or when
# backend/requirements.txt changes.
python tools/build_runtime.py

# Publish version 1.0.0 to the stable channel. Builds the frontend,
# hashes everything, uploads only new blobs, signs, pushes.
python tools/release.py 1.0.0 -m "First public release"

# Build ShepDarts-Setup-1.0.0.exe from the SAME payload rules as the
# release above, so what someone installs and what they'd get from an
# update of the same version are byte-for-byte identical.
python tools/build_installer.py 1.0.0
```

The `.exe` lands in `dist-release\ShepDarts-Setup-1.0.0.exe`.

**Upload it to GitHub Releases:**

1. Go to <https://github.com/darren-darts/autodarts-updates/releases>.
2. *Draft a new release* → tag `v1.0.0` → attach the `.exe` from
   `dist-release\` → *Publish release*.

The link you hand out never changes:

```
https://github.com/darren-darts/autodarts-updates/releases/latest
```

**What the person receiving it sees:** Windows SmartScreen warns because
the installer is unsigned — tell them to expect *More info → Run anyway*,
or it reads as a virus alert. A code-signing certificate (~£200/year) would
remove it; not worth it at family scale. Nothing else is required of them —
no Python, no Node, the runtime is bundled.

### 4. Publish an update

The common case. No new installer, no Inno Setup, nothing for anyone to
download — the app fetches this itself from the Updates page.

```powershell
python tools/release.py 1.1.0 -m "Fixed takeout detection near the bull"
```

That's it. Installed copies see it within a minute (GitHub Pages caches the
channel pointer for up to 10 minutes in the worst case; the app works
around most of that with a cache-busting check).

**To test on your own machine before your family gets it**, publish to
`beta` first, switch your own install to the beta channel on its Updates
page, confirm it, then ship the *exact same build* to everyone:

```powershell
python tools/release.py 1.1.0 --channel beta -m "Testing the takeout fix"
# ... test it yourself on the beta channel ...
python tools/release.py --promote 1.1.0 --to stable
```

`--promote` re-points the channel at a version already uploaded — nothing
is rebuilt, so what ships is byte-for-byte what you tested.

**Always safe to run first:**

```powershell
python tools/release.py 1.1.0 --dry-run -m "..."
```

Shows exactly what would upload without touching anything.

### When do I need a new Windows installer?

Only two situations — an ordinary code/UI/game change is never one of them:

- **A brand new PC**, which has never installed ShepDarts before.
- **A new entry in `backend/requirements.txt`.** Windows's bundled Python
  has `pip` stripped out after it's built (saves ~25MB), so it cannot gain
  a dependency after the fact. Publish with `--min-runtime` so old installs
  are told to fetch the new installer instead of applying an update that
  would fail to start:

  ```powershell
  python tools/build_runtime.py
  python tools/release.py 1.2.0 --min-runtime 1.1.0 -m "..."
  python tools/build_installer.py 1.2.0
  # ...then upload the new .exe to Releases, as in step 3.
  ```

A Raspberry Pi never needs this second case — see below.

---

## Raspberry Pi

**There is no separate Pi "build" step, and no artefact to upload.** A Pi
installs and updates from the *exact same* release you just published with
`tools/release.py` — the one command above ships both platforms at once.
What differs is only how each platform *fetches* it:

| | Windows | Raspberry Pi |
|---|---|---|
| Runtime | A Python interpreter bundled into the installer, frozen at build time | A venv built from the Pi's own `python3`, at install time |
| Gets the app | Downloads a Setup.exe you built and uploaded | Runs a script that pulls the same release the updater uses |
| A new pip dependency | Needs a new installer (see above) | Installs it automatically — pip is right there in the venv |

### First install on a Pi

Nothing to build beforehand — this is the entire process, run **on the Pi**
itself (SSH in, or a keyboard/monitor on it directly):

```bash
AUTODARTS_BASE_URL=https://darren-darts.github.io/autodarts-updates \
  bash <(curl -fsSL https://darren-darts.github.io/autodarts-updates/installers/install-pi.sh)
```

This installs the apt packages OpenCV needs, builds the venv, pulls
whatever is currently on the `stable` channel through the same signed
manifest the in-app updater uses, and installs a systemd service that
starts ShepDarts at boot (`~/.config/systemd/user/autodarts.service`).

To install from `beta` instead of `stable`:

```bash
AUTODARTS_BASE_URL=https://darren-darts.github.io/autodarts-updates \
AUTODARTS_CHANNEL=beta \
  bash <(curl -fsSL https://darren-darts.github.io/autodarts-updates/installers/install-pi.sh)
```

Safe to re-run any time — it replaces `app/` and `runtime/`, never
`config/` (calibration, players, selfies).

### Updating a Pi

**Normally: nothing to run at all.** Once installed, a Pi behaves exactly
like a Windows install from the Updates page — `python tools/release.py
1.1.0 -m "..."` on your PC is the only command, and the Pi picks it up
itself (auto-checks on start, or press *Check for updates*).

This includes a new pip dependency, which Windows cannot do without a new
installer — a Pi's launcher notices `requirements.txt` changed and runs
`pip install` into its own venv automatically, right after staging the new
code and before starting it. If that install fails (no network, no wheel
for the Pi's CPU), the whole update rolls back and the previous version
keeps running — same safety net as a normal update.

**Only re-run the install script by hand if:**

- The Pi needs a new **apt package** (a system-level dependency, which pip
  can never install) — check `install-pi.sh` and re-run it.
- `launcher.py` itself needs updating — it lives outside `app/` by design,
  so an in-app update can never replace it:

  ```bash
  curl -fsSL https://darren-darts.github.io/autodarts-updates/installers/launcher.py \
    -o ~/autodarts/launcher.py
  systemctl --user restart autodarts
  ```

- Something is badly broken and you want a clean reinstall — re-running
  `install-pi.sh` is safe and non-destructive (see above).

### Checking a Pi

```bash
systemctl --user status autodarts     # is it running
journalctl --user -u autodarts -f     # live log
```

Or from any browser on the same network: `http://<pi-ip>:8000`.

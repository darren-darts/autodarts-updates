# Distributing Autodarts, and shipping updates

Friends and family install once from a Setup file. Everything after that is
an in-app update: they open **Updates**, press **Download**, then **Install
and restart**. A typical update is a few kilobytes.

---

## How small is an update?

Measured on this project, not estimated:

| What changed | Actually transferred |
|---|---|
| One backend file (a bug fix) | **~1–4 KB** |
| A whole live update, verified end to end | **3,211 bytes** |
| Every Python file at once | ~69 KB |
| Anything in the Vue frontend | ~113 KB |
| Backend + frontend together | ~180 KB |
| Adding/changing an arena image | + up to 3.4 MB |
| First install (bundled Python, OpenCV, numpy) | **80 MB download**, 205 MB installed, once |

Two things make it this small:

1. **Files are stored by content hash.** A file that did not change has the
   same hash, so the app copies it from the copy it already has instead of
   downloading it. Only genuinely changed files cross the network.
2. **Everything is gzipped.** Python source compresses about 3.5:1.

The frontend figure is a floor, not a variable: Vite bundles the whole SPA
into one JS and one CSS file, so *any* frontend change rewrites both. A
one-line CSS tweak costs the same ~113 KB as a rewritten screen. Not worth
optimising - it is one second on any connection - but worth knowing.

The first install is almost entirely OpenCV (113 MB) and numpy (58 MB),
compressing to an 80 MB download. It is a one-time cost and the updater
never re-downloads it — updates only ever replace `app/`, never `runtime/`.

---

## Where updates are hosted

**Any static HTTPS host works.** The app only ever performs plain GETs
against a base URL with a fixed path layout, so the host is a `release.toml`
setting, not a design decision. Only the *publisher* differs, and
`release.py` has one backend per target.

This matters more than it sounds, because **signing means the host is not
trusted**. A release is accepted on the strength of an Ed25519 signature made
by a key that exists only on your machine — so a host being free, public, or
even compromised cannot result in code running on anyone's PC. Choose a host
on cost and convenience; it is not a security decision.

### Currently configured: GitHub Pages

| | |
|---|---|
| Updates repo | `darren-darts/autodarts-updates` (public, artefacts only) |
| Update URL | `https://darren-darts.github.io/autodarts-updates` |
| Installer | GitHub Releases (git hard-limits files at 100 MB) |
| Commit identity | `darrensheppard69@gmail.com`, set on that checkout only |
| Signing key | `~/.autodarts/release_ed25519` — **back this up** |
| Cost | Free. No card, no egress bill. |

> The commit identity is set per-repository on purpose. This machine's global
> git identity is a work account, and that address would otherwise be stamped
> into every commit of a public personal repo — not fixable afterwards
> without rewriting history.

Publishing is then just:

```bash
python tools/release.py 1.1.0 -m "What changed"
```

which commits the changed blobs and pushes. **A push is atomic**, so unlike
object storage there is no window in which a release is half-published — the
blobs-then-manifest-then-pointer ordering still happens, but git makes it
moot. The history doubles as a release log, and `git revert` un-ships a bad
version.

Two real costs, both handled in code rather than left as gotchas:

- **Pages sends `Cache-Control: max-age=600`.** A new release could take ten
  minutes to become visible, and a CDN ignores a request's `no-cache` header
  for publicly cacheable responses. The client appends a unique query string
  when fetching the channel pointer — part of the cache key, so it always
  gets a fresh copy. Blobs and manifests are immutable, so they stay cached.
- **Jekyll.** Pages runs uploaded files through Jekyll by default, which
  silently drops any path beginning with `_`. The publisher writes a
  `.nojekyll` marker so the tree is served verbatim.

The client also sniffs the gzip magic number rather than trusting the
`Content-Type`, so a host that expands `.gz` itself (some CDNs do) works
identically. That is what makes "any static host" true rather than aspirational.

### First-time setup

1. Create a **public** repo at
   <https://github.com/new> named `autodarts-updates`. Tick *Add a README* so
   it has a `main` branch to clone. It holds only published artefacts — the
   built app you are already handing out. Your source stays private; Pages
   needs a public repo on the free tier, but there is no reason that has to
   mean publishing the source.
2. Enable Pages: *Settings → Pages → Source: Deploy from a branch → `main` /
   `(root)` → Save*.
3. Publish:

```powershell
cd E:\WORK\Areas\Personal\Darts-Darren\claude-plan
.\backend\.venv\Scripts\python.exe tools\release.py 0.1.0 --channel beta -m "First build"
```

   Git will prompt for GitHub credentials the first time. Use a
   [personal access token](https://github.com/settings/tokens) as the
   password (a classic token with `repo` scope is fine) — GitHub stopped
   accepting account passwords over HTTPS.

4. Wait a minute for the first Pages build, then confirm it is live:

   ```powershell
   curl.exe https://darren-darts.github.io/autodarts-updates/channels/beta.json
   ```

   That should print JSON. A 404 usually means Pages has not finished its
   first build, or the branch/folder in step 2 is not `main` / `(root)`.

After that, a push is live in well under a minute.

### If the push is refused with a 403

```
remote: Permission to darren-darts/autodarts-updates.git denied to <someone-else>.
```

Git authenticated as the wrong GitHub account. On Windows this is almost
always the Credential Manager handing back a token it cached for a different
login — nothing to do with the repo or its permissions.

The publisher puts the login in the remote URL
(`https://darren-darts@github.com/...`) so the credential lookup is keyed to
the right account. If a stale entry still wins, clear it:

```powershell
cmdkey /delete:LegacyGeneric:target=git:https://github.com
cmdkey /delete:git:https://github.com
```

Or, more precisely, via the GUI: *Control Panel → Credential Manager →
Windows Credentials → remove any `git:https://github.com` entry*.

Then re-run the publish. **Nothing is lost when a push fails** — the release
is already committed to the local checkout, and the retry pushes it. That
recovery is deliberate: an earlier version decided whether to push based on
whether there was anything new to *commit*, so a failed push left a commit
that no retry would ever send, and the release sat on disk looking published.
It now asks whether the branch is ahead of the remote instead.

### Switching back to S3

Set `target = "s3"` in `release.toml` and fill in the `[s3]` section — the
previous bucket config is kept there, commented out. Nothing else changes,
including already-installed copies if the `base_url` stays the same.

---

## One-time setup

### 1. Create your signing key

```bash
python tools/release.py --keygen
```

Writes the private key to `~/.autodarts/release_ed25519` and pastes the
public half into `backend/update/trusted_keys.py`.

**Back the private key up somewhere offline.** Every installed copy trusts
its public half; if you lose it, nobody can be sent another update without
reinstalling by hand. It is deliberately stored outside the repository so
`git add -A` cannot publish it.

This is what stops anyone who ever gains write access to your bucket from
pushing code to your family's PCs. HTTPS proves the bytes came from your
bucket; the signature proves *you* put them there.

### 2. Configure the bucket

```bash
cp release.example.toml release.toml     # git-ignored
```

Fill in `bucket`, `region`, `prefix` and `base_url`.

### 3. Make just the update prefix publicly readable

The app fetches with plain HTTPS GETs and no credentials — shipping an AWS
key inside an app you hand out means the key is extractable from every copy.
Keep the bucket private for writes and open only the update prefix for
reads:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "PublicReadUpdates",
    "Effect": "Allow",
    "Principal": "*",
    "Action": "s3:GetObject",
    "Resource": "arn:aws:s3:::YOUR-BUCKET/updates/*"
  }]
}
```

Turn off "Block all public access" for this bucket (or at least the ACL-free
bucket-policy portion). Nothing secret goes under `updates/` — it is the app
you are giving away.

Optional: put CloudFront in front and set `base_url` to the distribution.
Cheaper egress, faster for anyone far from the region. Nothing else changes.

### 4. Build the runtime and installer

```bash
python tools/build_runtime.py            # ~205 MB, only when deps change
python tools/build_installer.py 1.0.0
```

Produces `dist-release/Autodarts-Setup-1.0.0.exe` if
[Inno Setup](https://jrsoftware.org/isdl.php) is installed, otherwise a zip
with an `Install.bat`. Prefer the .exe: it is smaller (LZMA2 beats zip
deflate — 56 MB vs 77 MB measured on the same payload), gives a Start Menu
entry and an uninstaller, and draws a milder SmartScreen warning than a bare
batch file.

The compiler is located by searching, not by a hard-coded path — Inno Setup
puts its major version in the directory name and offers a per-user install
under `%LOCALAPPDATA%\Programs` that needs no administrator. Both 6 and 7
are found, newest first.

### Hosting the installer

Put it in **GitHub Releases**, not the Pages repo: git hard-limits files at
100 MB and a 56 MB binary in every clone would bloat that repository's
history permanently, for no benefit. Releases allows 2 GB per asset and
gives a stable link to share:

```
https://github.com/darren-darts/autodarts-updates/releases/latest
```

Repo → *Releases* → *Create a new release* → tag `v0.1.0` → attach the
Setup .exe → *Publish*.

### What the person receiving it sees

Windows will warn: the installer is unsigned, and SmartScreen flags any
unsigned installer downloaded from the internet. They need *More info → Run
anyway*. Tell them to expect it, or it reads as a virus alert. A code-signing
certificate (~£200/year) removes it and is not worth it at family scale.

Nothing else is required of them — no Python, no Node, no build tools. The
runtime is bundled.

---

## Publishing an update

Once you are happy with a change:

```bash
python tools/release.py 1.1.0 -m "Fixed takeout detection near the bull"
```

That builds the frontend, hashes the payload, uploads only blobs the bucket
does not already have, signs the manifest and moves the `stable` channel
pointer. Installed copies see it within a minute.

Try it on your own machine first:

```bash
python tools/release.py 1.1.0 --channel beta -m "..."   # publish to beta
# set your own app to the beta channel on the Updates page, test it
python tools/release.py --promote 1.1.0 --to stable     # ship it to everyone
```

`--promote` moves the pointer to a version already in the bucket. Nothing is
rebuilt or re-uploaded, so what the family receives is byte-for-byte what you
tested.

`--dry-run` shows exactly what would upload, and leaves your working tree
untouched.

### Ordering, and why an interrupted upload is safe

Blobs upload first, then the manifest, and the channel pointer **last**. The
pointer is the only thing a client reads to decide what exists, so until it
moves the new release is invisible. A publish that dies halfway leaves a few
orphaned blobs and nothing else. Blobs are named by content hash and never
overwritten, so publishing can never disturb a release someone is already
running.

### When a release needs a new pip dependency

An in-app update replaces app code, not the bundled Python. If you add a
dependency to `requirements.txt`, rebuild the runtime, rebuild the installer,
and publish with a bumped runtime requirement:

```bash
python tools/release.py 1.2.0 --min-runtime 1.1.0 -m "..."
```

Installs with an older runtime will then *decline* the update and say to
download the new installer, rather than applying it and failing to start.

---

## Raspberry Pi

```bash
AUTODARTS_BASE_URL=https://YOUR-BUCKET/updates \
  bash <(curl -fsSL https://YOUR-BUCKET/updates/installers/install-pi.sh)
```

Installs the apt packages OpenCV needs, builds a venv from the Pi's own
python3, pulls the current release through the same signed manifest the
updater uses, and installs a systemd user service that starts at boot.

In-app updates work identically from then on.

---

## What it looks like on an installed machine

```
%LOCALAPPDATA%\Autodarts\          (Pi: ~/autodarts/)
├── .autodarts-root      marks this as installed, not a source checkout
├── launcher.py          applies updates, then starts the app
├── runtime/             bundled Python + OpenCV + numpy
├── app/                 REPLACED WHOLESALE by an update
│   ├── backend/  frontend/dist/  VERSION.json
├── config/              calibration, players, selfies, settings - PRESERVED
├── staging/             downloads in progress
└── logs/
```

`config/` sits **outside** `app/` on purpose. Applying an update swaps `app/`
for a freshly downloaded directory, so anything inside it is disposable by
definition. A family member losing a painstaking three-camera calibration to
a bug-fix update would be far worse than the bug, so the two are kept apart
at the directory level rather than by remembering a rule.

---

## Safety properties, and how each was verified

Both test suites are in the scratchpad for this session and were run against
the real code, not mocks.

| Property | How it is achieved | Verified |
|---|---|---|
| Only you can ship code | Ed25519 signature on manifest **and** channel pointer; public key compiled in | Forged pointer rejected; tampered blob rejected |
| No unverified fallback | `signing.verify` raises when no key is configured | Empty key list refuses rather than accepting |
| Corrupt download can't install | Every file hash-checked on arrival, whole tree re-verified before staging | Tampered blob discarded, nothing staged |
| No path escape | Manifest paths rejected at parse time; containment re-checked on write | 15 traversal/unsafe paths rejected |
| User data survives | `config/` outside the swapped directory | Calibration file intact after a real update |
| A bad update can't brick it | Previous version kept; a crash within 25s rolls it back | Rollback restores the old version and records why |
| Interrupted download resumes | Staged files verified by hash and skipped | Resume path exercised |
| Old versions stay installable | Content-addressed blobs are never overwritten | By construction |

### Two real bugs this testing found

Both were Windows-specific, both would have broken every update on a real
install, and neither was visible from reading the code:

1. **The server ran with its working directory inside `app/`.** Windows
   refuses to rename a directory that is any process's current directory, so
   every update failed with a bare `Access is denied`. The server now runs
   from the install root with `app/backend` on `PYTHONPATH`.

2. **Killing the launcher orphaned the server.** `TerminateProcess` runs no
   handlers, so an End Task left uvicorn running — holding the cameras, port
   8000, and `app/` itself, which made the *next* update fail the same way.
   The launcher now puts itself in a Job Object with `KILL_ON_JOB_CLOSE`, so
   Windows terminates the server whenever the launcher dies.

A third, subtler one: the first Job Object attempt silently did nothing
because ctypes defaults a return value to C `int`, truncating the 64-bit
handle. The calls all reported success while operating on a mangled handle.
Explicit `restype`/`argtypes` fixed it — and it only surfaced because the
orphan test checked for survivors rather than trusting the API's return code.

"""Publish a release to S3.

    python tools/release.py --keygen                     # once, ever
    python tools/release.py 1.1.0 --channel beta -m "..."  # publish to beta
    python tools/release.py --promote 1.1.0 --to stable    # bless it for everyone
    python tools/release.py 1.1.0 --dry-run                # show what would upload

Two ordering rules make a release safe to run from a domestic connection
that might drop halfway through:

1. **Blobs first, manifest second, channel pointer last.** The pointer is
   the only thing any client reads to decide what to install, so until it
   is flipped the new release simply does not exist. An interrupted upload
   leaves orphaned blobs - a few kilobytes of waste - and nothing else.
2. **Blobs are never overwritten.** They are named by content hash, so an
   upload either creates a new object or is redundant. Nothing an old
   manifest points at can be disturbed by publishing a new version, which
   is what keeps previous versions installable.

`--promote` re-points a channel at a version that has already been uploaded
and verified, so the beta-to-stable step transfers nothing and cannot
introduce a difference between what was tested and what ships.
"""
from __future__ import annotations

import argparse
import fnmatch
import gzip
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from update import manifest as manifest_mod  # noqa: E402
from update import signing  # noqa: E402
from update.version import Version  # noqa: E402

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 and earlier
    import tomli as tomllib  # type: ignore

CONFIG_PATH = ROOT / "release.toml"
KEY_PATH = Path(
    os.environ.get("AUTODARTS_RELEASE_KEY", Path.home() / ".autodarts" / "release_ed25519")
)
TRUSTED_KEYS_PATH = ROOT / "backend" / "update" / "trusted_keys.py"
ORIGIN_PATH = ROOT / "backend" / "update" / "origin.py"
VERSION_PATH = ROOT / "VERSION.json"


def fail(message: str) -> "NoReturn":  # type: ignore[valid-type]
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def human(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


def load_config() -> dict:
    if not CONFIG_PATH.is_file():
        fail(
            f"{CONFIG_PATH.name} not found. Copy release.example.toml to "
            f"release.toml and fill in your bucket details."
        )
    with open(CONFIG_PATH, "rb") as handle:
        return tomllib.load(handle)


# ----------------------------------------------------------------- keys


def keygen() -> None:
    if KEY_PATH.exists():
        fail(
            f"A signing key already exists at {KEY_PATH}. Delete it only if you "
            f"are certain - every installed copy trusts its public half, and "
            f"losing it means nobody can be sent another update without "
            f"reinstalling."
        )
    private, public = signing.generate_keypair()
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    KEY_PATH.write_text(private + "\n", encoding="utf-8")
    try:
        os.chmod(KEY_PATH, 0o600)
    except OSError:
        pass  # Windows; NTFS user-profile permissions already restrict it

    print(f"Private key written to {KEY_PATH}")
    print("   Back this up somewhere safe and offline. It is not recoverable.\n")
    print("Public key (paste into backend/update/trusted_keys.py):\n")
    print(f'    "{public}",\n')

    current = TRUSTED_KEYS_PATH.read_text(encoding="utf-8")
    if "REPLACE_WITH_PUBLIC_KEY" in current:
        TRUSTED_KEYS_PATH.write_text(
            current.replace(
                '"REPLACE_WITH_PUBLIC_KEY_FROM_release.py_--keygen"', f'"{public}"'
            ),
            encoding="utf-8",
        )
        print(f"Inserted it into {TRUSTED_KEYS_PATH.relative_to(ROOT)} for you.")
    else:
        print("trusted_keys.py already has a key - add the new one alongside it")
        print("   and keep both until every install has updated.")


def load_private_key() -> str:
    if not KEY_PATH.is_file():
        fail(f"No signing key at {KEY_PATH}. Run: python tools/release.py --keygen")
    return KEY_PATH.read_text(encoding="utf-8").strip()


# -------------------------------------------------------------- payload


def collect_files(config: dict) -> list[tuple[str, Path]]:
    payload = config.get("payload", {})
    includes = payload.get("include", [])
    excludes = payload.get("exclude", [])

    selected: dict[str, Path] = {}
    for pattern in includes:
        for path in sorted(ROOT.glob(pattern)):
            if not path.is_file():
                continue
            relative = path.relative_to(ROOT).as_posix()
            if any(fnmatch.fnmatch(relative, rule) for rule in excludes):
                continue
            # Validate now, not on the user's machine. A path the client
            # would reject is a broken release, and it should fail here
            # where it can still be fixed.
            manifest_mod.safe_relpath(relative)
            selected[relative] = path

    if not selected:
        fail("The payload include patterns matched no files.")
    return sorted(selected.items())


def build_frontend() -> None:
    print("Building the frontend...")
    npm = "npm.cmd" if os.name == "nt" else "npm"
    result = subprocess.run([npm, "run", "build"], cwd=str(ROOT / "frontend"))
    if result.returncode != 0:
        fail("Frontend build failed - nothing was published.")


def write_version_file(version: str, channel: str) -> None:
    VERSION_PATH.write_text(
        json.dumps({"version": version, "channel": channel}, indent=2) + "\n",
        encoding="utf-8",
    )


def write_origin(base_url: str) -> None:
    """Compile the update origin into the build being published."""
    text = ORIGIN_PATH.read_text(encoding="utf-8")
    lines = []
    for line in text.splitlines():
        if line.startswith("UPDATE_BASE_URL"):
            lines.append(f'UPDATE_BASE_URL = "{base_url}"')
        else:
            lines.append(line)
    ORIGIN_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ s3


class GitHubPages:
    """Publish by committing the blob tree to a repo GitHub Pages serves.

    The layout is byte-identical to the S3 one - the client cannot tell the
    difference, because it only ever performs HTTPS GETs against a base URL.

    Two ways this is genuinely better than object storage here:

    * **A push is atomic.** With S3 the ordering (blobs, manifest, pointer
      last) exists to make a half-finished upload harmless. Git makes the
      whole release land in one commit, so that window does not exist at all.
    * **Every release is auditable and revertible.** The history *is* the
      release log, and `git revert` un-ships a bad version.

    The costs, both real: Pages sends `Cache-Control: max-age=600`, so a new
    release takes up to ten minutes to become visible (the client works
    around this with a cache-busting query string on the channel pointer
    only), and the repo must be public on the free tier - which is why this
    is a *separate* repo holding only published artefacts, not the source.
    """

    def __init__(self, config: dict, dry_run: bool):
        github = config.get("github", {})
        self.repo = github.get("repo") or fail("release.toml is missing github.repo")
        self.branch = github.get("branch") or "main"
        self.prefix = (github.get("prefix") or "").strip("/")
        self.checkout = Path(
            github.get("checkout") or (ROOT / "dist-release" / "updates-repo")
        ).expanduser()
        # Set per-repository, never globally. Without this the commits take
        # the machine's global git identity - which here is a work account -
        # and stamp a work email into every commit of a public personal repo.
        # Not recoverable after the fact without rewriting history.
        self.user_name = github.get("user_name")
        self.user_email = github.get("user_email")
        # The GitHub account that pushes. Defaults to the repo owner, which is
        # right for a personal repo; set explicitly for an organisation.
        self.login = github.get("login") or self.repo.partition("/")[0]
        self.dry_run = dry_run
        self.uploaded = 0
        self.skipped = 0
        self.bytes_sent = 0
        self._ready = False

    @property
    def remote_url(self) -> str:
        """Remote with the login embedded, so the right account is used.

        Without the `user@` part, git asks the credential helper for
        "github.com" and gets back whatever account was cached first - on a
        machine that has ever pushed to GitHub as someone else, that is the
        wrong identity, and the push fails with a 403 naming an account you
        did not choose. Including the login makes it part of the credential
        lookup key, so the helper either finds the right token or prompts for
        it. No secret is stored in the URL - only the username.
        """
        return f"https://{self.login}@github.com/{self.repo}.git"

    # ---- git plumbing

    def git(self, *args: str, check: bool = True, quiet: bool = False):
        result = subprocess.run(
            ["git", *args],
            cwd=str(self.checkout) if self.checkout.exists() else None,
            capture_output=True,
            text=True,
        )
        if check and result.returncode != 0:
            fail(f"git {' '.join(args)} failed:\n{result.stderr.strip()}")
        if not quiet and result.stdout.strip():
            print(f"  {result.stdout.strip().splitlines()[-1]}")
        return result

    def prepare(self) -> None:
        """Clone the updates repo, or refresh an existing checkout."""
        if self._ready:
            return
        self._ready = True

        url = self.remote_url
        if not (self.checkout / ".git").is_dir():
            if self.dry_run:
                print(f"  would clone {url} into {self.checkout}")
                self.checkout.mkdir(parents=True, exist_ok=True)
                return
            self.checkout.parent.mkdir(parents=True, exist_ok=True)
            print(f"  cloning {url}")
            result = subprocess.run(
                ["git", "clone", "--branch", self.branch, url, str(self.checkout)],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                # An empty repo has no branch to clone yet, which is the
                # normal state the very first time this runs.
                print("  (empty repository - initialising it)")
                self.checkout.mkdir(parents=True, exist_ok=True)
                self.git("init", quiet=True)
                self.git("remote", "add", "origin", url, check=False, quiet=True)
                self.git("checkout", "-B", self.branch, quiet=True)
        else:
            # Keep an existing checkout's remote in step with the config, so
            # changing the login does not require deleting the directory.
            self.git("remote", "set-url", "origin", url, check=False, quiet=True)
            self.git("fetch", "origin", check=False, quiet=True)
            self.git("checkout", self.branch, check=False, quiet=True)
            self.git("pull", "--ff-only", "origin", self.branch, check=False, quiet=True)

        # Without .nojekyll, Pages runs the files through Jekyll, which
        # silently ignores any path segment starting with an underscore and
        # can rewrite others. The blob tree uses hashed names, so this has
        # not bitten yet - but it would, eventually, on exactly one release,
        # and be baffling. One empty file removes the whole class of problem.
        marker = self.checkout / ".nojekyll"
        if not marker.exists() and not self.dry_run:
            marker.write_text("", encoding="utf-8")

        if not self.dry_run:
            if self.user_name:
                self.git("config", "user.name", self.user_name, quiet=True)
            if self.user_email:
                self.git("config", "user.email", self.user_email, quiet=True)

    def key(self, path: str) -> str:
        return f"{self.prefix}/{path.lstrip('/')}" if self.prefix else path.lstrip("/")

    def local(self, path: str) -> Path:
        return self.checkout / self.key(path)

    def exists(self, path: str) -> bool:
        self.prepare()
        return self.local(path).is_file()

    def get(self, path: str) -> bytes:
        self.prepare()
        return self.local(path).read_bytes()

    def put(self, path: str, data: bytes, content_type: str, cache: str) -> None:
        # content_type/cache are ignored: Pages derives both itself and gives
        # no way to override them. Accepted rather than worked around - the
        # client sniffs gzip rather than trusting Content-Type, and busts the
        # cache on the one object where staleness matters.
        self.prepare()
        self.bytes_sent += len(data)
        if self.dry_run:
            print(f"  would write {self.key(path)} ({human(len(data))})")
            return
        target = self.local(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def finish(self, version: str, channel: str) -> None:
        if self.dry_run:
            print(f"\n  would commit and push to {self.repo} ({self.branch})")
            return
        self.prepare()
        self.git("add", "-A", quiet=True)

        if self.git("status", "--porcelain", quiet=True).stdout.strip():
            self.git("commit", "-m", f"Release {version} ({channel})", quiet=True)
        else:
            print("  nothing new to commit")

        # Deciding whether to push on "was there anything to commit" is wrong,
        # and was: a failed push (bad credentials, no network) leaves the
        # commit made but unpushed, so the *retry* found a clean tree, decided
        # there was nothing to do, and silently never pushed. The release then
        # sits on the local disk forever looking published. What matters is
        # whether the branch is ahead of the remote, so that is what is asked.
        ahead = self.git(
            "rev-list", "--count", f"origin/{self.branch}..HEAD", check=False, quiet=True
        )
        unpushed = ahead.stdout.strip() if ahead.returncode == 0 else "1"
        if unpushed in ("", "0"):
            print("  already published - nothing to push")
            return

        print(f"  pushing {unpushed} commit(s) to {self.repo}...")
        result = self.git("push", "-u", "origin", self.branch, check=False, quiet=True)
        if result.returncode != 0:
            self._explain_push_failure(result.stderr.strip())

    def _explain_push_failure(self, stderr: str) -> None:
        owner = self.repo.partition("/")[0]
        hint = (
            f"Check that {self.repo} exists and that you can push to it."
        )
        # By far the most common cause on Windows: the Credential Manager has
        # a cached github.com login for a *different* account, so git silently
        # authenticates as the wrong user and the server refuses. The error
        # names the account it used, which is the whole diagnosis - so say so
        # rather than making someone work it out.
        if "denied to" in stderr or "403" in stderr:
            marker = "denied to "
            used = stderr.split(marker, 1)[1].split(".")[0].strip() if marker in stderr else ""
            hint = (
                f"Git authenticated as {used or 'a different account'}, but the repo "
                f"belongs to {owner}.\n"
                f"Windows has cached the wrong GitHub login. Clear it and retry:\n\n"
                f"    cmdkey /delete:LegacyGeneric:target=git:https://github.com\n"
                f"    cmdkey /delete:git:https://github.com\n\n"
                f"Then re-run this command; git will prompt, and you sign in as "
                f"{owner} using a personal access token as the password.\n"
                f"Nothing is lost - the release is already committed locally and "
                f"will simply be pushed."
            )
        fail(f"git push failed:\n{stderr}\n\n{hint}")


class Bucket:
    def __init__(self, config: dict, dry_run: bool):
        s3 = config.get("s3", {})
        self.name = s3.get("bucket") or fail("release.toml is missing s3.bucket")
        if "your-bucket-name" in self.name:
            fail("release.toml still has the example bucket name - fill in your own.")
        self.prefix = (s3.get("prefix") or "updates").strip("/")
        self.region = s3.get("region")
        self.profile = s3.get("profile") or None
        # Set for any S3-compatible service that is not AWS itself:
        #   Cloudflare R2  https://<account-id>.r2.cloudflarestorage.com
        #   Backblaze B2   https://s3.<region>.backblazeb2.com
        # Both speak the same API, so nothing else here changes.
        self.endpoint_url = s3.get("endpoint_url") or None
        # Some buckets grant public read per-object via ACL rather than with
        # a bucket policy. Set acl = "public-read" in release.toml for those.
        # Left unset on buckets with Object Ownership set to "bucket owner
        # enforced", where sending any ACL is rejected outright.
        self.acl = s3.get("acl") or None
        self.dry_run = dry_run
        self._client = None
        self.uploaded = 0
        self.skipped = 0
        self.bytes_sent = 0

    @property
    def client(self):
        if self._client is None:
            try:
                import boto3
            except ModuleNotFoundError:
                fail("boto3 is not installed. Run: pip install boto3")
            # A named profile keeps the secret in ~/.aws/credentials, outside
            # this repository. If that profile is not set up, fall back to
            # boto3's default chain rather than failing outright - so
            # AWS_ACCESS_KEY_ID in the environment, or an IAM role on a build
            # machine, both still work without editing release.toml.
            session = None
            if self.profile:
                try:
                    session = boto3.Session(profile_name=self.profile)
                    session.get_credentials()
                except Exception:
                    print(
                        f"  note: AWS profile {self.profile!r} not found - "
                        f"using the default credential chain instead"
                    )
                    session = None
            self._client = (session or boto3.Session()).client(
                "s3", region_name=self.region, endpoint_url=self.endpoint_url
            )

            if not (session or boto3.Session()).get_credentials():
                fail(
                    f"No AWS credentials found. Either create the {self.profile!r} "
                    f"profile in ~/.aws/credentials, or set AWS_ACCESS_KEY_ID and "
                    f"AWS_SECRET_ACCESS_KEY in the environment."
                )
        return self._client

    def key(self, path: str) -> str:
        return f"{self.prefix}/{path.lstrip('/')}"

    def exists(self, path: str) -> bool:
        if self.dry_run:
            return False
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.name, Key=self.key(path))
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "403"):
                return False
            raise

    def get(self, path: str) -> bytes:
        return self.client.get_object(Bucket=self.name, Key=self.key(path))["Body"].read()

    def put(self, path: str, data: bytes, content_type: str, cache: str) -> None:
        self.bytes_sent += len(data)
        if self.dry_run:
            print(f"  would upload {self.key(path)} ({human(len(data))})")
            return
        extra = {"ACL": self.acl} if self.acl else {}
        try:
            self.client.put_object(
                Bucket=self.name,
                Key=self.key(path),
                Body=data,
                ContentType=content_type,
                CacheControl=cache,
                **extra,
            )
        except Exception as exc:  # noqa: BLE001
            if self.acl and "AccessControlListNotSupported" in str(exc):
                fail(
                    "This bucket has ACLs disabled (Object Ownership is "
                    "'bucket owner enforced'), so acl = \"public-read\" cannot "
                    "work. Remove `acl` from release.toml and grant public read "
                    "with a bucket policy instead - see DISTRIBUTION.md."
                )
            raise


# Blobs are immutable, so they can be cached forever. The channel pointer is
# the opposite: it is the one object whose whole job is to change, and a
# cached copy is exactly how someone ends up unable to see a new release.
BLOB_CACHE = "public, max-age=31536000, immutable"
POINTER_CACHE = "public, max-age=60, must-revalidate"


def publish(args, config: dict) -> None:
    version = args.version
    try:
        parsed = Version.parse(version)
    except ValueError as exc:
        fail(str(exc))

    channel = args.channel
    base_url = configured_base_url(config)
    if not base_url and not args.dry_run:
        fail("release.toml is missing base_url - installed apps would not know where to look.")

    private = load_private_key()
    if not signing.is_configured():
        fail(
            "backend/update/trusted_keys.py still has the placeholder key, so "
            "published builds could not verify anything. Run --keygen first."
        )
    expected_public = signing.public_key_for(private)
    if expected_public not in signing.TRUSTED_PUBLIC_KEYS:
        fail(
            "The signing key does not match any key in trusted_keys.py. "
            "Publishing with it would produce an update that every installed "
            "copy refuses. Add its public half:\n\n    "
            f'"{expected_public}",'
        )

    if not args.skip_build:
        build_frontend()

    # VERSION.json and origin.py are stamped into the build, so they have to
    # be written before hashing or the manifest would describe files that
    # differ from what ships. A dry run still needs that to report accurate
    # hashes and sizes - but it must not leave the working tree modified, so
    # the originals are restored below.
    originals = {path: path.read_bytes() for path in (VERSION_PATH, ORIGIN_PATH) if path.is_file()}
    try:
        write_version_file(version, channel)
        if base_url:
            write_origin(base_url)
        _publish_files(args, config, version, channel, base_url, private)
    finally:
        if args.dry_run:
            for path, content in originals.items():
                path.write_bytes(content)


def resolve_target(config: dict) -> str:
    target = (config.get("target") or "").strip().lower()
    if target:
        return target
    return "github" if config.get("github") else "s3"


def configured_base_url(config: dict) -> str:
    """The public HTTPS base compiled into published builds.

    Resolved from the *active* target only. Scanning every section for a
    base_url looks more forgiving but is worse: a leftover `[s3]` section
    kept for reference would silently win over the GitHub target, stamping
    the wrong URL into every build - a failure that only shows up much later,
    on someone else's machine, as "no updates found".

    For GitHub Pages the URL is a fixed function of the repository name, so
    it is derived rather than asked for. One less thing to typo.
    """
    target = resolve_target(config)
    section = config.get(target, {})

    configured = (section.get("base_url") or "").strip().rstrip("/")
    if configured:
        return configured

    if target == "github" and section.get("repo"):
        owner, _, name = section["repo"].partition("/")
        prefix = (section.get("prefix") or "").strip("/")
        url = f"https://{owner}.github.io/{name}"
        return f"{url}/{prefix}" if prefix else url
    return ""


def make_backend(config: dict, dry_run: bool):
    """Pick the publishing target named by release.toml.

    Both backends expose the same three operations (exists / put / finish),
    which is all publishing needs. The *client* has no equivalent switch and
    never will - it only performs HTTPS GETs against a base URL, so where a
    release is hosted is purely a publishing concern.
    """
    target = resolve_target(config)
    if target == "github":
        return GitHubPages(config, dry_run)
    if target == "s3":
        return Bucket(config, dry_run)
    fail(f"unknown target {target!r} in release.toml - use \"github\" or \"s3\"")


def _publish_files(args, config, version, channel, base_url, private) -> None:
    files = collect_files(config)
    print(f"\nPayload: {len(files)} files")

    bucket = make_backend(config, args.dry_run)
    entries = []
    for relative, path in files:
        digest, size = manifest_mod.sha256_file(path)
        blob = gzip.compress(path.read_bytes(), 9)
        entries.append(manifest_mod.FileEntry(relative, digest, size, len(blob)))

        blob_path = manifest_mod.blob_key(digest)
        if bucket.exists(blob_path):
            bucket.skipped += 1
            continue
        bucket.put(blob_path, blob, "application/gzip", BLOB_CACHE)
        bucket.uploaded += 1

    requirements = ROOT / "backend" / "requirements.txt"
    requirements_hash = (
        manifest_mod.sha256_file(requirements)[0] if requirements.is_file() else ""
    )

    parsed_manifest = manifest_mod.Manifest.build(
        version=version,
        channel=channel,
        notes=args.message or "",
        files=entries,
        min_runtime=args.min_runtime,
        requirements_sha256=requirements_hash,
    )
    manifest_bytes = parsed_manifest.canonical()
    manifest_sha = manifest_mod.sha256_bytes(manifest_bytes)

    print(
        f"\n  new blobs:    {bucket.uploaded}"
        f"\n  already there: {bucket.skipped}"
        f"\n  transferred:   {human(bucket.bytes_sent)}"
        f"\n  payload size:  {human(parsed_manifest.total_size)} uncompressed"
    )

    # Manifest before pointer: a client must never be told about a version
    # whose manifest is not yet readable.
    bucket.put(
        f"releases/{version}/manifest.json", manifest_bytes, "application/json", POINTER_CACHE
    )
    bucket.put(
        f"releases/{version}/manifest.json.sig",
        signing.sign(manifest_bytes, private).encode("ascii"),
        "text/plain",
        POINTER_CACHE,
    )

    upload_installer_assets(bucket)
    point_channel_at(bucket, channel, version, manifest_sha, private)

    # Git needs one commit at the end; S3 has already written everything.
    if hasattr(bucket, "finish"):
        bucket.finish(version, channel)

    if args.dry_run:
        print("\nDry run - nothing was uploaded.")
    else:
        print(f"\nPublished {version} to the '{channel}' channel.")
        print(f"   Installed apps on '{channel}' will offer it within a minute.")
    if Version.parse(version).prerelease and channel == "stable":
        print("   Note: this is a pre-release version on the stable channel.")


def upload_installer_assets(bucket: "Bucket") -> None:
    """Keep launcher.py and install-pi.sh next to the releases.

    The Pi installer curls both from the bucket, and the launcher is the one
    file an in-app update cannot replace (it is outside app/, by design), so
    a copy has to be reachable for someone re-running the installer to pick
    up a newer one.
    """
    for source, key, content_type in (
        (ROOT / "installer" / "launcher.py", "installers/launcher.py", "text/x-python"),
        (ROOT / "installer" / "install-pi.sh", "installers/install-pi.sh", "text/x-shellscript"),
    ):
        if source.is_file():
            bucket.put(key, source.read_bytes(), content_type, POINTER_CACHE)


def point_channel_at(
    bucket: Bucket, channel: str, version: str, manifest_sha: str, private: str
) -> None:
    pointer = manifest_mod.channel_pointer(version, manifest_sha)
    pointer_bytes = manifest_mod.canonical_bytes(pointer)
    bucket.put(f"channels/{channel}.json", pointer_bytes, "application/json", POINTER_CACHE)
    bucket.put(
        f"channels/{channel}.json.sig",
        signing.sign(pointer_bytes, private).encode("ascii"),
        "text/plain",
        POINTER_CACHE,
    )


def promote(args, config: dict) -> None:
    """Re-point a channel at a version that is already published.

    Nothing is rebuilt or re-uploaded, so what stable gets is byte-for-byte
    what beta was tested on. The manifest is fetched back from the bucket
    and its signature re-checked first, because pointing a channel at a
    release that has been damaged in place would break every install on it.
    """
    version = args.promote
    bucket = make_backend(config, args.dry_run)
    private = load_private_key()

    try:
        body = bucket.get(f"releases/{version}/manifest.json")
        signature = bucket.get(f"releases/{version}/manifest.json.sig")
    except Exception as exc:  # noqa: BLE001
        fail(f"Could not read the published manifest for {version}: {exc}")

    try:
        signing.verify(body, signature.decode("ascii").strip())
    except signing.SignatureError as exc:
        fail(f"The published manifest for {version} failed signature checking: {exc}")

    parsed = manifest_mod.Manifest.from_dict(json.loads(body))
    if parsed.version != version:
        fail(f"Manifest at releases/{version}/ declares version {parsed.version}.")

    point_channel_at(bucket, args.to, version, manifest_mod.sha256_bytes(body), private)
    if hasattr(bucket, "finish"):
        bucket.finish(version, args.to)
    if args.dry_run:
        print("Dry run - the channel was not moved.")
    else:
        print(f"Channel '{args.to}' now points at {version}.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish an Autodarts release to S3.")
    parser.add_argument("version", nargs="?", help="version to publish, e.g. 1.2.0")
    parser.add_argument("--channel", default="stable", choices=("stable", "beta"))
    parser.add_argument("-m", "--message", default="", help="release notes shown in the app")
    parser.add_argument("--min-runtime", default="0.0.0",
                        help="bump when a release needs a newer installer/runtime")
    parser.add_argument("--skip-build", action="store_true", help="reuse the existing frontend build")
    parser.add_argument("--dry-run", action="store_true", help="show what would happen")
    parser.add_argument("--keygen", action="store_true", help="create the release signing key")
    parser.add_argument("--promote", metavar="VERSION", help="move a published version to another channel")
    parser.add_argument("--to", default="stable", choices=("stable", "beta"))
    args = parser.parse_args()

    if args.keygen:
        keygen()
        return

    config = load_config()

    if args.promote:
        promote(args, config)
        return

    if not args.version:
        parser.error("a version is required, e.g.: python tools/release.py 1.2.0")
    publish(args, config)


if __name__ == "__main__":
    main()

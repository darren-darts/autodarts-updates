"""Ed25519 signing - the trust anchor for the whole update mechanism.

Why signatures at all, when downloads are already over HTTPS from a bucket
only one person can write to?

Because the failure this guards against is not eavesdropping, it is *anyone
else obtaining write access to the update path* - a leaked access key, a
bucket policy loosened by accident, a mistyped prefix that makes the tree
world-writable. Every installed copy of this app runs whatever code that
path serves, on a family member's PC. HTTPS proves you reached the right
bucket; it says nothing about who put the bytes there.

The private key lives on the release machine only and never enters the
repository or the bucket. The public key is compiled into the app. So a
release the key-holder did not sign will not install, no matter how it got
into the bucket.

Two deliberate properties:

* **Fail closed.** No trusted keys configured means no updates, not
  unverified updates. An update system that silently degrades to trusting
  anything is worse than none at all, because it is trusted.
* **A list of keys, not one.** Rotation needs an overlap window where both
  the old and new key verify, or every existing install is orphaned the
  moment the key changes.
"""
from __future__ import annotations

import base64

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .trusted_keys import TRUSTED_PUBLIC_KEYS


class SignatureError(Exception):
    """Raised when a payload is not signed by a trusted key."""


def _b64decode(value: str, expect_len: int, what: str) -> bytes:
    try:
        raw = base64.b64decode(value.strip(), validate=True)
    except Exception as exc:  # noqa: BLE001 - any decode failure is the same to us
        raise SignatureError(f"{what} is not valid base64") from exc
    if len(raw) != expect_len:
        raise SignatureError(f"{what} has wrong length: {len(raw)} != {expect_len}")
    return raw


def generate_keypair() -> tuple[str, str]:
    """(private_b64, public_b64). Used once, by `release.py --keygen`."""
    private = Ed25519PrivateKey.generate()
    private_raw = private.private_bytes_raw()
    public_raw = private.public_key().public_bytes_raw()
    return (
        base64.b64encode(private_raw).decode("ascii"),
        base64.b64encode(public_raw).decode("ascii"),
    )


def public_key_for(private_b64: str) -> str:
    raw = _b64decode(private_b64, 32, "private key")
    public_raw = Ed25519PrivateKey.from_private_bytes(raw).public_key().public_bytes_raw()
    return base64.b64encode(public_raw).decode("ascii")


def sign(payload: bytes, private_b64: str) -> str:
    raw = _b64decode(private_b64, 32, "private key")
    signature = Ed25519PrivateKey.from_private_bytes(raw).sign(payload)
    return base64.b64encode(signature).decode("ascii")


def verify(payload: bytes, signature_b64: str, trusted: list[str] | None = None) -> None:
    """Raise SignatureError unless `payload` was signed by a trusted key."""
    keys = TRUSTED_PUBLIC_KEYS if trusted is None else trusted
    keys = [k for k in keys if k and not k.startswith("REPLACE")]
    if not keys:
        raise SignatureError(
            "No trusted update signing key is configured, so updates cannot be "
            "verified. Run 'python tools/release.py --keygen' to create one."
        )

    signature = _b64decode(signature_b64, 64, "signature")
    for key_b64 in keys:
        try:
            public = Ed25519PublicKey.from_public_bytes(_b64decode(key_b64, 32, "public key"))
            public.verify(signature, payload)
            return
        except (InvalidSignature, SignatureError):
            continue
    raise SignatureError("signature does not match any trusted key")


def is_configured() -> bool:
    """Whether this build can verify updates at all - surfaced in the UI."""
    return any(k and not k.startswith("REPLACE") for k in TRUSTED_PUBLIC_KEYS)
